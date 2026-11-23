"""Regression tests for one-shot flash messages.

These existed uncovered and were silently broken: Starlette's session only
writes a new cookie when it is marked modified, and the old
`setdefault("flashes", []).append(...)` stopped marking it as soon as the
key existed — which, because every rendered page re-set the key, was from
the first page view onwards. Every flash in the app after that point was
dropped. See app/flash.py.
"""

import re

from tests.conftest import login


def _csrf(client, url):
    return re.search(r'name="csrf_token" value="([^"]*)"', client.get(url).text).group(1)


def test_a_flash_survives_a_redirect(client):
    login(client)
    csrf = _csrf(client, "/settings")
    resp = client.post(
        "/settings",
        data={
            "csrf_token": csrf,
            "owner_display_name": "Chris",
            "quick_action_minutes": "10",
            "urgency_green_days": "7",
            "urgency_red_days": "2",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Settings saved." in client.get("/settings").text


def test_a_flash_is_shown_only_once(client):
    login(client)
    csrf = _csrf(client, "/settings")
    client.post(
        "/settings",
        data={
            "csrf_token": csrf,
            "owner_display_name": "Chris",
            "quick_action_minutes": "10",
            "urgency_green_days": "7",
            "urgency_red_days": "2",
        },
        follow_redirects=False,
    )
    assert "Settings saved." in client.get("/settings").text
    assert "Settings saved." not in client.get("/settings").text


def test_several_flashes_all_survive(client):
    from app.flash import flash, pop_flashes

    class FakeRequest:
        def __init__(self):
            self.session = {}

    req = FakeRequest()
    flash(req, "first", "success")
    flash(req, "second", "error")
    assert [f["message"] for f in pop_flashes(req)] == ["first", "second"]
    assert pop_flashes(req) == []


def test_popping_nothing_does_not_touch_the_session(client):
    from app.flash import pop_flashes

    class FakeRequest:
        def __init__(self):
            self.session = {}

    req = FakeRequest()
    assert pop_flashes(req) == []
    # No key written: an ordinary page view must not mark the session
    # modified and re-issue the cookie on every response.
    assert req.session == {}
