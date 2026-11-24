"""Tests for the server-side insert path (app/intake.py + scripts/add_request.py).

The load-bearing property is the absence of a notification: this feeds an
inbox-clearing workflow, so creating a request must not mail the Owner's
Inbox. `send_email` is patched at the transport boundary (as in
tests/test_notifications_integration.py) and asserted never to be called.
"""

from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.intake import IntakeError, Link, create_request
from app.models import (
    EventType,
    Request as RequestModel,
    RequestClass,
    RequestEvent,
    RequestSource,
    RequestStatus,
)
from app.state_machine import accept_and_queue, decline


def _enable_email(monkeypatch, owner_email="owner-inbox@example.com"):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "owner_notification_email", owner_email)


def _create(db_session, **overrides) -> RequestModel:
    kwargs = dict(
        title="Reconcile October PCard statement",
        description="Match receipts, code the lines, submit for approval.",
        request_class=RequestClass.long,
        source=RequestSource.email,
    )
    kwargs.update(overrides)
    req = create_request(db_session, **kwargs)
    db_session.commit()
    return req


class TestNoNotification:
    def test_create_request_sends_nothing_even_with_email_enabled(self, db_session, monkeypatch):
        _enable_email(monkeypatch)
        with patch("app.email.send_email") as mock_send:
            _create(db_session)
        assert mock_send.call_count == 0

    def test_intake_does_not_depend_on_the_notification_layer(self):
        # Structural, not behavioural: nothing in app.intake can fire a
        # notification because it never reaches the module that sends them.
        import app.intake

        source = __import__("inspect").getsource(app.intake)
        assert "notifications" not in source
        assert "send_email" not in source

    def test_accepting_a_captured_request_notifies_nobody(self, db_session, monkeypatch):
        _enable_email(monkeypatch)
        req = _create(db_session)
        with patch("app.notifications.send_email") as mock_send:
            from app.notifications import notify_requester_accepted
            from app.models import AppSettings

            notify_requester_accepted("http://testserver/", req, AppSettings())
        assert mock_send.call_count == 0


class TestCreatedShape:
    def test_lands_at_submitted_for_review(self, db_session):
        req = _create(db_session)
        assert req.status == RequestStatus.submitted
        assert req.source == RequestSource.email
        assert req.public_number.startswith("REQ-")
        assert req.access_token

    def test_quick_actions_also_land_at_submitted(self, db_session):
        req = _create(db_session, request_class=RequestClass.quick)
        assert req.status == RequestStatus.submitted

    def test_has_no_requester_by_default(self, db_session):
        req = _create(db_session)
        assert req.requester_name is None
        assert req.requester_email is None

    def test_logs_a_submitted_event(self, db_session):
        req = _create(db_session)
        events = db_session.scalars(
            select(RequestEvent).where(RequestEvent.request_id == req.id)
        ).all()
        assert [e.event_type for e in events] == [EventType.submitted]

    def test_stores_the_effective_date_and_its_reason(self, db_session):
        req = _create(
            db_session,
            desired_completion_date=date(2026, 11, 25),
            desired_date_reason="Stated Nov 28; 3 business days earlier, an approval follows.",
        )
        assert req.desired_completion_date == date(2026, 11, 25)
        assert "Nov 28" in req.desired_date_reason

    def test_stores_links_including_non_http_schemes(self, db_session):
        # The public form rejects anything but http(s); a captured email
        # must be able to keep the message:// URL it came from.
        req = _create(
            db_session,
            links=[Link(url="message://%3Cabc@uwo.ca%3E", label="Originating email")],
        )
        assert [(link.label, link.url) for link in req.links] == [
            ("Originating email", "message://%3Cabc@uwo.ca%3E")
        ]

    def test_accepts_zero_links(self, db_session):
        assert _create(db_session).links == []

    def test_public_numbers_do_not_collide(self, db_session):
        first = _create(db_session)
        second = _create(db_session, title="Book exam scanning")
        assert first.public_number != second.public_number


class TestReviewStep:
    def test_a_captured_long_action_can_be_accepted(self, db_session):
        req = _create(db_session)
        accept_and_queue(db_session, req)
        assert req.status == RequestStatus.queued
        assert req.queue_position == 1

    def test_a_captured_quick_action_can_be_accepted(self, db_session):
        req = _create(db_session, request_class=RequestClass.quick)
        accept_and_queue(db_session, req)
        assert req.status == RequestStatus.queued
        assert req.request_class == RequestClass.quick

    def test_a_captured_request_can_be_declined(self, db_session):
        req = _create(db_session)
        decline(db_session, req, "Already handled.")
        assert req.status == RequestStatus.declined


class TestValidation:
    def test_rejects_the_form_source(self, db_session):
        with pytest.raises(IntakeError):
            _create(db_session, source=RequestSource.form)

    def test_requires_a_title(self, db_session):
        with pytest.raises(IntakeError):
            _create(db_session, title="   ")

    def test_requires_a_description(self, db_session):
        with pytest.raises(IntakeError):
            _create(db_session, description="")

    def test_requires_a_reason_alongside_a_date(self, db_session):
        with pytest.raises(IntakeError):
            _create(db_session, desired_completion_date=date(2026, 12, 1))

    def test_rejects_a_reason_with_no_date(self, db_session):
        with pytest.raises(IntakeError):
            _create(db_session, desired_date_reason="Because.")

    def test_rejects_a_link_with_no_url(self, db_session):
        with pytest.raises(IntakeError):
            _create(db_session, links=[Link(url="  ")])

    def test_rejects_an_over_long_title(self, db_session):
        with pytest.raises(IntakeError):
            _create(db_session, title="x" * 301)


class TestScriptCli:
    def test_pairs_links_with_labels_in_order(self):
        from scripts.add_request import _pair_links

        links = _pair_links(["a://1", "b://2"], ["First"])
        assert links == [Link(url="a://1", label="First"), Link(url="b://2", label=None)]

    def test_rejects_more_labels_than_links(self):
        from scripts.add_request import _pair_links

        with pytest.raises(SystemExit):
            _pair_links(["a://1"], ["First", "Second"])

    def test_form_is_not_an_offered_source(self):
        from scripts.add_request import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["--title", "t", "--description", "d", "--class", "long", "--source", "form"]
            )

    def test_end_to_end_run_creates_the_request(self, engine, db_session, monkeypatch, capsys):
        # Runs main() for real against the test database. This covers the
        # part unit-testing create_request can't: main() prints a summary
        # after committing, and SessionLocal expires every attribute on
        # commit, so reading them at the wrong moment raises
        # DetachedInstanceError rather than printing anything.
        import scripts.add_request as script
        from sqlalchemy.orm import sessionmaker

        monkeypatch.setattr(
            script, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine)
        )
        exit_code = script.main(
            [
                "--title", "Book exam scanning",
                "--description", "Email the booking form to the exam office.",
                "--class", "quick",
                "--source", "email",
                "--date", "2026-12-01",
                "--date-reason", "Stated Dec 3; 2 business days earlier, a third party schedules it.",
                "--link", "message://%3Cabc@uwo.ca%3E",
                "--link-label", "Originating email",
            ]
        )
        assert exit_code == 0
        assert "Book exam scanning" in capsys.readouterr().out

        req = db_session.scalar(select(RequestModel).where(RequestModel.title == "Book exam scanning"))
        assert req.source == RequestSource.email
        assert req.status == RequestStatus.submitted
        assert req.request_class == RequestClass.quick
        assert req.desired_completion_date == date(2026, 12, 1)
        assert [link.url for link in req.links] == ["message://%3Cabc@uwo.ca%3E"]

    def test_end_to_end_run_reports_a_validation_failure(self, engine, monkeypatch, capsys):
        import scripts.add_request as script
        from sqlalchemy.orm import sessionmaker

        monkeypatch.setattr(
            script, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine)
        )
        exit_code = script.main(
            ["--title", "t", "--description", "d", "--class", "long", "--source", "email",
             "--date", "2026-12-01"]
        )
        assert exit_code == 1
        assert "reason" in capsys.readouterr().err


class TestDecliningACapturedRequest:
    """Declining is an expected outcome of triage, not an exception.

    A captured request has nobody on the other end, so requiring a written
    justification is friction on a path that has to stay fast. The rule
    keys on `requester_email` — whether there is an audience — rather than
    on `source`, which would give the same answer today for the wrong
    reason.
    """

    def test_no_reason_is_needed(self, db_session):
        req = _create(db_session)
        decline(db_session, req)
        assert req.status == RequestStatus.declined
        assert req.decline_reason is None

    def test_it_matches_cancel_rather_than_contradicting_it(self, db_session):
        from app.state_machine import cancel

        declined = _create(db_session)
        cancelled = _create(db_session, title="Another item")
        decline(db_session, declined)
        cancel(db_session, cancelled)
        # Two closing paths on the same dashboard, same rule about reasons.
        assert (declined.decline_reason, cancelled.cancel_reason) == (None, None)

    def test_the_form_asks_for_an_optional_reason(self, client, db_session):
        from tests.conftest import login

        req = _create(db_session)
        login(client)
        page = client.get(f"/requests/{req.id}").text
        assert "Reason (optional)" in page
        assert "Reason (shown to the requester)" not in page

    def test_the_route_accepts_an_empty_reason(self, client, db_session):
        import re

        from tests.conftest import login

        req = _create(db_session)
        login(client)
        csrf = re.search(
            r'name="csrf_token" value="([^"]*)"', client.get(f"/requests/{req.id}").text
        ).group(1)
        resp = client.post(
            f"/requests/{req.id}/decline",
            data={"csrf_token": csrf, "reason": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        db_session.expire_all()
        assert db_session.scalar(select(RequestModel)).status == RequestStatus.declined

    def test_declining_a_captured_request_mails_nobody(self, client, db_session, monkeypatch):
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
        monkeypatch.setattr(settings, "smtp_password", "app-password")

        req = _create(db_session)
        with patch("app.email.send_email") as mock_send:
            decline(db_session, req)
        assert mock_send.call_count == 0
