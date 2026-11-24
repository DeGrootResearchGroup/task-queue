"""Tests for the event request class and its calendar write path.

An event differs from the other two classes in three ways that each have
a way of going wrong unattended:

  - it carries a start and an end, where everything else carries a date;
  - accepting it must NOT give it a queue position, or it pads the
    prioritised list the queue exists to produce;
  - it reaches the Exchange calendar only once accepted, because anything
    that blocks the Owner's availability has to be his decision.

conftest sets INTAKE_TOKEN_HASH to the digest of TOKEN, so the intake
route is registered for this module.
"""

from sqlalchemy import select

from app import state_machine
from app.models import Request as RequestModel, RequestClass, RequestStatus

TOKEN = "test-intake-token"


def auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def event_item(**overrides) -> dict:
    data = {
        "title": "Phys-Pharm Seminar: Jeremy Hirota",
        "description": "Seminar on biomaterials for 3D lung models. MSB 384.",
        "request_class": "event",
        "source": "email",
        "starts_at": "2026-11-30T15:00:00",
        "ends_at": "2026-11-30T16:00:00",
        "location": "MSB 384",
        "dedupe_key": "seminar@example.com",
    }
    data.update(overrides)
    return data


def post(client, items, token: str = TOKEN):
    return client.post("/intake/requests", json={"items": items}, headers=auth(token))


class TestIntake:
    def test_an_event_can_be_captured(self, client, db_session):
        resp = post(client, [event_item()])
        assert resp.status_code == 200, resp.text
        assert resp.json()["created"] == 1
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "seminar@example.com")
        )
        assert req.request_class == RequestClass.event
        assert req.location == "MSB 384"
        assert req.starts_at.hour == 15
        assert req.ends_at.hour == 16

    def test_the_date_column_is_filled_from_the_start(self, client, db_session):
        """Everything that sorts and filters on desired_completion_date --
        the dashboard, the archive, the .ics feed -- keeps working without
        knowing events exist."""
        post(client, [event_item()])
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "seminar@example.com")
        )
        assert req.desired_completion_date.isoformat() == "2026-11-30"
        assert req.desired_date_reason

    def test_an_event_without_times_is_rejected(self, client):
        resp = post(client, [event_item(starts_at=None, ends_at=None)])
        assert resp.json()["created"] == 0

    def test_an_event_ending_before_it_starts_is_rejected(self, client):
        resp = post(client, [event_item(ends_at="2026-11-30T14:00:00")])
        assert resp.json()["created"] == 0

    def test_times_on_a_non_event_are_rejected(self, client):
        """Otherwise a Quick Action could carry a start that nothing reads,
        and the sweep would never write it to the calendar."""
        resp = post(client, [event_item(request_class="quick")])
        assert resp.json()["created"] == 0

    def test_a_caller_cannot_supply_a_calendar_uid(self, client):
        """Accepting one would let an event be marked written without
        anything having been written to the calendar."""
        resp = post(client, [event_item(calendar_uid="FAKE-UID")])
        assert resp.json()["created"] == 0


class TestAccept:
    def _make(self, client, db_session):
        post(client, [event_item()])
        return db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "seminar@example.com")
        )

    def test_accepting_an_event_does_not_give_it_a_queue_position(self, client, db_session):
        req = self._make(client, db_session)
        state_machine.accept_and_queue(db_session, req)
        db_session.commit()
        assert req.status == RequestStatus.queued
        assert req.queue_position is None

    def test_accepting_an_event_does_not_renumber_the_quick_queue(self, client, db_session):
        post(client, [
            {
                "title": "Reconcile PCard",
                "description": "Monthly Visa reconciliation.",
                "request_class": "quick",
                "source": "email",
                "dedupe_key": "pcard@example.com",
            }
        ])
        quick = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "pcard@example.com")
        )
        state_machine.accept_and_queue(db_session, quick)
        db_session.commit()
        before = quick.queue_position

        req = self._make(client, db_session)
        state_machine.accept_and_queue(db_session, req)
        db_session.commit()
        assert quick.queue_position == before


class TestPendingFeed:
    def _captured(self, client, db_session):
        post(client, [event_item()])
        return db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "seminar@example.com")
        )

    def test_a_submitted_event_is_not_offered_for_writing(self, client, db_session):
        """The whole point of the gate: nothing blocks his availability
        before he has accepted it."""
        self._captured(client, db_session)
        resp = client.get("/intake/events/pending", headers=auth())
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_an_accepted_event_is_offered(self, client, db_session):
        req = self._captured(client, db_session)
        state_machine.accept_and_queue(db_session, req)
        db_session.commit()
        body = client.get("/intake/events/pending", headers=auth()).json()
        assert body["count"] == 1
        row = body["items"][0]
        assert row["title"] == "Phys-Pharm Seminar: Jeremy Hirota"
        assert row["location"] == "MSB 384"
        assert row["starts_at"].startswith("2026-11-30T15:00")

    def test_a_declined_event_is_never_offered(self, client, db_session):
        req = self._captured(client, db_session)
        state_machine.decline(db_session, req)
        db_session.commit()
        assert client.get("/intake/events/pending", headers=auth()).json()["count"] == 0

    def test_the_endpoint_needs_the_token(self, client):
        assert client.get("/intake/events/pending").status_code == 404
        assert client.get(
            "/intake/events/pending", headers=auth("wrong")
        ).status_code == 404


class TestRecordingTheUid:
    def _accepted(self, client, db_session):
        post(client, [event_item()])
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "seminar@example.com")
        )
        state_machine.accept_and_queue(db_session, req)
        db_session.commit()
        return req

    def test_recording_a_uid_removes_it_from_pending(self, client, db_session):
        req = self._accepted(client, db_session)
        resp = client.post(
            f"/intake/events/{req.public_number}/written",
            json={"calendar_uid": "FC8DD724-78A9-4C79-8333-DCC5E92FE95A"},
            headers=auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "recorded"
        assert client.get("/intake/events/pending", headers=auth()).json()["count"] == 0

    def test_a_second_write_is_refused_and_names_the_duplicate(self, client, db_session):
        """A sweep that crashed between creating the event and reporting it
        will retry and create a second one. Refusing here leaves a visible
        duplicate on the calendar rather than an orphan nothing can
        delete -- and hands back the UID to clean up."""
        req = self._accepted(client, db_session)
        first = {"calendar_uid": "UID-ONE"}
        client.post(
            f"/intake/events/{req.public_number}/written", json=first, headers=auth()
        )
        resp = client.post(
            f"/intake/events/{req.public_number}/written",
            json={"calendar_uid": "UID-TWO"},
            headers=auth(),
        )
        body = resp.json()
        assert body["status"] == "already_written"
        assert body["calendar_uid"] == "UID-ONE"
        assert body["rejected_uid"] == "UID-TWO"

    def test_recording_against_a_non_event_is_refused(self, client, db_session):
        post(client, [
            {
                "title": "Reconcile PCard",
                "description": "Monthly Visa reconciliation.",
                "request_class": "quick",
                "source": "email",
                "dedupe_key": "pcard2@example.com",
            }
        ])
        quick = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "pcard2@example.com")
        )
        resp = client.post(
            f"/intake/events/{quick.public_number}/written",
            json={"calendar_uid": "UID"},
            headers=auth(),
        )
        assert resp.status_code == 422

    def test_the_endpoint_needs_the_token(self, client, db_session):
        req = self._accepted(client, db_session)
        resp = client.post(
            f"/intake/events/{req.public_number}/written", json={"calendar_uid": "X"}
        )
        assert resp.status_code == 404


class TestEventTimesAreFloating:
    """An event's time is a wall-clock time, not a UTC instant.

    These served as `...T15:00:00Z` while being stored naive, so a client
    that honoured the suffix would have booked a 3pm seminar at 10am.
    """

    def test_pending_times_carry_no_zone_suffix(self, client, db_session):
        post(client, [event_item()])
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "seminar@example.com")
        )
        state_machine.accept_and_queue(db_session, req)
        db_session.commit()
        row = client.get("/intake/events/pending", headers=auth()).json()["items"][0]
        assert row["starts_at"] == "2026-11-30T15:00:00"
        assert row["ends_at"] == "2026-11-30T16:00:00"
        assert not row["starts_at"].endswith("Z")

    def test_updated_at_still_carries_z(self, client):
        """The audit timestamps really are UTC; only event times changed."""
        post(client, [event_item()])
        row = client.get("/intake/requests/status", headers=auth()).json()["items"][0]
        assert row["updated_at"].endswith("Z")
