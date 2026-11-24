"""Tests for putting a request on hold while somebody else has it.

The property under test throughout: a hold is a pause, not a close. The
request stays live everywhere that matters (the intake disposition the mail
sweep reads, the calendar feed, the dashboard's open counts), it keeps the
queue position it had, and it names who is being waited on so it can be
chased.

Also pins the fix this status was built for: `Request Information` must not
be offered on a request with no requester, because `needs_information` can
only be left from the public tracking page.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import (
    EventType,
    Request as RequestModel,
    RequestClass,
    RequestEvent,
    RequestSource,
    RequestStatus,
)
from app.routers.intake import DISPOSITIONS
from app.security import generate_access_token
from app.state_machine import (
    TransitionError,
    block,
    cancel,
    complete,
    start_work,
    unblock,
    withdraw,
)
from tests.conftest import get_csrf_and_cookies, login, submit_request

_counter = iter(range(1000, 9999))


def make_request(db_session, **kwargs) -> RequestModel:
    defaults = dict(
        public_number=f"REQ-{next(_counter)}",
        access_token=generate_access_token(),
        title="Confirm supervisory committee membership",
        description="Needs the student's signed form before it can go to the Chair.",
        request_class=RequestClass.long,
        status=RequestStatus.submitted,
        source=RequestSource.email,
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


def queued_long(db_session, position: int, **kwargs) -> RequestModel:
    return make_request(
        db_session, status=RequestStatus.queued, queue_position=position, **kwargs
    )


# ---------------------------------------------------------------------------
# The transition itself
# ---------------------------------------------------------------------------


class TestBlock:
    @pytest.mark.parametrize(
        "status", [RequestStatus.submitted, RequestStatus.queued, RequestStatus.in_progress]
    )
    def test_block_is_reachable_from_every_live_status(self, db_session, status):
        req = make_request(db_session, status=status)
        block(db_session, req, "Mahsa — signed leave form")
        db_session.commit()
        assert req.status == RequestStatus.blocked
        assert req.previous_status == status

    @pytest.mark.parametrize(
        "status",
        [
            RequestStatus.done,
            RequestStatus.declined,
            RequestStatus.withdrawn,
            RequestStatus.cancelled,
            RequestStatus.needs_information,
        ],
    )
    def test_a_closed_or_already_waiting_request_cannot_be_blocked(self, db_session, status):
        req = make_request(db_session, status=status)
        with pytest.raises(TransitionError):
            block(db_session, req, "Mahsa")

    def test_waiting_on_is_required(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued)
        with pytest.raises(TransitionError, match="who or what"):
            block(db_session, req, "   ")
        assert req.status == RequestStatus.queued

    def test_block_records_who_when_and_the_note(self, db_session):
        req = make_request(db_session, status=RequestStatus.in_progress)
        when = date.today() + timedelta(days=14)
        block(db_session, req, "  Mahsa — signed leave form  ", when, "  Asked by email Nov 24.  ")
        db_session.commit()
        assert req.blocked_on == "Mahsa — signed leave form"
        assert req.blocked_until == when
        assert req.blocked_note == "Asked by email Nov 24."
        assert req.blocked_at is not None

    def test_block_logs_an_event_naming_the_second_party(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        block(db_session, req, "Registrar", date(2027, 1, 15))
        db_session.commit()
        event = db_session.scalars(
            select(RequestEvent).where(RequestEvent.event_type == EventType.blocked)
        ).one()
        assert event.event_metadata["waiting_on"] == "Registrar"
        assert event.event_metadata["until"] == "2027-01-15"


class TestQueuePosition:
    def test_blocking_leaves_the_queue_and_renumbers_the_rest(self, db_session):
        first = queued_long(db_session, 1)
        second = queued_long(db_session, 2)
        third = queued_long(db_session, 3)

        block(db_session, second, "Co-author — revised figures")
        db_session.commit()

        assert second.queue_position is None
        assert second.previous_queue_position == 2
        assert first.queue_position == 1
        assert third.queue_position == 2

    def test_unblocking_restores_the_position_it_had(self, db_session):
        first = queued_long(db_session, 1)
        second = queued_long(db_session, 2)
        third = queued_long(db_session, 3)

        block(db_session, second, "Co-author")
        db_session.commit()
        unblock(db_session, second)
        db_session.commit()

        assert second.status == RequestStatus.queued
        assert second.queue_position == 2
        assert first.queue_position == 1
        assert third.queue_position == 3

    def test_an_in_progress_request_comes_back_in_progress(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        start_work(db_session, req)
        db_session.commit()
        block(db_session, req, "Supplier — quote")
        db_session.commit()
        unblock(db_session, req)
        db_session.commit()
        assert req.status == RequestStatus.in_progress

    def test_a_submitted_request_comes_back_submitted(self, db_session):
        req = make_request(db_session, status=RequestStatus.submitted)
        block(db_session, req, "Student — transcript")
        db_session.commit()
        unblock(db_session, req)
        db_session.commit()
        assert req.status == RequestStatus.submitted
        assert req.queue_position is None


class TestUnblock:
    def test_only_a_blocked_request_can_be_unblocked(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        with pytest.raises(TransitionError, match="not on hold"):
            unblock(db_session, req)

    def test_unblock_clears_the_waiting_on_detail(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        block(db_session, req, "Registrar", date.today(), "note")
        db_session.commit()
        unblock(db_session, req)
        db_session.commit()
        assert req.blocked_on is None
        assert req.blocked_until is None
        assert req.blocked_at is None
        assert req.blocked_note is None
        assert req.previous_status is None

    def test_unblock_logs_what_it_had_been_waiting_on(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        block(db_session, req, "Registrar")
        db_session.commit()
        unblock(db_session, req)
        db_session.commit()
        event = db_session.scalars(
            select(RequestEvent).where(RequestEvent.event_type == EventType.unblocked)
        ).one()
        assert event.event_metadata["was_waiting_on"] == "Registrar"


class TestStillLive:
    """A hold is a pause. Everything that asks "is this still open?" has to
    keep saying yes, or the surrounding machinery quietly closes it."""

    def test_intake_reports_a_blocked_request_as_active(self):
        # Load-bearing: the twice-daily mail sweep files the originating
        # message once its request stops being active. A blocked request's
        # message is the evidence of what is being waited for and must stay
        # in the Inbox.
        assert DISPOSITIONS[RequestStatus.blocked] == "active"

    def test_every_status_has_a_disposition(self):
        assert set(DISPOSITIONS) == set(RequestStatus)

    def test_blocked_stays_on_the_calendar_feed(self):
        from app.calendar_feed import CALENDAR_STATUSES

        # Waiting on somebody does not move the date the work is due.
        assert RequestStatus.blocked in CALENDAR_STATUSES

    def test_blocked_counts_as_open_on_the_dashboard(self):
        from app.routers.dashboard import OPEN_STATUSES

        assert RequestStatus.blocked in OPEN_STATUSES

    def test_a_blocked_request_can_still_be_cancelled(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        block(db_session, req, "Registrar")
        db_session.commit()
        cancel(db_session, req, "Student withdrew from the program")
        db_session.commit()
        assert req.status == RequestStatus.cancelled
        assert req.blocked_on is None

    def test_a_blocked_request_can_still_be_withdrawn(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        block(db_session, req, "Registrar")
        db_session.commit()
        withdraw(db_session, req)
        db_session.commit()
        assert req.status == RequestStatus.withdrawn
        assert req.blocked_on is None

    def test_a_blocked_request_is_not_completable_without_unblocking(self, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        block(db_session, req, "Registrar")
        db_session.commit()
        with pytest.raises(TransitionError):
            complete(db_session, req)


# ---------------------------------------------------------------------------
# Through the dashboard
# ---------------------------------------------------------------------------


class TestDashboardRoutes:
    def test_owner_can_put_a_request_on_hold_and_take_it_off(self, client, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        login(client)

        csrf, _ = get_csrf_and_cookies(client, f"/requests/{req.id}")
        resp = client.post(
            f"/requests/{req.id}/block",
            data={
                "csrf_token": csrf,
                "waiting_on": "Mahsa — signed leave form",
                "until": (date.today() + timedelta(days=7)).isoformat(),
                "note": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        db_session.expire_all()
        assert db_session.get(RequestModel, req.id).status == RequestStatus.blocked

        csrf, _ = get_csrf_and_cookies(client, f"/requests/{req.id}")
        resp = client.post(
            f"/requests/{req.id}/unblock", data={"csrf_token": csrf}, follow_redirects=False
        )
        assert resp.status_code == 303
        db_session.expire_all()
        restored = db_session.get(RequestModel, req.id)
        assert restored.status == RequestStatus.queued
        assert restored.queue_position == 1

    def test_an_empty_chase_date_is_accepted(self, client, db_session):
        # What an untouched date input posts. Typing it as `date | None`
        # on the route would 422 here.
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        login(client)
        csrf, _ = get_csrf_and_cookies(client, f"/requests/{req.id}")
        resp = client.post(
            f"/requests/{req.id}/block",
            data={"csrf_token": csrf, "waiting_on": "Registrar", "until": "", "note": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        db_session.expire_all()
        held = db_session.get(RequestModel, req.id)
        assert held.status == RequestStatus.blocked
        assert held.blocked_until is None

    def test_the_dashboard_lists_held_requests_and_flags_an_overdue_chase(
        self, client, db_session
    ):
        overdue = make_request(db_session, title="Overdue hold", status=RequestStatus.queued, queue_position=1)
        future = make_request(db_session, title="Future hold", status=RequestStatus.queued, queue_position=2)
        block(db_session, overdue, "Student A", date.today() - timedelta(days=3))
        block(db_session, future, "Student B", date.today() + timedelta(days=30))
        db_session.commit()

        login(client)
        body = client.get("/dashboard").text

        assert "On hold" in body
        assert "Waiting on Student A" in body
        assert "Waiting on Student B" in body
        assert "Chase up" in body
        # The one past its date sorts above the one that is not.
        assert body.index("Overdue hold") < body.index("Future hold")

    def test_the_detail_page_offers_the_hold_and_shows_who_is_waited_on(self, client, db_session):
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        login(client)
        assert "Put on hold" in client.get(f"/requests/{req.id}").text

        block(db_session, req, "Mahsa — signed leave form")
        db_session.commit()
        body = client.get(f"/requests/{req.id}").text
        assert "Mahsa — signed leave form" in body
        assert "Take off hold" in body
        assert "On Hold" in body
        # Says where it will land, so taking it off hold is not a leap.
        assert "queue position 1" in body

    @pytest.mark.parametrize(
        ("offset", "expected", "absent"),
        [(-2, "Due a nudge since", "Chase up on"), (30, "Chase up on", "Due a nudge since")],
    )
    def test_the_detail_page_compares_the_chase_date_against_today(
        self, client, db_session, offset, expected, absent
    ):
        # Covers the `today()` template global in both directions. It is a
        # callable, not a value: as a value it would be frozen at import and
        # a long-running server would never notice the date changing.
        req = make_request(db_session, status=RequestStatus.queued, queue_position=1)
        block(db_session, req, "Registrar", date.today() + timedelta(days=offset))
        db_session.commit()
        login(client)
        body = client.get(f"/requests/{req.id}").text
        assert expected in body
        assert absent not in body


# ---------------------------------------------------------------------------
# The bug this was built for
# ---------------------------------------------------------------------------


class TestRequestInformationIsGated:
    def test_not_offered_on_a_request_with_no_requester(self, client, db_session):
        # REQ-0012's shape: captured from the mailbox, nobody on the other
        # end. `needs_information` is only escapable from the public
        # tracking page, so offering it here strands the request.
        req = make_request(db_session, source=RequestSource.email, status=RequestStatus.queued, queue_position=1)
        login(client)
        body = client.get(f"/requests/{req.id}").text
        assert "/request-info" not in body
        assert "Put on hold" in body

    def test_the_state_machine_refuses_it_too_not_just_the_template(self, db_session):
        # The template gate is the browser-side half. A direct POST, a
        # script, or a future caller gets the same answer — and the answer
        # says what to do instead.
        from app.state_machine import request_information

        req = make_request(db_session, source=RequestSource.email, status=RequestStatus.queued, queue_position=1)
        with pytest.raises(TransitionError, match="no requester to ask"):
            request_information(db_session, req, "which form?")
        assert req.status == RequestStatus.queued

    def test_posting_to_the_route_anyway_gives_a_409_saying_what_to_do(self, client, db_session):
        req = make_request(db_session, source=RequestSource.email, status=RequestStatus.queued, queue_position=1)
        login(client)
        csrf, _ = get_csrf_and_cookies(client, f"/requests/{req.id}")
        resp = client.post(
            f"/requests/{req.id}/request-info",
            data={"csrf_token": csrf, "question": "which form?"},
            follow_redirects=False,
        )
        assert resp.status_code == 409
        assert "on hold" in resp.text
        db_session.expire_all()
        assert db_session.get(RequestModel, req.id).status == RequestStatus.queued

    @pytest.mark.parametrize(
        "status", [RequestStatus.submitted, RequestStatus.queued, RequestStatus.in_progress]
    )
    def test_still_offered_where_there_is_somebody_to_ask(self, client, db_session, status):
        req = make_request(
            db_session,
            source=RequestSource.form,
            requester_name="Alice",
            requester_email="alice@example.com",
            status=status,
            queue_position=1 if status == RequestStatus.queued else None,
        )
        login(client)
        assert "/request-info" in client.get(f"/requests/{req.id}").text


class TestRequesterFacing:
    def test_a_form_requester_is_told_their_request_is_on_hold(self, client, db_session):
        track_url = submit_request(client)
        req = db_session.scalars(select(RequestModel)).one()
        login(client)

        csrf, _ = get_csrf_and_cookies(client, f"/requests/{req.id}")
        with patch("app.notifications.send_email") as send:
            client.post(
                f"/requests/{req.id}/block",
                data={"csrf_token": csrf, "waiting_on": "The Registrar", "until": "", "note": ""},
                follow_redirects=False,
            )
        assert send.called, "a requester with an address should be told"
        body = send.call_args.args[2]
        assert "The Registrar" in body
        assert "on hold" in body.lower()

        page = client.get(track_url).text
        assert "On Hold" in page
        assert "The Registrar" in page

    def test_nothing_is_emailed_for_a_request_with_no_requester(self, client, db_session):
        req = make_request(db_session, source=RequestSource.email, status=RequestStatus.queued, queue_position=1)
        login(client)
        csrf, _ = get_csrf_and_cookies(client, f"/requests/{req.id}")
        with patch("app.notifications.send_email") as send:
            client.post(
                f"/requests/{req.id}/block",
                data={"csrf_token": csrf, "waiting_on": "Mahsa", "until": "", "note": ""},
                follow_redirects=False,
            )
        assert not send.called

    def test_the_chase_date_is_not_shown_to_the_requester(self, client, db_session):
        track_url = submit_request(client)
        req = db_session.scalars(select(RequestModel)).one()
        block(db_session, req, "The Registrar", date(2027, 3, 9))
        db_session.commit()
        page = client.get(track_url).text
        assert "The Registrar" in page
        # A private working note about when to nudge somebody, not a promise.
        assert "March 9, 2027" not in page


class TestQuickSession:
    """The quick run-through needs the same escape, and the same gate."""

    def _quick(self, db_session, **kwargs):
        return make_request(
            db_session,
            request_class=RequestClass.quick,
            status=RequestStatus.queued,
            queue_position=1,
            **kwargs,
        )

    def test_a_quick_action_can_be_put_on_hold_in_one_field(self, client, db_session):
        req = self._quick(db_session)
        login(client)
        csrf, _ = get_csrf_and_cookies(client, "/quick-session")
        resp = client.post(
            f"/quick-session/{req.id}/block",
            data={"csrf_token": csrf, "waiting_on": "Mahsa — signed leave form"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        db_session.expire_all()
        held = db_session.get(RequestModel, req.id)
        assert held.status == RequestStatus.blocked
        assert held.blocked_on == "Mahsa — signed leave form"
        assert held.blocked_until is None

    def test_the_session_offers_hold_and_hides_need_information(self, client, db_session):
        self._quick(db_session, source=RequestSource.email)
        login(client)
        body = client.get("/quick-session").text
        assert "Put on hold" in body
        assert "/need-info" not in body

    def test_need_information_survives_where_there_is_a_requester(self, client, db_session):
        self._quick(
            db_session,
            source=RequestSource.form,
            requester_name="Alice",
            requester_email="alice@example.com",
        )
        login(client)
        body = client.get("/quick-session").text
        assert "/need-info" in body
        assert "Put on hold" in body

    def test_a_held_quick_action_leaves_the_session(self, client, db_session):
        # The point of the hold: it stops being offered as the next thing
        # to do, without being closed.
        req = self._quick(db_session)
        block(db_session, req, "Mahsa")
        db_session.commit()
        login(client)
        assert req.title not in client.get("/quick-session").text
