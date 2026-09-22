import re

from tests.conftest import antispam_field, login, submit_request


def _csrf_from(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]*)"', html).group(1)


def _request_id_from_dashboard(client, title: str) -> int:
    resp = client.get("/dashboard")
    pattern = rf'/requests/(\d+)"(?:(?!/requests/).)*?class="row-title[^"]*">{re.escape(title)}'
    match = re.search(pattern, resp.text, re.S)
    assert match, f"could not find {title!r} on dashboard"
    return int(match.group(1))


def test_requester_can_add_information(client):
    tracking_url = submit_request(client, title="Needs more docs")
    track = client.get(tracking_url)
    csrf = _csrf_from(track.text)

    resp = client.post(
        f"{tracking_url}/add-information",
        data={**antispam_field(), "csrf_token": csrf, "content": "Here is the missing spreadsheet link."},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    track = client.get(tracking_url)
    assert "Here is the missing spreadsheet link." in track.text


def test_needs_information_round_trip_restores_queue_position(client):
    login(client)
    submit_request(client, title="Needs info request")
    req_id = _request_id_from_dashboard(client, "Needs info request")

    detail = client.get(f"/requests/{req_id}")
    csrf = _csrf_from(detail.text)
    client.post(f"/requests/{req_id}/accept", data={"csrf_token": csrf}, follow_redirects=False)

    detail = client.get(f"/requests/{req_id}")
    csrf = _csrf_from(detail.text)
    resp = client.post(
        f"/requests/{req_id}/request-info",
        data={"csrf_token": csrf, "question": "What is the target audience?"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    detail = client.get(f"/requests/{req_id}")
    assert "status-needs_information" in detail.text
    tracking_url = re.search(r'href="(/r/[^"]+)"', detail.text).group(1)

    track = client.get(tracking_url)
    assert "What is the target audience?" in track.text
    csrf = _csrf_from(track.text)
    resp = client.post(
        f"{tracking_url}/respond",
        data={**antispam_field(), "csrf_token": csrf, "response": "Graduate students."},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    detail = client.get(f"/requests/{req_id}")
    assert "status-queued" in detail.text


def test_requester_can_withdraw(client):
    tracking_url = submit_request(client, title="Changed my mind")
    track = client.get(tracking_url)
    csrf = _csrf_from(track.text)

    resp = client.post(
        f"{tracking_url}/withdraw",
        data={"csrf_token": csrf, "confirm": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    track = client.get(tracking_url)
    assert "Withdrawn" in track.text


def test_withdraw_requires_confirmation_checkbox(client):
    tracking_url = submit_request(client, title="Still deciding")
    track = client.get(tracking_url)
    csrf = _csrf_from(track.text)

    client.post(f"{tracking_url}/withdraw", data={"csrf_token": csrf}, follow_redirects=False)

    track = client.get(tracking_url)
    assert "Withdrawn" not in track.text


def test_unknown_token_returns_404(client):
    resp = client.get("/r/does-not-exist")
    assert resp.status_code == 404


def test_add_information_rejected_on_withdrawn_request(client):
    tracking_url = submit_request(client, title="Already withdrawn")
    # The CSRF token is session-scoped, not form-scoped, so it's still valid
    # to reuse after withdrawal even though the UI stops rendering the
    # "Add information" form for a closed request (track.html hides it) —
    # this test is deliberately bypassing the UI to exercise the backend
    # guard directly, the same way a still-held stale link could.
    csrf = _csrf_from(client.get(tracking_url).text)
    client.post(
        f"{tracking_url}/withdraw", data={"csrf_token": csrf, "confirm": "on"}, follow_redirects=False
    )
    assert "Withdrawn" in client.get(tracking_url).text

    resp = client.post(
        f"{tracking_url}/add-information",
        data={**antispam_field(), "csrf_token": csrf, "content": "Still trying to add more info."},
        follow_redirects=False,
    )
    assert resp.status_code == 409

    track = client.get(tracking_url)
    assert "Still trying to add more info." not in track.text


def test_add_information_honeypot_silently_ignored(client):
    from app.antispam import HONEYPOT_FIELD

    tracking_url = submit_request(client, title="Bot target")
    csrf = _csrf_from(client.get(tracking_url).text)

    resp = client.post(
        f"{tracking_url}/add-information",
        data={
            **antispam_field(),
            "csrf_token": csrf,
            "content": "Spam content",
            HONEYPOT_FIELD: "I am a bot",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303  # silently "succeeds" without actually doing anything

    track = client.get(tracking_url)
    assert "Spam content" not in track.text
