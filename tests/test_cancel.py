"""Tests for cancelling a request, and for what deactivating a standing
commitment does to the occurrence it already generated.

The property under test throughout is that the archive stays truthful: a
cancelled request must never be recorded as done, and must stay
distinguishable from one that was declined or withdrawn.
"""

import re
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import (
    EventType,
    RecurringCommitment,
    Request as RequestModel,
    RequestClass,
    RequestSource,
    RequestStatus,
)
from app.recurrence import generate_due_requests
from app.security import generate_access_token
from app.state_machine import TransitionError, cancel
from tests.conftest import login, submit_request

ACTIVE_STATUSES = (
    RequestStatus.submitted,
    RequestStatus.queued,
    RequestStatus.in_progress,
    RequestStatus.needs_information,
)
CLOSED_STATUSES = (RequestStatus.done, RequestStatus.declined, RequestStatus.withdrawn)


def make_request(db_session, **kwargs) -> RequestModel:
    defaults = dict(
        public_number=f"REQ-{abs(hash(str(kwargs))) % 9000 + 1000}",
        access_token=generate_access_token(),
        title="Reconcile PCard statement — November 2026",
        description="Match receipts, code the lines, submit for approval.",
        request_class=RequestClass.long,
        status=RequestStatus.submitted,
        source=RequestSource.manual,
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


def commitment(**kwargs) -> RecurringCommitment:
    defaults = dict(
        title="Reconcile PCard statement",
        description="Match receipts, code the lines, submit for approval.",
        request_class=RequestClass.long,
        source=RequestSource.manual,
        months=None,
        day_of_month=None,
        business_days_before_month_end=1,
        lead_business_days=3,
        generate_days_ahead=14,
        active=True,
        last_generated_stated_date=None,
    )
    defaults.update(kwargs)
    return RecurringCommitment(**defaults)


class TestTransition:
    @pytest.mark.parametrize("status", ACTIVE_STATUSES)
    def test_cancels_from_any_active_status(self, db_session, status):
        req = make_request(db_session, status=status)
        cancel(db_session, req)
        assert req.status == RequestStatus.cancelled
        assert req.cancelled_at is not None

    @pytest.mark.parametrize("status", CLOSED_STATUSES)
    def test_refuses_a_closed_request(self, db_session, status):
        req = make_request(db_session, status=status)
        with pytest.raises(TransitionError):
            cancel(db_session, req)

    def test_refuses_an_already_cancelled_request(self, db_session):
        req = make_request(db_session, status=RequestStatus.cancelled)
        with pytest.raises(TransitionError):
            cancel(db_session, req)

    def test_does_not_record_work_that_did_not_happen(self, db_session):
        req = make_request(db_session, status=RequestStatus.in_progress)
        cancel(db_session, req)
        assert req.status != RequestStatus.done
        assert req.completed_at is None
        assert req.completion_note is None

    def test_stays_distinguishable_from_decline_and_withdrawal(self, db_session):
        req = make_request(db_session)
        cancel(db_session, req, "No transactions this month.")
        assert req.status == RequestStatus.cancelled
        assert req.declined_at is None
        assert req.decline_reason is None
        assert req.withdrawn_at is None

    def test_leaves_the_queue_contiguous(self, db_session):
        first = make_request(db_session, status=RequestStatus.queued, queue_position=1, title="A")
        second = make_request(db_session, status=RequestStatus.queued, queue_position=2, title="B")
        third = make_request(db_session, status=RequestStatus.queued, queue_position=3, title="C")
        cancel(db_session, second)
        assert second.queue_position is None
        assert first.queue_position == 1
        assert third.queue_position == 2

    def test_stores_a_reason_when_given(self, db_session):
        req = make_request(db_session)
        cancel(db_session, req, "  No transactions this month.  ")
        assert req.cancel_reason == "No transactions this month."

    def test_a_reason_is_optional(self, db_session):
        req = make_request(db_session)
        cancel(db_session, req, "   ")
        assert req.cancel_reason is None

    def test_logs_a_cancelled_event_with_the_reason(self, db_session):
        req = make_request(db_session)
        cancel(db_session, req, "No exams this term.")
        db_session.flush()
        event = req.events[-1]
        assert event.event_type == EventType.cancelled
        assert event.event_metadata == {"reason": "No exams this term."}

    def test_clears_the_restore_fields(self, db_session):
        req = make_request(
            db_session,
            status=RequestStatus.needs_information,
            previous_status=RequestStatus.queued,
            previous_queue_position=2,
        )
        cancel(db_session, req)
        assert req.previous_status is None
        assert req.previous_queue_position is None


class TestOwnerRoute:
    def _owner_request(self, client, db_session, **kwargs) -> RequestModel:
        login(client)
        return make_request(db_session, **kwargs)

    def test_cancels_through_the_route(self, client, db_session):
        req = self._owner_request(client, db_session)
        csrf = re.search(
            r'name="csrf_token" value="([^"]*)"', client.get(f"/requests/{req.id}").text
        ).group(1)
        resp = client.post(
            f"/requests/{req.id}/cancel",
            data={"csrf_token": csrf, "reason": "No transactions this month."},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        db_session.expire_all()
        req = db_session.scalar(select(RequestModel))
        assert req.status == RequestStatus.cancelled
        assert req.cancel_reason == "No transactions this month."

    def test_the_action_is_offered_on_an_active_request(self, client, db_session):
        req = self._owner_request(client, db_session, status=RequestStatus.in_progress)
        assert "Cancel — no longer needed" in client.get(f"/requests/{req.id}").text

    def test_the_action_is_gone_once_closed(self, client, db_session):
        req = self._owner_request(client, db_session, status=RequestStatus.done)
        assert "Cancel — no longer needed" not in client.get(f"/requests/{req.id}").text

    def test_the_detail_page_says_it_was_not_completed(self, client, db_session):
        req = self._owner_request(
            client, db_session, status=RequestStatus.cancelled, cancel_reason="No exams this term."
        )
        page = client.get(f"/requests/{req.id}").text
        assert "no longer needed, rather than completed or declined" in page
        assert "No exams this term." in page

    def test_requires_csrf(self, client, db_session):
        req = self._owner_request(client, db_session)
        resp = client.post(f"/requests/{req.id}/cancel", data={}, follow_redirects=False)
        assert resp.status_code == 403
        db_session.expire_all()
        assert db_session.scalar(select(RequestModel)).status == RequestStatus.submitted

    def test_requires_login(self, client, db_session):
        req = make_request(db_session)
        resp = client.post(f"/requests/{req.id}/cancel", data={}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"

    def test_cancelling_an_owner_item_mails_nobody(self, client, db_session, monkeypatch):
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
        monkeypatch.setattr(settings, "smtp_password", "app-password")
        monkeypatch.setattr(settings, "owner_notification_email", "owner@example.com")

        req = self._owner_request(client, db_session)
        csrf = re.search(
            r'name="csrf_token" value="([^"]*)"', client.get(f"/requests/{req.id}").text
        ).group(1)
        with patch("app.email.send_email") as mock_send:
            client.post(f"/requests/{req.id}/cancel", data={"csrf_token": csrf})
        assert mock_send.call_count == 0

    def test_cancelling_a_form_request_tells_its_requester(self, client, monkeypatch):
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
        monkeypatch.setattr(settings, "smtp_password", "app-password")

        with patch("app.notifications.send_email") as mock_send:
            submit_request(client)
            login(client)
            page = client.get("/dashboard").text
            request_id = int(re.search(r"/requests/(\d+)", page).group(1))
            csrf = re.search(
                r'name="csrf_token" value="([^"]*)"', client.get(f"/requests/{request_id}").text
            ).group(1)
            mock_send.reset_mock()
            client.post(f"/requests/{request_id}/cancel", data={"csrf_token": csrf})

        assert mock_send.call_count == 1
        to_address, subject, body, _ = mock_send.call_args[0]
        assert to_address == "alice@example.com"
        assert "cancelled" in subject.lower()
        assert "rather than having been completed or declined" in body


class TestArchive:
    def test_a_cancelled_request_reaches_the_archive(self, client, db_session):
        login(client)
        make_request(db_session, status=RequestStatus.cancelled, title="Nothing to reconcile")
        assert "Nothing to reconcile" in client.get("/archive").text

    def test_it_filters_separately_from_done(self, client, db_session):
        login(client)
        make_request(db_session, status=RequestStatus.cancelled, title="Cancelled item")
        make_request(db_session, status=RequestStatus.done, title="Completed item")

        cancelled_only = client.get("/archive?status=cancelled").text
        assert "Cancelled item" in cancelled_only
        assert "Completed item" not in cancelled_only

        done_only = client.get("/archive?status=done").text
        assert "Completed item" in done_only
        assert "Cancelled item" not in done_only


class TestTrackingPage:
    def test_a_requester_sees_it_was_cancelled_not_completed(self, client, db_session):
        req = make_request(
            db_session,
            status=RequestStatus.cancelled,
            source=RequestSource.form,
            requester_name="Alice",
            requester_email="alice@example.com",
            cancel_reason="The committee was dissolved.",
        )
        page = client.get(f"/r/{req.access_token}").text
        assert "Cancelled" in page
        assert "It was not completed, and it was not declined." in page
        assert "The committee was dissolved." in page

    def test_requester_actions_are_withdrawn_once_cancelled(self, client, db_session):
        req = make_request(
            db_session,
            status=RequestStatus.cancelled,
            source=RequestSource.form,
            requester_name="Alice",
            requester_email="alice@example.com",
        )
        assert "Requester actions" not in client.get(f"/r/{req.access_token}").text


class TestDeactivatingASeries:
    def _setup(self, client, db_session, today=date(2026, 11, 15)):
        login(client)
        c = commitment()
        db_session.add(c)
        db_session.commit()
        generate_due_requests(db_session, today=today)
        db_session.commit()
        return c, db_session.scalar(select(RequestModel))

    def _deactivate(self, client, commitment_id):
        csrf = re.search(
            r'name="csrf_token" value="([^"]*)"', client.get("/settings").text
        ).group(1)
        return client.post(
            f"/settings/recurring/{commitment_id}/active",
            data={"csrf_token": csrf, "active": ""},
            follow_redirects=False,
        )

    def test_the_open_occurrence_is_cancelled_not_orphaned(self, client, db_session):
        c, occurrence = self._setup(client, db_session)
        assert occurrence.status == RequestStatus.submitted

        assert self._deactivate(client, c.id).status_code == 303
        db_session.expire_all()
        occurrence = db_session.scalar(select(RequestModel))
        assert occurrence.status == RequestStatus.cancelled
        assert occurrence.cancel_reason == "Standing commitment deactivated."

    def test_the_flash_says_the_occurrence_was_cancelled(self, client, db_session):
        c, _ = self._setup(client, db_session)
        self._deactivate(client, c.id)
        assert "Cancelled its open occurrence" in client.get("/settings").text

    def test_an_accepted_occurrence_is_also_cancelled(self, client, db_session):
        from app.state_machine import accept_and_queue

        c, occurrence = self._setup(client, db_session)
        accept_and_queue(db_session, occurrence)
        db_session.commit()

        self._deactivate(client, c.id)
        db_session.expire_all()
        occurrence = db_session.scalar(select(RequestModel))
        assert occurrence.status == RequestStatus.cancelled
        assert occurrence.queue_position is None

    def test_an_already_completed_occurrence_is_left_alone(self, client, db_session):
        from app.state_machine import accept_and_queue, complete

        c, occurrence = self._setup(client, db_session)
        accept_and_queue(db_session, occurrence)
        complete(db_session, occurrence, "Done.")
        db_session.commit()

        self._deactivate(client, c.id)
        db_session.expire_all()
        occurrence = db_session.scalar(select(RequestModel))
        assert occurrence.status == RequestStatus.done

    def test_deactivating_with_nothing_open_says_nothing_extra(self, client, db_session):
        login(client)
        c = commitment()
        db_session.add(c)
        db_session.commit()
        self._deactivate(client, c.id)
        assert "Cancelled its open occurrence" not in client.get("/settings").text

    def test_reactivating_does_not_resurrect_the_cancelled_occurrence(self, client, db_session):
        c, _ = self._setup(client, db_session)
        self._deactivate(client, c.id)
        csrf = re.search(
            r'name="csrf_token" value="([^"]*)"', client.get("/settings").text
        ).group(1)
        client.post(
            f"/settings/recurring/{c.id}/active",
            data={"csrf_token": csrf, "active": "on"},
            follow_redirects=False,
        )
        db_session.expire_all()
        assert db_session.scalar(select(RequestModel)).status == RequestStatus.cancelled


class TestGenerationAfterCancelling:
    def _generated(self, db_session, today=date(2026, 11, 15)):
        c = commitment()
        db_session.add(c)
        db_session.commit()
        generate_due_requests(db_session, today=today)
        db_session.commit()
        return c, db_session.scalar(select(RequestModel))

    def test_a_cancelled_occurrence_is_not_re_created(self, db_session):
        c, occurrence = self._generated(db_session)
        cancel(db_session, occurrence, "No transactions this month.")
        db_session.commit()

        # Same day, and again later in the same cycle.
        for today in (date(2026, 11, 15), date(2026, 11, 20), date(2026, 11, 27)):
            generate_due_requests(db_session, today=today)
            db_session.commit()

        requests = db_session.scalars(select(RequestModel)).all()
        assert len(requests) == 1
        assert requests[0].status == RequestStatus.cancelled

    def test_the_next_cycle_still_generates_normally(self, db_session):
        c, occurrence = self._generated(db_session)
        cancel(db_session, occurrence, "No transactions this month.")
        db_session.commit()

        generate_due_requests(db_session, today=date(2026, 12, 20))
        db_session.commit()

        titles = sorted(r.title for r in db_session.scalars(select(RequestModel)).all())
        assert titles == [
            "Reconcile PCard statement — December 2026",
            "Reconcile PCard statement — November 2026",
        ]

    def test_cancelling_leaves_the_series_active(self, db_session):
        c, occurrence = self._generated(db_session)
        cancel(db_session, occurrence)
        db_session.commit()
        db_session.expire_all()
        assert db_session.scalar(select(RecurringCommitment)).active is True
