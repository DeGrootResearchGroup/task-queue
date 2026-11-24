"""Tests for correcting a captured request in place.

Capture used to be one-way: dedupe_key is permanent and a second POST
reports `duplicate`, so an item captured with a wrong or missing date had
no route back except the Owner editing it by hand. These cover the three
things that must stay true of the way back in -- it cannot reach his
decisions, it cannot reopen a closed one, and it leaves a trace.
"""

from sqlalchemy import select

from app import state_machine
from app.models import Request as RequestModel, RequestEvent

TOKEN = "test-intake-token"
URL = "/intake/requests/correct"


def auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def capture(client, **overrides):
    data = {
        "title": "WE Monthly Coffee Hour",
        "description": "Strategic pillar session with the Dean.",
        "request_class": "quick",
        "source": "email",
        "dedupe_key": "coffee@example.com",
    }
    data.update(overrides)
    return client.post("/intake/requests", json={"items": [data]}, headers=auth())


def correct(client, key="coffee@example.com", **body):
    body.setdefault("note", "Re-read the body.")
    return client.post(f"{URL}?dedupe_key={key}", json=body, headers=auth())


class TestTheGap:
    def test_a_date_can_be_added_after_capture(self, client, db_session):
        """The case that prompted this: captured with no date, and the real
        session turned out to be the next day."""
        capture(client)
        resp = correct(
            client,
            desired_completion_date="2026-11-25",
            desired_date_reason="Body gives the next session as 25 November.",
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "corrected"
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "coffee@example.com")
        )
        db_session.refresh(req)
        assert req.desired_completion_date.isoformat() == "2026-11-25"

    def test_a_wrong_title_can_be_replaced(self, client, db_session):
        capture(client)
        correct(client, title="WE Monthly Coffee Hour - Nov pillar session")
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "coffee@example.com")
        )
        db_session.refresh(req)
        assert req.title.endswith("Nov pillar session")

    def test_an_unknown_key_is_not_found(self, client):
        assert correct(client, key="nope@example.com", title="x").status_code == 404


class TestWhatItMustNotReach:
    def test_it_cannot_set_status(self, client):
        capture(client)
        assert correct(client, status="queued").status_code == 422

    def test_it_cannot_set_the_class_or_queue_position(self, client):
        capture(client)
        assert correct(client, request_class="long").status_code == 422
        assert correct(client, queue_position=1).status_code == 422

    def test_it_refuses_a_closed_request(self, client, db_session):
        """Re-dating something he has declined would quietly reopen a
        decision he already made."""
        capture(client)
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "coffee@example.com")
        )
        state_machine.decline(db_session, req)
        db_session.commit()
        resp = correct(client, desired_completion_date="2026-12-01",
                       desired_date_reason="too late")
        assert resp.status_code == 409

    def test_a_date_still_needs_a_reason(self, client):
        capture(client)
        assert correct(client, desired_completion_date="2026-11-25").status_code == 422

    def test_it_needs_the_token(self, client):
        capture(client)
        resp = client.post(f"{URL}?dedupe_key=coffee@example.com", json={"note": "x", "title": "y"})
        assert resp.status_code == 404

    def test_a_note_is_required(self, client):
        capture(client)
        resp = client.post(
            f"{URL}?dedupe_key=coffee@example.com", json={"title": "y"}, headers=auth()
        )
        assert resp.status_code == 422


class TestTrace:
    def test_the_change_is_recorded_on_the_timeline(self, client, db_session):
        capture(client)
        correct(client, title="Corrected title", note="Body said otherwise.")
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "coffee@example.com")
        )
        ev = db_session.scalars(
            select(RequestEvent).where(RequestEvent.request_id == req.id)
        ).all()[-1]
        meta = ev.event_metadata
        assert meta["note"] == "Body said otherwise."
        assert meta["changed"]["title"]["to"] == "Corrected title"
        assert meta["changed"]["title"]["from"] == "WE Monthly Coffee Hour"

    def test_an_identical_value_is_not_a_change(self, client):
        capture(client)
        resp = correct(client, title="WE Monthly Coffee Hour")
        assert resp.json()["status"] == "unchanged"


class TestDoubleSend:
    def test_a_second_copy_of_one_announcement_is_flagged(self, client):
        """Two real copies of one mailing are two message-ids, so dedupe_key
        cannot see them. Reported, never refused."""
        capture(client)
        resp = capture(client, dedupe_key="coffee-copy2@example.com")
        row = resp.json()["results"][0]
        assert row["status"] == "created"
        assert "possible_double_send" in row

    def test_a_different_title_is_not_flagged(self, client):
        capture(client)
        resp = capture(client, title="Something else", dedupe_key="other@example.com")
        assert "possible_double_send" not in resp.json()["results"][0]


class TestReasonWithoutDate:
    def test_a_reason_with_no_date_is_accepted(self, client, db_session):
        """Previously rejected, which forbade exactly what the capture gate
        tells the sweep to record."""
        resp = client.post(
            "/intake/requests",
            json={"items": [{
                "title": "Register for SHARCNET course",
                "description": "Three-session online course on R.",
                "request_class": "quick",
                "source": "email",
                "desired_date_reason": "No stated cutoff; registration closes when full.",
                "dedupe_key": "sharcnet@example.com",
            }]},
            headers=auth(),
        )
        assert resp.json()["created"] == 1
        req = db_session.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == "sharcnet@example.com")
        )
        assert req.desired_completion_date is None
        assert req.desired_date_reason.startswith("No stated cutoff")
