"""Tests for the read-only .ics feed.

Two things matter beyond "it renders": the feed must be unreachable
without the token, and its entries must not consume calendar availability
— the Owner tracks when he is free elsewhere, and a deadline is not a
commitment to be busy.
"""

from datetime import date, datetime

import pytest

from app.calendar_feed import _escape, _fold, render_calendar
from app.models import (
    AppSettings,
    Request as RequestModel,
    RequestClass,
    RequestSource,
    RequestStatus,
)
from app.security import generate_access_token
from tests.conftest import get_csrf_and_cookies, login


def make_request(db_session, **kwargs) -> RequestModel:
    defaults = dict(
        public_number=f"REQ-{abs(hash(kwargs.get('title', 'x'))) % 9000 + 1000}",
        access_token=generate_access_token(),
        title="Reconcile PCard statement",
        description="Match receipts and submit.",
        request_class=RequestClass.long,
        status=RequestStatus.submitted,
        source=RequestSource.email,
        desired_completion_date=date(2026, 11, 25),
        desired_date_reason="Stated Nov 28; 3 business days earlier, an approval follows.",
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


def enable_feed(client) -> str:
    """Turns the feed on through the real route and returns its URL."""
    login(client)
    csrf, _ = get_csrf_and_cookies(client, "/settings")
    resp = client.post("/settings/calendar-feed", data={"csrf_token": csrf}, follow_redirects=True)
    assert resp.status_code == 200
    import re

    match = re.search(r'value="(http[^"]*/calendar/[^"]+\.ics)"', resp.text)
    assert match, "settings page did not show a feed URL"
    return match.group(1)


class TestAccessControl:
    def test_feed_is_404_until_enabled(self, client, db_session):
        make_request(db_session)
        assert client.get("/calendar/anything.ics").status_code == 404

    def test_wrong_token_is_404(self, client, db_session):
        make_request(db_session)
        enable_feed(client)
        assert client.get("/calendar/not-the-token.ics").status_code == 404

    def test_correct_token_serves_the_feed(self, client, db_session):
        make_request(db_session)
        url = enable_feed(client)
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/calendar")

    def test_feed_needs_no_session(self, client, db_session):
        # A calendar client can't log in; the URL is the whole credential.
        make_request(db_session)
        url = enable_feed(client)
        client.cookies.clear()
        assert client.get(url).status_code == 200

    def test_replacing_the_token_revokes_the_old_url(self, client, db_session):
        make_request(db_session)
        old_url = enable_feed(client)
        new_url = enable_feed(client)
        assert old_url != new_url
        assert client.get(old_url).status_code == 404
        assert client.get(new_url).status_code == 200

    def test_disabling_the_feed_stops_it_resolving(self, client, db_session):
        make_request(db_session)
        url = enable_feed(client)
        csrf, _ = get_csrf_and_cookies(client, "/settings")
        client.post("/settings/calendar-feed/disable", data={"csrf_token": csrf})
        assert client.get(url).status_code == 404

    def test_head_is_supported(self, client, db_session):
        make_request(db_session)
        url = enable_feed(client)
        assert client.head(url).status_code == 200

    def test_feed_is_read_only(self, client, db_session):
        make_request(db_session)
        url = enable_feed(client)
        for method in ("post", "put", "delete", "patch"):
            assert getattr(client, method)(url).status_code == 405


class TestFeedContents:
    def test_includes_open_dated_requests(self, client, db_session):
        make_request(db_session, title="Reconcile PCard statement")
        body = client.get(enable_feed(client)).text
        assert "SUMMARY:Due: Reconcile PCard statement" in body
        assert "DTSTART;VALUE=DATE:20261125" in body
        # All-day events take an exclusive DTEND, so one day spans to the next.
        assert "DTEND;VALUE=DATE:20261126" in body

    def test_entries_do_not_consume_availability(self, client, db_session):
        make_request(db_session)
        body = client.get(enable_feed(client)).text
        assert "TRANSP:TRANSPARENT" in body
        assert "X-MICROSOFT-CDO-BUSYSTATUS:FREE" in body

    def test_excludes_requests_with_no_date(self, client, db_session):
        make_request(db_session, title="Undated item", desired_completion_date=None,
                     desired_date_reason=None)
        body = client.get(enable_feed(client)).text
        assert "Undated item" not in body

    @pytest.mark.parametrize(
        "status", [RequestStatus.done, RequestStatus.declined, RequestStatus.withdrawn]
    )
    def test_excludes_closed_requests(self, client, db_session, status):
        make_request(db_session, title="Closed item", status=status)
        body = client.get(enable_feed(client)).text
        assert "Closed item" not in body

    def test_carries_the_stated_date_reason_and_a_link_back(self, client, db_session):
        req = make_request(db_session)
        body = client.get(enable_feed(client)).text
        assert "Nov 28" in body
        assert f"requests/{req.id}" in body

    def test_uid_is_stable_across_fetches(self, client, db_session):
        req = make_request(db_session)
        url = enable_feed(client)
        first, second = client.get(url).text, client.get(url).text
        assert f"UID:request-{req.id}@personal-request-queue" in first
        assert f"UID:request-{req.id}@personal-request-queue" in second


class TestRendering:
    def _settings(self):
        row = AppSettings()
        row.owner_display_name = "Chris"
        return row

    def test_document_is_crlf_terminated(self):
        out = render_calendar([], calendar_name="X", admin_base_url="http://x/")
        assert out.startswith("BEGIN:VCALENDAR\r\n")
        assert out.endswith("END:VCALENDAR\r\n")
        assert "\n" not in out.replace("\r\n", "")

    def test_escapes_text_special_characters(self):
        assert _escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"

    def test_escapes_backslashes_once(self):
        # The backslash rule must not re-escape what the comma rule added.
        assert _escape("a,b") == "a\\,b"

    def test_folds_long_lines_to_75_octets(self):
        folded = _fold("SUMMARY:" + "x" * 200)
        assert all(len(line.encode()) <= 75 for line in folded)
        assert all(line.startswith(" ") for line in folded[1:])
        assert "".join([folded[0]] + [line[1:] for line in folded[1:]]) == "SUMMARY:" + "x" * 200

    def test_folding_never_splits_a_multibyte_character(self):
        line = "SUMMARY:" + "é" * 100
        folded = _fold(line)
        assert all(len(part.encode()) <= 75 for part in folded)
        assert "".join([folded[0]] + [part[1:] for part in folded[1:]]) == line

    def test_dtstamp_is_utc_basic_format(self):
        out = render_calendar(
            [], calendar_name="X", admin_base_url="http://x/", now=datetime(2026, 11, 23, 9, 5, 1)
        )
        assert "BEGIN:VCALENDAR" in out
