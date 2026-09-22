import re

from app.antispam import FORM_RENDERED_AT_FIELD, HONEYPOT_FIELD


def _get_submit_form_tokens(client):
    resp = client.get("/")
    csrf = re.search(r'name="csrf_token" value="([^"]*)"', resp.text).group(1)
    rendered_at = re.search(rf'name="{FORM_RENDERED_AT_FIELD}" value="([^"]*)"', resp.text).group(1)
    return csrf, rendered_at


def _valid_payload(csrf, rendered_at, **overrides):
    payload = {
        "csrf_token": csrf,
        FORM_RENDERED_AT_FIELD: rendered_at,
        HONEYPOT_FIELD: "",
        "requester_name": "Alice",
        "requester_email": "alice@example.com",
        "title": "Review manuscript introduction",
        "description": "Review sections 1-2 and tell me if the argument is clear.",
        "additional_context": "",
        "request_class": "long",
        "desired_completion_date": "",
        "desired_date_reason": "",
        "confirmation": "on",
    }
    payload.update(overrides)
    return payload


def test_submit_long_action_creates_submitted_request(client):
    csrf, rendered_at = _get_submit_form_tokens(client)
    resp = client.post("/", data=_valid_payload(csrf, rendered_at), follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/r/")

    track = client.get(resp.headers["location"])
    assert "Submitted" in track.text
    assert "awaiting review" in track.text


def test_submit_quick_action_is_queued_immediately(client):
    csrf, rendered_at = _get_submit_form_tokens(client)
    resp = client.post(
        "/", data=_valid_payload(csrf, rendered_at, request_class="quick"), follow_redirects=False
    )
    assert resp.status_code == 303
    track = client.get(resp.headers["location"])
    assert "Queued" in track.text


def test_submit_requires_confirmation(client):
    csrf, rendered_at = _get_submit_form_tokens(client)
    resp = client.post("/", data=_valid_payload(csrf, rendered_at, confirmation=""), follow_redirects=False)
    assert resp.status_code == 422
    assert "confirm" in resp.text.lower()


def test_submit_requires_reason_when_date_given(client):
    csrf, rendered_at = _get_submit_form_tokens(client)
    resp = client.post(
        "/",
        data=_valid_payload(csrf, rendered_at, desired_completion_date="2026-12-01", desired_date_reason=""),
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "date" in resp.text.lower()


def test_submit_rejects_missing_description(client):
    csrf, rendered_at = _get_submit_form_tokens(client)
    resp = client.post("/", data=_valid_payload(csrf, rendered_at, description=""), follow_redirects=False)
    assert resp.status_code == 422


def test_tracking_page_hides_other_requesters(client):
    csrf, rendered_at = _get_submit_form_tokens(client)
    client.post("/", data=_valid_payload(csrf, rendered_at, requester_name="Alice"), follow_redirects=False)

    csrf2, rendered_at2 = _get_submit_form_tokens(client)
    resp = client.post(
        "/", data=_valid_payload(csrf2, rendered_at2, requester_name="Bob"), follow_redirects=False
    )
    track = client.get(resp.headers["location"])
    assert "Alice" not in track.text


def test_head_request_to_submission_form_returns_empty_body(client):
    resp = client.request("HEAD", "/")
    assert resp.status_code == 200
    assert resp.content == b""
    assert int(resp.headers["content-length"]) > 0


def test_head_request_to_tracking_page(client):
    csrf, rendered_at = _get_submit_form_tokens(client)
    created = client.post("/", data=_valid_payload(csrf, rendered_at), follow_redirects=False)
    tracking_url = created.headers["location"]

    resp = client.request("HEAD", tracking_url)
    assert resp.status_code == 200
    assert resp.content == b""


def test_head_request_to_unknown_tracking_token_still_404s(client):
    resp = client.request("HEAD", "/r/does-not-exist")
    assert resp.status_code == 404
