"""Tests for setting a request's desired completion date by hand.

The gap this closes: the capture gate frequently finds no date (it is in
an attachment, or in a sentence the gate is too narrow to count), and
`/intake/requests/correct` only helps while the sweep still has that mail
in front of it. The Owner needs a way in from the page itself.

The properties under test: a date only lands with a reason recording how
it was arrived at, the same rule every other write path enforces; a closed
request cannot be re-dated; the change is on the timeline; and it reaches
the calendar feed, which is generated from this field.
"""

from datetime import date, datetime

import pytest

from app.calendar_feed import render_calendar
from app.models import (
    EventType,
    Request as RequestModel,
    RequestClass,
    RequestSource,
    RequestStatus,
)
from app.security import generate_access_token
from tests.conftest import get_csrf_and_cookies, login

_counter = iter(range(3000, 9999))

REASON = "Stated 'end of the month' in the attached PDF; set to the last working day."


def make_request(db_session, **kwargs) -> RequestModel:
    defaults = dict(
        public_number=f"REQ-{next(_counter)}",
        access_token=generate_access_token(),
        title="Confirm supervisory committee membership",
        description="Needs the student's signed form before it can go to the Chair.",
        request_class=RequestClass.long,
        status=RequestStatus.submitted,
        source=RequestSource.email,
        created_at=datetime(2026, 11, 24, 9, 0),
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


def post_date(client, req, date_value="", reason="", csrf=None):
    if csrf is None:
        csrf, _ = get_csrf_and_cookies(client, f"/requests/{req.id}")
    return client.post(
        f"/requests/{req.id}/date",
        data={"csrf_token": csrf, "desired_completion_date": date_value, "desired_date_reason": reason},
        follow_redirects=False,
    )


def date_events(db_session, req) -> list[dict]:
    db_session.refresh(req)
    return [e.event_metadata for e in req.events if e.event_type == EventType.date_changed]


@pytest.fixture()
def owner(client):
    login(client)
    return client


class TestSettingADate:
    def test_a_date_the_capture_missed_can_be_added(self, owner, db_session):
        req = make_request(db_session)

        resp = post_date(owner, req, "2026-12-02", REASON)

        assert resp.status_code == 303
        db_session.refresh(req)
        assert req.desired_completion_date == date(2026, 12, 2)
        assert req.desired_date_reason == REASON

    def test_an_existing_date_can_be_corrected(self, owner, db_session):
        req = make_request(
            db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason="Read off the subject line."
        )

        post_date(owner, req, "2026-12-09", "Body of the mail contradicts the subject: the 9th.")

        db_session.refresh(req)
        assert req.desired_completion_date == date(2026, 12, 9)
        assert req.desired_date_reason == "Body of the mail contradicts the subject: the 9th."

    def test_the_status_is_left_alone(self, owner, db_session):
        # Dating something is not a decision about it: a request awaiting
        # review is still awaiting review afterwards.
        req = make_request(db_session)

        post_date(owner, req, "2026-12-02", REASON)

        db_session.refresh(req)
        assert req.status == RequestStatus.submitted

    def test_it_is_recorded_on_the_timeline(self, owner, db_session):
        req = make_request(db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason="First read.")

        post_date(owner, req, "2026-12-09", REASON)

        assert date_events(db_session, req) == [
            {"source": "owner_edit", "from": "2026-12-02", "to": "2026-12-09", "reason": REASON}
        ]

    def test_the_page_says_what_it_was_set_to(self, owner, db_session):
        req = make_request(db_session)

        post_date(owner, req, "2026-12-02", REASON)
        page = owner.get(f"/requests/{req.id}").text

        assert "Date set to December 2, 2026" in page
        assert "Date changed" in page  # the timeline entry


class TestTheReasonRule:
    def test_a_date_without_a_reason_is_refused(self, owner, db_session):
        req = make_request(db_session)

        post_date(owner, req, "2026-12-02", "")

        db_session.refresh(req)
        assert req.desired_completion_date is None

    def test_the_refusal_says_why(self, owner, db_session):
        req = make_request(db_session)

        post_date(owner, req, "2026-12-02", "")

        assert "needs a reason" in owner.get(f"/requests/{req.id}").text

    def test_whitespace_is_not_a_reason(self, owner, db_session):
        req = make_request(db_session)

        post_date(owner, req, "2026-12-02", "   \n  ")

        db_session.refresh(req)
        assert req.desired_completion_date is None

    def test_an_overlong_reason_is_refused(self, owner, db_session):
        req = make_request(db_session)

        post_date(owner, req, "2026-12-02", "x" * 2001)

        db_session.refresh(req)
        assert req.desired_completion_date is None


class TestClearingTheDate:
    def test_an_empty_date_clears_it(self, owner, db_session):
        req = make_request(db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason=REASON)

        post_date(owner, req, "", "")

        db_session.refresh(req)
        assert req.desired_completion_date is None

    def test_the_reason_goes_with_it(self, owner, db_session):
        # A reason left behind explains a date that is no longer there, and
        # the tracking page would show it under nothing.
        req = make_request(db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason=REASON)

        post_date(owner, req, "", REASON)

        db_session.refresh(req)
        assert req.desired_date_reason is None

    def test_clearing_is_recorded_too(self, owner, db_session):
        req = make_request(db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason=REASON)

        post_date(owner, req, "", "")

        assert date_events(db_session, req) == [
            {"source": "owner_edit", "from": "2026-12-02", "to": None, "reason": None}
        ]


class TestWhatCannotBeRedated:
    @pytest.mark.parametrize(
        "status", [RequestStatus.done, RequestStatus.declined, RequestStatus.cancelled, RequestStatus.withdrawn]
    )
    def test_a_closed_request_is_refused(self, owner, db_session, status):
        req = make_request(db_session, status=status, desired_completion_date=date(2026, 12, 2))

        post_date(owner, req, "2026-12-09", REASON)

        db_session.refresh(req)
        assert req.desired_completion_date == date(2026, 12, 2)

    def test_the_closed_refusal_names_the_status(self, owner, db_session):
        req = make_request(db_session, status=RequestStatus.done)

        post_date(owner, req, "2026-12-09", REASON)

        assert "is done; a closed request is not re-dated" in owner.get(f"/requests/{req.id}").text

    def test_a_closed_request_is_not_offered_the_form(self, owner, db_session):
        req = make_request(db_session, status=RequestStatus.done)

        assert f'action="/requests/{req.id}/date"' not in owner.get(f"/requests/{req.id}").text

    @pytest.mark.parametrize(
        "status",
        [
            RequestStatus.submitted,
            RequestStatus.queued,
            RequestStatus.in_progress,
            RequestStatus.needs_information,
            RequestStatus.blocked,
        ],
    )
    def test_every_open_status_is_offered_the_form(self, owner, db_session, status):
        req = make_request(db_session, status=status)

        assert f'action="/requests/{req.id}/date"' in owner.get(f"/requests/{req.id}").text

    def test_it_needs_the_csrf_token(self, owner, db_session):
        req = make_request(db_session)

        resp = post_date(owner, req, "2026-12-02", REASON, csrf="not-the-token")

        assert resp.status_code != 303
        db_session.refresh(req)
        assert req.desired_completion_date is None

    def test_it_needs_a_session(self, client, db_session):
        req = make_request(db_session)

        resp = client.post(
            f"/requests/{req.id}/date",
            data={"desired_completion_date": "2026-12-02", "desired_date_reason": REASON},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"
        db_session.refresh(req)
        assert req.desired_completion_date is None


class TestNoChange:
    def test_resubmitting_the_same_values_records_nothing(self, owner, db_session):
        req = make_request(db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason=REASON)

        post_date(owner, req, "2026-12-02", REASON)

        assert date_events(db_session, req) == []

    def test_and_says_so(self, owner, db_session):
        req = make_request(db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason=REASON)

        post_date(owner, req, "2026-12-02", REASON)

        assert "The date is unchanged" in owner.get(f"/requests/{req.id}").text


class TestWhereTheDateGoes:
    def test_the_form_offers_the_date_it_already_has(self, owner, db_session):
        req = make_request(db_session, desired_completion_date=date(2026, 12, 2), desired_date_reason=REASON)

        page = owner.get(f"/requests/{req.id}").text

        assert 'value="2026-12-02"' in page
        assert "Change date" in page

    def test_a_request_with_no_date_says_so_and_offers_to_set_one(self, owner, db_session):
        req = make_request(db_session)

        page = owner.get(f"/requests/{req.id}").text

        assert "No date" in page
        assert "Set a date" in page

    def test_a_new_date_reaches_the_calendar_feed(self, owner, db_session):
        # The feed is generated from this field, so a hand-set date is a
        # calendar entry the moment it is saved.
        req = make_request(db_session)
        post_date(owner, req, "2026-12-02", REASON)
        db_session.refresh(req)

        ics = render_calendar([req], calendar_name="Deadlines", admin_base_url="http://x/")

        assert "DTSTART;VALUE=DATE:20261202" in ics
