import re

from tests.conftest import login, submit_request


def _csrf_from(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]*)"', html).group(1)


def _request_id_from_dashboard(client, title: str) -> int:
    """Finds the request id whose row (not some other row) contains `title`.

    The `(?:(?!/requests/).)*?` keeps the reluctant match from skipping past
    this row's own title and latching onto a different row's href.
    """
    resp = client.get("/dashboard")
    pattern = rf'/requests/(\d+)"(?:(?!/requests/).)*?class="row-title[^"]*">{re.escape(title)}'
    match = re.search(pattern, resp.text, re.S)
    assert match, f"could not find {title!r} on dashboard"
    return int(match.group(1))


def test_dashboard_requires_login(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_with_wrong_password_fails(client):
    resp = client.get("/login")
    csrf = _csrf_from(resp.text)
    resp = client.post(
        "/login", data={"csrf_token": csrf, "username": "owner", "password": "wrong"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    dash = client.get("/dashboard", follow_redirects=False)
    assert dash.status_code == 303  # still not authenticated


def test_full_long_action_lifecycle(client):
    login(client)
    tracking_url = submit_request(client, title="Review proposal draft")
    req_id = _request_id_from_dashboard(client, "Review proposal draft")

    detail = client.get(f"/requests/{req_id}")
    assert "Accept &amp; Queue" in detail.text or "Accept" in detail.text
    csrf = _csrf_from(detail.text)

    resp = client.post(f"/requests/{req_id}/accept", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    detail = client.get(f"/requests/{req_id}")
    assert "status-queued" in detail.text
    csrf = _csrf_from(detail.text)

    resp = client.post(f"/requests/{req_id}/start", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    detail = client.get(f"/requests/{req_id}")
    assert "status-in_progress" in detail.text
    csrf = _csrf_from(detail.text)

    resp = client.post(
        f"/requests/{req_id}/complete", data={"csrf_token": csrf, "note": "Done well"}, follow_redirects=False
    )
    assert resp.status_code == 303

    detail = client.get(f"/requests/{req_id}")
    assert "status-done" in detail.text
    assert "Done well" in detail.text

    tracking = client.get(tracking_url)
    assert "Completed" in tracking.text


def test_decline_requires_reason_and_shows_on_tracking_page(client):
    login(client)
    tracking_url = submit_request(client, title="A questionable request")
    req_id = _request_id_from_dashboard(client, "A questionable request")

    detail = client.get(f"/requests/{req_id}")
    csrf = _csrf_from(detail.text)
    resp = client.post(
        f"/requests/{req_id}/decline",
        data={"csrf_token": csrf, "reason": "Out of scope for me"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    tracking = client.get(tracking_url)
    assert "Declined" in tracking.text
    assert "Out of scope for me" in tracking.text


def test_queue_reorder_via_move_up_down(client):
    login(client)
    submit_request(client, title="First in queue")
    submit_request(client, title="Second in queue")

    for title in ("First in queue", "Second in queue"):
        req_id = _request_id_from_dashboard(client, title)
        detail = client.get(f"/requests/{req_id}")
        csrf = _csrf_from(detail.text)
        client.post(f"/requests/{req_id}/accept", data={"csrf_token": csrf}, follow_redirects=False)

    dash = client.get("/dashboard")
    assert dash.text.index("First in queue") < dash.text.index("Second in queue")

    second_id = _request_id_from_dashboard(client, "Second in queue")
    detail = client.get(f"/requests/{second_id}")
    csrf = _csrf_from(detail.text)
    client.post(f"/requests/{second_id}/move-up", data={"csrf_token": csrf}, follow_redirects=False)

    dash = client.get("/dashboard")
    assert dash.text.index("Second in queue") < dash.text.index("First in queue")


def test_quick_action_session_processes_and_advances(client):
    login(client)
    submit_request(client, title="Sign form A", request_class="quick")
    submit_request(client, title="Sign form B", request_class="quick")

    session = client.get("/quick-session")
    assert "Sign form A" in session.text
    csrf = _csrf_from(session.text)
    req_id = re.search(r"/quick-session/(\d+)/done", session.text).group(1)

    resp = client.post(f"/quick-session/{req_id}/done", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    session = client.get("/quick-session")
    assert "Sign form B" in session.text
    assert "Sign form A" not in session.text


def test_move_quick_action_to_long_actions(client):
    login(client)
    submit_request(client, title="Misclassified quick task", request_class="quick")
    req_id = _request_id_from_dashboard(client, "Misclassified quick task")

    detail = client.get(f"/requests/{req_id}")
    csrf = _csrf_from(detail.text)
    resp = client.post(f"/requests/{req_id}/move-to-long", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    detail = client.get(f"/requests/{req_id}")
    assert "status-queued" in detail.text
    assert "Long Action" in detail.text
