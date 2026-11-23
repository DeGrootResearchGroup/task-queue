"""Tests for the machine-facing intake endpoint.

Three properties carry the weight, because this runs unattended: a leaked
token must not be able to create anything the Owner has not reviewed, a
re-run must not duplicate, and one bad item must not sink the batch.

conftest sets INTAKE_TOKEN, so the route is registered for this module.
"""

import json

import pytest
from sqlalchemy import select

from app.models import Request as RequestModel, RequestSource, RequestStatus

TOKEN = "test-intake-token"
URL = "/intake/requests"


def auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def item(**overrides) -> dict:
    data = {
        "title": "Approve Kuali program form",
        "description": "Approve in Kuali; a student's form is held up until this clears.",
        "request_class": "quick",
        "source": "email",
    }
    data.update(overrides)
    return data


def post(client, items, token: str = TOKEN):
    return client.post(URL, json={"items": items}, headers=auth(token))


class TestTokenGate:
    def test_a_valid_token_is_accepted(self, client):
        resp = post(client, [item()])
        assert resp.status_code == 200
        assert resp.json()["created"] == 1

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer wrong-token"},
            {"Authorization": "Bearer "},
            {"Authorization": "Basic test-intake-token"},
            {"Authorization": "test-intake-token"},
        ],
        ids=["missing", "wrong", "empty", "wrong-scheme", "no-scheme"],
    )
    def test_anything_else_is_404(self, client, db_session, headers):
        resp = client.post(URL, json={"items": [item()]}, headers=headers)
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not found."}
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_a_wrong_token_looks_the_same_as_no_such_route(self, client):
        missing_route = client.post("/intake/nothing-here", json={}, headers=auth())
        wrong_token = client.post(URL, json={"items": []}, headers=auth("nope"))
        assert missing_route.status_code == wrong_token.status_code == 404


# Building an OpenAPI schema is the only reliable way to see whether a
# route was mounted (FastAPI wraps included routers in an opaque object),
# and doing so surfaces pre-existing duplicate-operation-id warnings from
# the GET+HEAD routes in public.py and calendar.py. They are unrelated to
# what these tests check.
@pytest.mark.filterwarnings("ignore:Duplicate Operation ID:UserWarning")
class TestRouteGating:
    def _probe(self, token: str):
        from fastapi import FastAPI

        from app.config import Settings
        from app.main import include_routers

        probe = FastAPI()
        include_routers(probe, Settings(intake_token=token))
        return probe.openapi()["paths"]

    def test_the_route_does_not_exist_without_a_token(self):
        assert URL not in self._probe("")

    def test_the_route_exists_with_a_token(self):
        assert URL in self._probe("some-token")


class TestWhatItMayCreate:
    def test_everything_lands_at_submitted_for_review(self, client, db_session):
        post(client, [item(request_class="quick"), item(title="Long one", request_class="long")])
        statuses = {r.status for r in db_session.scalars(select(RequestModel))}
        assert statuses == {RequestStatus.submitted}

    def test_it_cannot_mint_a_form_sourced_request(self, client, db_session):
        resp = post(client, [item(source="form")])
        assert resp.json()["rejected"] == 1
        assert db_session.scalars(select(RequestModel)).all() == []

    @pytest.mark.parametrize(
        "field,value",
        [
            ("status", "queued"),
            ("queue_position", 1),
            ("requester_email", "someone@example.com"),
            ("public_number", "REQ-0001"),
        ],
    )
    def test_it_refuses_fields_it_does_not_offer(self, client, db_session, field, value):
        # Refused rather than silently ignored, so a caller that thinks it
        # is setting something finds out.
        resp = post(client, [item(**{field: value})])
        assert resp.json()["rejected"] == 1
        assert field in resp.json()["results"][0]["error"]
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_created_requests_have_no_requester(self, client, db_session):
        post(client, [item()])
        req = db_session.scalar(select(RequestModel))
        assert req.requester_name is None
        assert req.requester_email is None

    def test_it_stores_the_whole_item(self, client, db_session):
        post(
            client,
            [
                item(
                    title="Reconcile PCard statement",
                    request_class="long",
                    source="manual",
                    desired_completion_date="2026-11-24",
                    desired_date_reason="Stated Nov 27; 3 business days earlier, approval follows.",
                    additional_context="October statement.",
                    links=[{"url": "message://%3Cabc@uwo.ca%3E", "label": "Originating email"}],
                )
            ],
        )
        req = db_session.scalar(select(RequestModel))
        assert req.source == RequestSource.manual
        assert str(req.desired_completion_date) == "2026-11-24"
        assert "Nov 27" in req.desired_date_reason
        assert req.additional_context == "October statement."
        assert [(link.label, link.url) for link in req.links] == [
            ("Originating email", "message://%3Cabc@uwo.ca%3E")
        ]

    def test_a_date_without_a_reason_is_rejected(self, client, db_session):
        resp = post(client, [item(desired_completion_date="2026-12-01")])
        assert resp.json()["rejected"] == 1
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_it_notifies_nobody(self, client, monkeypatch):
        from unittest.mock import patch

        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
        monkeypatch.setattr(settings, "smtp_password", "app-password")
        monkeypatch.setattr(settings, "owner_notification_email", "owner@example.com")

        with patch("app.email.send_email") as mock_send:
            post(client, [item()])
        assert mock_send.call_count == 0


class TestBatch:
    def test_several_items_in_one_post(self, client, db_session):
        resp = post(client, [item(title=f"Item {n}") for n in range(3)])
        assert resp.json()["created"] == 3
        assert len(db_session.scalars(select(RequestModel)).all()) == 3

    def test_one_bad_item_does_not_sink_the_rest(self, client, db_session):
        resp = post(
            client,
            [item(title="Good one"), item(title=""), item(title="Also good")],
        )
        body = resp.json()
        assert resp.status_code == 200
        assert (body["created"], body["rejected"]) == (2, 0 + 1)
        assert [r["status"] for r in body["results"]] == ["created", "rejected", "created"]

        titles = sorted(r.title for r in db_session.scalars(select(RequestModel)))
        assert titles == ["Also good", "Good one"]

    def test_results_carry_the_index_and_public_number(self, client):
        results = post(client, [item(title="A"), item(title="B")]).json()["results"]
        assert [r["index"] for r in results] == [0, 1]
        assert all(r["public_number"].startswith("REQ-") for r in results)

    def test_a_rejected_item_consumes_no_request_number(self, client, db_session):
        post(client, [item(title=""), item(title="Only real one")])
        assert db_session.scalar(select(RequestModel)).public_number == "REQ-0001"

    def test_a_bare_list_is_accepted_too(self, client):
        resp = client.post(URL, json=[item()], headers=auth())
        assert resp.status_code == 200
        assert resp.json()["created"] == 1

    def test_an_empty_batch_is_fine(self, client):
        assert post(client, []).json() == {
            "results": [],
            "created": 0,
            "duplicate": 0,
            "rejected": 0,
        }


class TestLimits:
    def test_too_many_items_rejects_the_whole_batch(self, client, db_session):
        from app.routers.intake import MAX_INTAKE_ITEMS

        resp = post(client, [item(title=f"Item {n}") for n in range(MAX_INTAKE_ITEMS + 1)])
        assert resp.status_code == 422
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_exactly_the_item_cap_is_allowed(self, client):
        from app.routers.intake import MAX_INTAKE_ITEMS

        resp = post(client, [item(title=f"Item {n}") for n in range(MAX_INTAKE_ITEMS)])
        assert resp.status_code == 200
        assert resp.json()["created"] == MAX_INTAKE_ITEMS

    def test_an_oversized_body_is_refused(self, client, db_session):
        from app.routers.intake import MAX_INTAKE_BODY_BYTES

        payload = json.dumps({"items": [item(description="x" * (MAX_INTAKE_BODY_BYTES + 100))]})
        resp = client.post(
            URL, content=payload, headers={**auth(), "Content-Type": "application/json"}
        )
        assert resp.status_code == 413
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_a_non_json_body_is_refused(self, client):
        resp = client.post(URL, content=b"not json", headers=auth())
        assert resp.status_code == 400

    def test_a_json_scalar_is_refused(self, client):
        resp = client.post(URL, json=5, headers=auth())
        assert resp.status_code == 422

    def test_the_route_is_rate_limited(self):
        # The mechanism itself is proven in tests/test_login_rate_limit.py;
        # what matters here is that this route is actually registered with
        # the limiter rather than left uncovered.
        import app.main  # noqa: F401  (registers the routes)
        from app.rate_limit import limiter

        assert "app.routers.intake.intake_requests" in limiter._route_limits


class TestIdempotency:
    KEY = "<CAE1234@mail.uwo.ca>"

    def test_the_same_key_twice_creates_one_request(self, client, db_session):
        first = post(client, [item(dedupe_key=self.KEY)]).json()
        second = post(client, [item(dedupe_key=self.KEY)]).json()

        assert first["created"] == 1
        assert second["created"] == 0
        assert second["duplicate"] == 1
        assert len(db_session.scalars(select(RequestModel)).all()) == 1

    def test_a_duplicate_reports_the_original(self, client):
        first = post(client, [item(dedupe_key=self.KEY)]).json()["results"][0]
        second = post(client, [item(dedupe_key=self.KEY)]).json()["results"][0]
        assert second["status"] == "duplicate"
        assert second["request_id"] == first["request_id"]
        assert second["public_number"] == first["public_number"]

    def test_a_repeat_inside_one_batch_is_caught(self, client, db_session):
        body = post(client, [item(dedupe_key=self.KEY), item(dedupe_key=self.KEY)]).json()
        assert (body["created"], body["duplicate"]) == (1, 1)
        assert len(db_session.scalars(select(RequestModel)).all()) == 1

    def test_different_keys_are_separate_requests(self, client, db_session):
        post(client, [item(dedupe_key="<a@uwo.ca>"), item(dedupe_key="<b@uwo.ca>")])
        assert len(db_session.scalars(select(RequestModel)).all()) == 2

    def test_items_with_no_key_are_never_deduped(self, client, db_session):
        # Two genuinely identical items with no key must both be stored —
        # deduping on content would silently drop legitimate repeats.
        post(client, [item(), item()])
        assert len(db_session.scalars(select(RequestModel)).all()) == 2

    @pytest.mark.parametrize("terminal", ["declined", "cancelled"])
    def test_a_decided_item_is_not_resurrected(self, client, db_session, terminal):
        from app.state_machine import cancel, decline

        post(client, [item(dedupe_key=self.KEY)])
        req = db_session.scalar(select(RequestModel))
        if terminal == "declined":
            decline(db_session, req, "Not needed.")
        else:
            cancel(db_session, req, "Did not apply.")
        db_session.commit()

        body = post(client, [item(dedupe_key=self.KEY)]).json()
        assert body["duplicate"] == 1
        assert len(db_session.scalars(select(RequestModel)).all()) == 1
        db_session.expire_all()
        assert db_session.scalar(select(RequestModel)).status != RequestStatus.submitted

    def test_an_over_long_key_is_rejected(self, client, db_session):
        from app.schemas import MAX_DEDUPE_KEY_LEN

        resp = post(client, [item(dedupe_key="x" * (MAX_DEDUPE_KEY_LEN + 1))])
        assert resp.json()["rejected"] == 1
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_a_key_is_echoed_back(self, client):
        result = post(client, [item(dedupe_key=self.KEY)]).json()["results"][0]
        assert result["dedupe_key"] == self.KEY
