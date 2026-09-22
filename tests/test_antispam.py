import re
import time

from app.antispam import FORM_RENDERED_AT_FIELD, HONEYPOT_FIELD, is_spam
from app.config import get_settings


def test_is_spam_true_when_honeypot_filled():
    assert is_spam("http://spam.example", str(time.time() - 10)) is True


def test_is_spam_true_when_submitted_too_fast(monkeypatch):
    monkeypatch.setattr(get_settings(), "min_form_fill_seconds", 3.0)
    assert is_spam("", str(time.time())) is True


def test_is_spam_false_for_legitimate_submission(monkeypatch):
    monkeypatch.setattr(get_settings(), "min_form_fill_seconds", 3.0)
    assert is_spam("", str(time.time() - 10)) is False


def test_is_spam_true_for_missing_or_malformed_timestamp():
    assert is_spam("", "") is True
    assert is_spam("", "not-a-number") is True


def test_honeypot_field_silently_rejects_submission(client):
    resp = client.get("/")
    csrf = re.search(r'name="csrf_token" value="([^"]*)"', resp.text).group(1)
    rendered_at = re.search(rf'name="{FORM_RENDERED_AT_FIELD}" value="([^"]*)"', resp.text).group(1)

    resp = client.post(
        "/",
        data={
            "csrf_token": csrf,
            FORM_RENDERED_AT_FIELD: rendered_at,
            HONEYPOT_FIELD: "I am a bot",
            "requester_name": "Bot",
            "requester_email": "bot@example.com",
            "title": "spam",
            "description": "spam",
            "request_class": "quick",
            "confirmation": "on",
        },
        follow_redirects=False,
    )
    # Redirects back to the form without creating anything (no /r/<token> location).
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    dash_check = client.get("/dashboard", follow_redirects=False)
    assert dash_check.status_code == 303  # not logged in; just confirms no crash


def test_submission_missing_csrf_token_is_rejected(client):
    resp = client.get("/")
    rendered_at = re.search(rf'name="{FORM_RENDERED_AT_FIELD}" value="([^"]*)"', resp.text).group(1)
    resp = client.post(
        "/",
        data={
            FORM_RENDERED_AT_FIELD: rendered_at,
            "requester_name": "Eve",
            "requester_email": "eve@example.com",
            "title": "test",
            "description": "test",
            "request_class": "quick",
            "confirmation": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 403
