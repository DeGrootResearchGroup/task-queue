"""Tests for walking the review queue from the request pages themselves.

The property under test: triaging a request leaves a way straight to the next
one awaiting review, so a pass over the queue does not have to go back through
the dashboard between every Accept or Decline. The link is also there before
the decision, as a Skip, and it disappears once nothing else is waiting.
"""

from datetime import datetime, timedelta

import pytest

from app.models import Request as RequestModel, RequestClass, RequestSource, RequestStatus
from app.security import generate_access_token
from tests.conftest import get_csrf_and_cookies, login

_counter = iter(range(2000, 9999))

BASE_TIME = datetime(2026, 11, 20, 9, 0, 0)


def make_submitted(db_session, title, minutes_old=0, **kwargs) -> RequestModel:
    """A request awaiting review, with an explicit `created_at`.

    The order the button follows is `created_at`, so these tests set it rather
    than relying on rows inserted in the same second sorting by luck.
    """
    defaults = dict(
        public_number=f"REQ-{next(_counter)}",
        access_token=generate_access_token(),
        title=title,
        description="Needs a decision before it can go anywhere.",
        request_class=RequestClass.long,
        status=RequestStatus.submitted,
        source=RequestSource.email,
        created_at=BASE_TIME + timedelta(minutes=minutes_old),
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


def next_link(html: str) -> tuple[int, str] | None:
    """The triage button's target id and label, or None if it isn't rendered."""
    import re

    match = re.search(r'href="/requests/(\d+)">\s*(Next awaiting review|Skip to next)', html)
    if match is None:
        return None
    return int(match.group(1)), match.group(2)


def badge_count(html: str) -> int:
    import re

    return int(re.search(r'title="(\d+) still awaiting review"', html).group(1))


@pytest.fixture()
def owner(client):
    login(client)
    return client


class TestBeforeTheDecision:
    def test_a_submitted_request_offers_a_skip_to_the_next_one(self, owner, db_session):
        first = make_submitted(db_session, "Committee membership", minutes_old=0)
        second = make_submitted(db_session, "Reference letter", minutes_old=10)

        resp = owner.get(f"/requests/{first.id}")

        assert next_link(resp.text) == (second.id, "Skip to next")

    def test_the_skip_does_not_claim_this_one_is_still_waiting(self, owner, db_session):
        first = make_submitted(db_session, "Committee membership", minutes_old=0)
        make_submitted(db_session, "Reference letter", minutes_old=10)
        make_submitted(db_session, "PCard reconciliation", minutes_old=20)

        resp = owner.get(f"/requests/{first.id}")

        # Three are submitted; two of them are not this page.
        assert badge_count(resp.text) == 2


class TestAfterTheDecision:
    def test_accepting_leaves_a_link_to_the_next_review(self, owner, db_session):
        first = make_submitted(db_session, "Committee membership", minutes_old=0)
        second = make_submitted(db_session, "Reference letter", minutes_old=10)

        csrf, _ = get_csrf_and_cookies(owner, f"/requests/{first.id}")
        resp = owner.post(f"/requests/{first.id}/accept", data={"csrf_token": csrf}, follow_redirects=True)

        assert next_link(resp.text) == (second.id, "Next awaiting review")
        assert badge_count(resp.text) == 1

    def test_declining_leaves_a_link_to_the_next_review(self, owner, db_session):
        first = make_submitted(db_session, "Committee membership", minutes_old=0)
        second = make_submitted(db_session, "Reference letter", minutes_old=10)

        csrf, _ = get_csrf_and_cookies(owner, f"/requests/{first.id}")
        resp = owner.post(
            f"/requests/{first.id}/decline",
            data={"csrf_token": csrf, "reason": "Not mine to sign."},
            follow_redirects=True,
        )

        assert next_link(resp.text) == (second.id, "Next awaiting review")

    def test_putting_one_on_hold_leaves_a_link_to_the_next_review(self, owner, db_session):
        first = make_submitted(db_session, "Committee membership", minutes_old=0)
        second = make_submitted(db_session, "Reference letter", minutes_old=10)

        csrf, _ = get_csrf_and_cookies(owner, f"/requests/{first.id}")
        resp = owner.post(
            f"/requests/{first.id}/block",
            data={"csrf_token": csrf, "waiting_on": "Mahsa — signed form", "until": "", "note": ""},
            follow_redirects=True,
        )

        assert next_link(resp.text) == (second.id, "Next awaiting review")


class TestOrder:
    def test_it_follows_the_dashboard_order(self, owner, db_session):
        make_submitted(db_session, "Committee membership", minutes_old=0)
        middle = make_submitted(db_session, "Reference letter", minutes_old=10)
        last = make_submitted(db_session, "PCard reconciliation", minutes_old=20)

        resp = owner.get(f"/requests/{middle.id}")

        assert next_link(resp.text) == (last.id, "Skip to next")

    def test_the_same_timestamp_is_broken_by_id_rather_than_looping(self, owner, db_session):
        # The mail sweep POSTs a run's candidates together, so ties are normal.
        first = make_submitted(db_session, "Committee membership", minutes_old=5)
        second = make_submitted(db_session, "Reference letter", minutes_old=5)

        assert next_link(owner.get(f"/requests/{first.id}").text) == (second.id, "Skip to next")

    def test_the_last_one_wraps_round_to_the_oldest(self, owner, db_session):
        first = make_submitted(db_session, "Committee membership", minutes_old=0)
        last = make_submitted(db_session, "Reference letter", minutes_old=10)

        resp = owner.get(f"/requests/{last.id}")

        assert next_link(resp.text) == (first.id, "Skip to next")


class TestEndOfThePass:
    def test_the_only_request_awaiting_review_gets_no_button(self, owner, db_session):
        only = make_submitted(db_session, "Committee membership")

        resp = owner.get(f"/requests/{only.id}")

        assert next_link(resp.text) is None

    def test_the_button_goes_once_the_last_one_is_triaged(self, owner, db_session):
        first = make_submitted(db_session, "Committee membership", minutes_old=0)
        second = make_submitted(db_session, "Reference letter", minutes_old=10)

        csrf, _ = get_csrf_and_cookies(owner, f"/requests/{first.id}")
        owner.post(f"/requests/{first.id}/accept", data={"csrf_token": csrf}, follow_redirects=False)
        csrf, _ = get_csrf_and_cookies(owner, f"/requests/{second.id}")
        resp = owner.post(f"/requests/{second.id}/accept", data={"csrf_token": csrf}, follow_redirects=True)

        assert next_link(resp.text) is None

    def test_a_request_that_was_never_in_review_still_shows_the_queue(self, owner, db_session):
        # Opening a queued item mid-pass should not hide what is still waiting.
        queued = make_submitted(db_session, "Already accepted", minutes_old=0, status=RequestStatus.queued)
        waiting = make_submitted(db_session, "Reference letter", minutes_old=10)

        resp = owner.get(f"/requests/{queued.id}")

        assert next_link(resp.text) == (waiting.id, "Next awaiting review")
