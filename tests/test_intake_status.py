"""Tests for GET /intake/requests/status.

The read-back a mail-side job uses to reflect what became of each captured
item onto the message it came from. Three things carry weight: the same
404-on-bad-token discipline as the write endpoint, a payload narrow enough
that a leaked token exposes message-ids and states but not the content of
the Owner's obligations, and a `disposition` the client can rely on
without encoding this app's state machine.
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import (
    Request as RequestModel,
    RequestClass,
    RequestSource,
    RequestStatus,
)
from app.routers.intake import DISPOSITIONS, MAX_STATUS_LIMIT
from app.security import generate_access_token
from tests.conftest import submit_request

TOKEN = "test-intake-token"
URL = "/intake/requests/status"


def auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make(db_session, *, key: str, status=RequestStatus.submitted, **kwargs) -> RequestModel:
    defaults = dict(
        public_number=f"REQ-{abs(hash(key)) % 9000 + 1000}",
        access_token=generate_access_token(),
        title="Reconcile PCard statement",
        description="Match receipts, code the lines, submit for approval.",
        request_class=RequestClass.long,
        status=status,
        source=RequestSource.email,
        dedupe_key=key,
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


def get(client, **params):
    return client.get(URL, params=params, headers=auth())


class TestTokenGate:
    def test_a_valid_token_is_accepted(self, client):
        assert get(client).status_code == 200

    @pytest.mark.parametrize(
        "headers",
        [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic test-intake-token"}],
        ids=["missing", "wrong", "wrong-scheme"],
    )
    def test_anything_else_is_404(self, client, db_session, headers):
        make(db_session, key="<a@uwo.ca>")
        resp = client.get(URL, headers=headers)
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not found."}

    def test_the_stored_digest_is_not_accepted_as_a_token(self, client):
        from app.config import get_settings

        resp = client.get(URL, headers=auth(get_settings().intake_token_hash))
        assert resp.status_code == 404

    def test_it_is_gated_on_the_same_setting_as_the_write_route(self):
        # One caller, one credential: the status route must appear and
        # disappear with the intake route, never on its own.
        import inspect

        from app.main import include_routers

        source = inspect.getsource(include_routers)
        assert source.count("intake_enabled") == 1
        assert source.count("include_router(intake.router)") == 1


class TestWhichRequestsAppear:
    def test_captured_requests_are_reported(self, client, db_session):
        make(db_session, key="<a@uwo.ca>")
        keys = [row["dedupe_key"] for row in get(client).json()["items"]]
        assert keys == ["<a@uwo.ca>"]

    def test_a_form_submission_is_not_reported(self, client, db_session):
        # No dedupe_key, so no message to reconcile against.
        submit_request(client)
        assert get(client).json()["items"] == []

    def test_a_request_with_no_key_is_not_reported(self, client, db_session):
        make(db_session, key="<kept@uwo.ca>")
        make(db_session, key=None, public_number="REQ-9001")
        keys = [row["dedupe_key"] for row in get(client).json()["items"]]
        assert keys == ["<kept@uwo.ca>"]

    @pytest.mark.parametrize("status", list(RequestStatus))
    def test_every_status_is_reported_not_just_open_ones(self, client, db_session, status):
        # The mail job files a message once it is closed, so closed rows
        # have to keep appearing rather than dropping out of the feed.
        make(db_session, key="<a@uwo.ca>", status=status)
        assert len(get(client).json()["items"]) == 1


class TestPayloadIsNarrow:
    EXPECTED = {"dedupe_key", "public_number", "status", "disposition", "updated_at"}

    def test_exactly_these_fields(self, client, db_session):
        make(db_session, key="<a@uwo.ca>")
        assert set(get(client).json()["items"][0]) == self.EXPECTED

    def test_no_title_or_description_anywhere_in_the_response(self, client, db_session):
        make(
            db_session,
            key="<a@uwo.ca>",
            title="Confidential obligation title",
            description="Confidential description.",
        )
        body = get(client).text
        assert "Confidential obligation title" not in body
        assert "Confidential description." not in body

    def test_timestamps_are_marked_utc(self, client, db_session):
        make(db_session, key="<a@uwo.ca>")
        assert get(client).json()["items"][0]["updated_at"].endswith("Z")


class TestDisposition:
    def test_the_mapping_is_exhaustive(self):
        # The guarantee the client depends on: adding a RequestStatus
        # without mapping it fails here rather than handing the mail job a
        # null disposition it has no branch for.
        assert set(DISPOSITIONS) == set(RequestStatus)
        assert set(DISPOSITIONS.values()) == {"awaiting_review", "active", "closed"}

    @pytest.mark.parametrize(
        "status,expected",
        [
            (RequestStatus.submitted, "awaiting_review"),
            (RequestStatus.queued, "active"),
            (RequestStatus.in_progress, "active"),
            (RequestStatus.needs_information, "active"),
            (RequestStatus.done, "closed"),
            (RequestStatus.declined, "closed"),
            (RequestStatus.withdrawn, "closed"),
            (RequestStatus.cancelled, "closed"),
        ],
    )
    def test_each_status_maps_as_documented(self, client, db_session, status, expected):
        make(db_session, key="<a@uwo.ca>", status=status)
        row = get(client).json()["items"][0]
        assert (row["status"], row["disposition"]) == (status.value, expected)

    def test_accepting_moves_it_from_awaiting_review_to_active(self, client, db_session):
        from app.state_machine import accept_and_queue

        req = make(db_session, key="<a@uwo.ca>")
        assert get(client).json()["items"][0]["disposition"] == "awaiting_review"

        accept_and_queue(db_session, req)
        db_session.commit()
        assert get(client).json()["items"][0]["disposition"] == "active"

    def test_cancelling_closes_it(self, client, db_session):
        from app.state_machine import cancel

        req = make(db_session, key="<a@uwo.ca>")
        cancel(db_session, req, "No transactions this month.")
        db_session.commit()
        assert get(client).json()["items"][0]["disposition"] == "closed"


class TestSince:
    def _two_rows(self, db_session):
        old = make(db_session, key="<old@uwo.ca>")
        new = make(db_session, key="<new@uwo.ca>")
        # Set explicitly rather than sleeping: updated_at resolves only to
        # the second, so two rows written together share a timestamp.
        old.updated_at = datetime(2026, 11, 1, 9, 0, 0)
        new.updated_at = datetime(2026, 11, 20, 9, 0, 0)
        db_session.commit()
        return old, new

    def test_it_filters_on_updated_at(self, client, db_session):
        self._two_rows(db_session)
        keys = [r["dedupe_key"] for r in get(client, since="2026-11-10T00:00:00").json()["items"]]
        assert keys == ["<new@uwo.ca>"]

    def test_it_is_inclusive_of_the_boundary_second(self, client, db_session):
        # updated_at has one-second resolution, so an exclusive filter would
        # silently drop rows updated in the same second as the caller's
        # high-water mark.
        _, new = self._two_rows(db_session)
        keys = [r["dedupe_key"] for r in get(client, since="2026-11-20T09:00:00").json()["items"]]
        assert keys == ["<new@uwo.ca>"]

    def test_an_aware_timestamp_is_converted_to_utc(self, client, db_session):
        self._two_rows(db_session)
        # 2026-11-20T04:00:00-05:00 is 09:00:00Z — the boundary, inclusive.
        keys = [
            r["dedupe_key"]
            for r in get(client, since="2026-11-20T04:00:00-05:00").json()["items"]
        ]
        assert keys == ["<new@uwo.ca>"]

    def test_a_later_aware_timestamp_excludes_it(self, client, db_session):
        self._two_rows(db_session)
        assert get(client, since="2026-11-20T05:00:00-05:00").json()["items"] == []

    def test_a_malformed_timestamp_is_rejected(self, client):
        assert get(client, since="last tuesday").status_code == 422

    def test_no_since_returns_everything(self, client, db_session):
        self._two_rows(db_session)
        assert get(client).json()["count"] == 2

    def test_results_are_ordered_oldest_first(self, client, db_session):
        self._two_rows(db_session)
        keys = [r["dedupe_key"] for r in get(client).json()["items"]]
        assert keys == ["<old@uwo.ca>", "<new@uwo.ca>"]


class TestPaging:
    def _many(self, db_session, count: int):
        base = datetime(2026, 11, 1, 9, 0, 0)
        rows = []
        for n in range(count):
            row = make(db_session, key=f"<{n:03d}@uwo.ca>", public_number=f"REQ-{n:04d}")
            rows.append(row)
        for row in rows:
            # All in the same second on purpose: that is the case a
            # timestamp-only cursor cannot page through.
            row.updated_at = base
        db_session.commit()
        return rows

    def test_a_page_reports_more_and_a_cursor(self, client, db_session):
        self._many(db_session, 5)
        body = get(client, limit=2).json()
        assert body["count"] == 2
        assert body["has_more"] is True
        assert body["next_cursor"]

    def test_the_last_page_has_no_cursor(self, client, db_session):
        self._many(db_session, 3)
        body = get(client, limit=10).json()
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    def test_paging_through_identical_timestamps_terminates(self, client, db_session):
        # The failure this guards against: every row sharing one second, so
        # a cursor carrying only a timestamp would return the same page
        # forever.
        self._many(db_session, 7)
        seen: list[str] = []
        cursor = ""
        for _ in range(10):
            body = get(client, limit=2, cursor=cursor).json()
            seen.extend(r["dedupe_key"] for r in body["items"])
            if not body["has_more"]:
                break
            cursor = body["next_cursor"]
        assert len(seen) == 7
        assert len(set(seen)) == 7

    def test_a_bad_cursor_is_rejected(self, client):
        assert get(client, cursor="not-a-cursor").status_code == 422

    def test_limit_is_bounded(self, client):
        assert get(client, limit=0).status_code == 422
        assert get(client, limit=MAX_STATUS_LIMIT + 1).status_code == 422
        assert get(client, limit=MAX_STATUS_LIMIT).status_code == 200


class TestUpdatedAtMoves:
    """The premise the `since` filter rests on.

    If updated_at did not move on a status change, incremental polling
    would silently never see it.
    """

    @pytest.mark.parametrize(
        "transition",
        ["accept", "decline", "cancel", "complete", "withdraw", "request_information"],
    )
    def test_every_transition_bumps_updated_at(self, db_session, transition):
        from app import state_machine as sm

        req = make(db_session, key=f"<{transition}@uwo.ca>")
        if transition in ("complete", "withdraw"):
            sm.accept_and_queue(db_session, req)
            db_session.commit()
        before = datetime(2020, 1, 1)
        req.updated_at = before
        db_session.commit()

        actions = {
            "accept": lambda: sm.accept_and_queue(db_session, req),
            "decline": lambda: sm.decline(db_session, req, "no"),
            "cancel": lambda: sm.cancel(db_session, req, "no"),
            "complete": lambda: sm.complete(db_session, req, "done"),
            "withdraw": lambda: sm.withdraw(db_session, req),
            "request_information": lambda: sm.request_information(db_session, req, "which one?"),
        }
        actions[transition]()
        db_session.commit()
        db_session.refresh(req)
        assert req.updated_at > before

    def test_a_change_is_visible_to_an_incremental_poll(self, client, db_session):
        from app.state_machine import accept_and_queue

        req = make(db_session, key="<a@uwo.ca>")
        req.updated_at = datetime(2026, 11, 1, 9, 0, 0)
        db_session.commit()

        watermark = datetime(2026, 11, 1, 9, 0, 1)
        assert get(client, since=watermark.isoformat()).json()["items"] == []

        accept_and_queue(db_session, req)
        db_session.commit()
        polled = get(client, since=watermark.isoformat()).json()["items"]
        assert [r["disposition"] for r in polled] == ["active"]


def test_the_write_endpoint_still_works_alongside_it(client, db_session):
    resp = client.post(
        "/intake/requests",
        json={
            "items": [
                {
                    "title": "Approve Kuali form",
                    "description": "Approve in Kuali.",
                    "request_class": "quick",
                    "source": "email",
                    "dedupe_key": "<round-trip@uwo.ca>",
                }
            ]
        },
        headers=auth(),
    )
    assert resp.json()["created"] == 1
    status = client.get(URL, headers=auth()).json()["items"]
    assert [(r["dedupe_key"], r["disposition"]) for r in status] == [
        ("<round-trip@uwo.ca>", "awaiting_review")
    ]
    assert db_session.scalar(select(RequestModel)).dedupe_key == "<round-trip@uwo.ca>"
