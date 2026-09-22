"""Exercises the real HTTP routes and verifies each triggers the right
notification with the right recipient/content — patched at app.email.send_email
(the transport boundary) so no real network call happens, but everything
above that (route wiring, notifications.py's message composition, the
db.refresh-after-commit fix) runs for real.

Every test patches send_email around its ENTIRE body, not just the action
being asserted on — with email enabled, setup steps like submit_request()
also trigger a real notify_requester_received() call, and leaving that
unpatched means a genuine (if doomed-to-fail) network connection attempt to
smtp.gmail.com on every test run. mock_send.reset_mock() is used right
before the action under test so setup-call noise doesn't affect the
assertions.
"""

import re
from unittest.mock import patch

from tests.conftest import antispam_field, login, submit_request


def _csrf_from(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]*)"', html).group(1)


def _request_id_from_dashboard(client, title: str) -> int:
    resp = client.get("/dashboard")
    pattern = rf'/requests/(\d+)"(?:(?!/requests/).)*?class="row-title[^"]*">{re.escape(title)}'
    match = re.search(pattern, resp.text, re.S)
    assert match, f"could not find {title!r} on dashboard"
    return int(match.group(1))


def _enable_email(monkeypatch, owner_email="owner-inbox@example.com"):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "owner_notification_email", owner_email)


def test_submission_notifies_requester_and_owner(client, monkeypatch):
    _enable_email(monkeypatch)
    with patch("app.notifications.send_email") as mock_send:
        submit_request(client, title="Review manuscript", requester_email="alice@example.com")

    assert mock_send.call_count == 2
    recipients = {call.args[0] for call in mock_send.call_args_list}
    assert recipients == {"alice@example.com", "owner-inbox@example.com"}

    requester_call = next(c for c in mock_send.call_args_list if c.args[0] == "alice@example.com")
    assert "Review manuscript" in requester_call.args[1]

    owner_call = next(c for c in mock_send.call_args_list if c.args[0] == "owner-inbox@example.com")
    assert "alice@example.com" in owner_call.args[2]


def test_submission_skips_owner_email_when_not_configured(client, monkeypatch):
    _enable_email(monkeypatch, owner_email="")
    with patch("app.notifications.send_email") as mock_send:
        submit_request(client, title="No owner ping", requester_email="bob@example.com")

    assert mock_send.call_count == 1
    assert mock_send.call_args_list[0].args[0] == "bob@example.com"


def test_no_smtp_connection_attempted_when_not_configured(client):
    # Default test env has no SMTP credentials (see conftest.py). The
    # notification composers still run (that's correct — the actual gate
    # lives inside app.email.send_email's email_enabled check), so this
    # mocks at the real transport boundary, same as test_email.py, rather
    # than mocking send_email itself which would bypass that guard.
    with patch("app.email.smtplib.SMTP") as mock_smtp:
        submit_request(client, title="Should not email anyone")
    mock_smtp.assert_not_called()


def test_accept_notifies_requester(client, monkeypatch):
    _enable_email(monkeypatch, owner_email="")
    with patch("app.notifications.send_email") as mock_send:
        login(client)
        submit_request(client, title="Needs acceptance", requester_email="carol@example.com")
        req_id = _request_id_from_dashboard(client, "Needs acceptance")

        detail = client.get(f"/requests/{req_id}")
        csrf = _csrf_from(detail.text)
        mock_send.reset_mock()
        client.post(f"/requests/{req_id}/accept", data={"csrf_token": csrf}, follow_redirects=False)

    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == "carol@example.com"
    assert "accepted" in mock_send.call_args.args[1].lower()


def test_decline_notifies_requester_with_reason(client, monkeypatch):
    _enable_email(monkeypatch, owner_email="")
    with patch("app.notifications.send_email") as mock_send:
        login(client)
        submit_request(client, title="Needs decline", requester_email="dave@example.com")
        req_id = _request_id_from_dashboard(client, "Needs decline")

        detail = client.get(f"/requests/{req_id}")
        csrf = _csrf_from(detail.text)
        mock_send.reset_mock()
        client.post(
            f"/requests/{req_id}/decline",
            data={"csrf_token": csrf, "reason": "Not in scope"},
            follow_redirects=False,
        )

    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == "dave@example.com"
    assert "Not in scope" in mock_send.call_args.args[2]


def test_request_info_notifies_requester_with_question(client, monkeypatch):
    _enable_email(monkeypatch, owner_email="")
    with patch("app.notifications.send_email") as mock_send:
        login(client)
        submit_request(client, title="Needs info", requester_email="erin@example.com")
        req_id = _request_id_from_dashboard(client, "Needs info")

        detail = client.get(f"/requests/{req_id}")
        csrf = _csrf_from(detail.text)
        mock_send.reset_mock()
        client.post(
            f"/requests/{req_id}/request-info",
            data={"csrf_token": csrf, "question": "What is the target venue?"},
            follow_redirects=False,
        )

    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == "erin@example.com"
    assert "target venue" in mock_send.call_args.args[2]


def test_complete_notifies_requester(client, monkeypatch):
    _enable_email(monkeypatch, owner_email="")
    with patch("app.notifications.send_email") as mock_send:
        login(client)
        submit_request(client, title="Needs completion", requester_email="frank@example.com", request_class="quick")
        req_id = _request_id_from_dashboard(client, "Needs completion")

        detail = client.get(f"/requests/{req_id}")
        csrf = _csrf_from(detail.text)
        mock_send.reset_mock()
        client.post(f"/requests/{req_id}/complete", data={"csrf_token": csrf}, follow_redirects=False)

    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == "frank@example.com"
    assert "completed" in mock_send.call_args.args[1].lower()


def test_requester_response_notifies_owner(client, monkeypatch):
    _enable_email(monkeypatch, owner_email="owner-inbox@example.com")
    with patch("app.notifications.send_email") as mock_send:
        login(client)
        tracking_url = submit_request(client, title="Roundtrip", requester_email="grace@example.com")
        req_id = _request_id_from_dashboard(client, "Roundtrip")

        detail = client.get(f"/requests/{req_id}")
        csrf = _csrf_from(detail.text)
        client.post(
            f"/requests/{req_id}/request-info",
            data={"csrf_token": csrf, "question": "Which dataset?"},
            follow_redirects=False,
        )

        track = client.get(tracking_url)
        csrf2 = _csrf_from(track.text)
        mock_send.reset_mock()
        client.post(
            f"{tracking_url}/respond",
            data={**antispam_field(), "csrf_token": csrf2, "response": "The 2024 dataset."},
            follow_redirects=False,
        )

    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == "owner-inbox@example.com"
    assert "The 2024 dataset." in mock_send.call_args.args[2]
