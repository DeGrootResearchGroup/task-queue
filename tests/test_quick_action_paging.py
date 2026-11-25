"""Tests for paging through the dashboard's Quick Actions card.

The property under test: every waiting Quick Action can be read from the
dashboard. The card shows a page at a time, and the pager underneath reaches
the rest, so nothing past the first page is reachable only by starting a
Quick Action session.
"""

import re
from datetime import datetime, timedelta

import pytest

from app.models import Request as RequestModel, RequestClass, RequestSource, RequestStatus
from app.routers.dashboard import QUICK_PAGE_SIZE
from app.security import generate_access_token
from tests.conftest import login

_counter = iter(range(3000, 9999))

BASE_TIME = datetime(2026, 11, 20, 9, 0, 0)


def make_quick(db_session, title, minutes_old=0, **kwargs) -> RequestModel:
    """An accepted Quick Action waiting to be done, with an explicit `created_at`.

    The card orders by `created_at`, so these set it rather than relying on
    rows inserted in the same second sorting by luck.
    """
    defaults = dict(
        public_number=f"REQ-{next(_counter)}",
        access_token=generate_access_token(),
        title=title,
        description="Small enough to do on the spot.",
        request_class=RequestClass.quick,
        status=RequestStatus.queued,
        source=RequestSource.email,
        created_at=BASE_TIME + timedelta(minutes=minutes_old),
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


def make_many(db_session, count, prefix="Quick task", **kwargs) -> list[str]:
    """`count` Quick Actions in a known order, returning their titles."""
    titles = [f"{prefix} {n:02d}" for n in range(1, count + 1)]
    for minutes, title in enumerate(titles):
        make_quick(db_session, title, minutes_old=minutes, **kwargs)
    return titles


def quick_card(html: str) -> str:
    """Just the Quick Actions card, so a title found here is found *there*."""
    start = html.index('id="quick-actions"')
    rest = html[start:]
    end = rest.find('<div class="card section-card')
    return rest if end == -1 else rest[:end]


def titles_on_page(html: str, titles: list[str]) -> list[str]:
    card = quick_card(html)
    return [t for t in titles if t in card]


def pager_links(html: str) -> list[str]:
    """The pager's hrefs, with `&amp;` read back as the `&` a browser sees."""
    found = re.findall(r'href="([^"]*quick_page=[^"]*)"', quick_card(html))
    return [link.replace("&amp;", "&") for link in found]


def next_link(html: str) -> str | None:
    match = re.search(r'href="([^"]+)" rel="next"', quick_card(html))
    return None if match is None else match.group(1).replace("&amp;", "&")


def showing_line(html: str) -> str | None:
    match = re.search(r"Showing (\d+)&ndash;(\d+) of (\d+)", quick_card(html))
    return None if match is None else f"{match.group(1)}-{match.group(2)} of {match.group(3)}"


def page_label(html: str) -> str | None:
    match = re.search(r"Page (\d+) of (\d+)", quick_card(html))
    return None if match is None else f"{match.group(1)}/{match.group(2)}"


def get_dashboard(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    resp = client.get(f"/dashboard?{query}" if query else "/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.text


class TestTheFirstPage:
    def test_shows_one_page_of_rows(self, client, db_session):
        login(client)
        titles = make_many(db_session, 23)

        html = get_dashboard(client)

        assert titles_on_page(html, titles) == titles[:QUICK_PAGE_SIZE]

    def test_says_what_it_is_showing(self, client, db_session):
        login(client)
        make_many(db_session, 23)

        html = get_dashboard(client)

        assert showing_line(html) == "1-8 of 23"
        assert page_label(html) == "1/3"

    def test_counts_beside_the_heading_stay_the_total(self, client, db_session):
        """The point of the exercise: the count said 23 while 8 were shown."""
        login(client)
        make_many(db_session, 23)

        html = get_dashboard(client)

        assert "23 quick actions" in html

    def test_offers_a_way_forward_but_not_back(self, client, db_session):
        login(client)
        make_many(db_session, 23)

        links = pager_links(get_dashboard(client))

        assert links == ["/dashboard?quick_page=2#quick-actions"]


class TestWalkingTheQueue:
    def test_every_row_is_reachable_by_following_next(self, client, db_session):
        login(client)
        titles = make_many(db_session, 23)

        seen: list[str] = []
        html = get_dashboard(client)
        for _ in range(10):  # a bound, so a pager that loops fails rather than hangs
            seen.extend(titles_on_page(html, titles))
            forward = next_link(html)
            if forward is None:
                break
            resp = client.get(forward)
            assert resp.status_code == 200
            html = resp.text

        assert seen == titles

    def test_a_middle_page_goes_both_ways(self, client, db_session):
        login(client)
        titles = make_many(db_session, 23)

        html = get_dashboard(client, quick_page=2)

        assert titles_on_page(html, titles) == titles[8:16]
        assert showing_line(html) == "9-16 of 23"
        assert pager_links(html) == [
            "/dashboard?quick_page=1#quick-actions",
            "/dashboard?quick_page=3#quick-actions",
        ]

    def test_the_last_page_holds_the_remainder_and_stops(self, client, db_session):
        login(client)
        titles = make_many(db_session, 23)

        html = get_dashboard(client, quick_page=3)

        assert titles_on_page(html, titles) == titles[16:]
        assert showing_line(html) == "17-23 of 23"
        assert pager_links(html) == ["/dashboard?quick_page=2#quick-actions"]

    def test_pages_land_on_the_card(self, client, db_session):
        """Anchored, so a click doesn't bounce back to the top of the dashboard."""
        login(client)
        make_many(db_session, 23)

        assert all(link.endswith("#quick-actions") for link in pager_links(get_dashboard(client)))


class TestWhenThereIsNothingToPage:
    @pytest.mark.parametrize("count", [0, 1, 7, QUICK_PAGE_SIZE])
    def test_no_pager_when_it_all_fits(self, client, db_session, count):
        login(client)
        titles = make_many(db_session, count)

        html = get_dashboard(client)

        assert pager_links(html) == []
        assert page_label(html) is None
        assert titles_on_page(html, titles) == titles

    def test_one_over_the_page_size_pages(self, client, db_session):
        login(client)
        make_many(db_session, QUICK_PAGE_SIZE + 1)

        html = get_dashboard(client)

        assert page_label(html) == "1/2"
        assert showing_line(html) == "1-8 of 9"

    def test_empty_queue_still_says_so(self, client, db_session):
        login(client)

        html = get_dashboard(client)

        assert "No Quick Actions waiting" in quick_card(html)
        assert pager_links(html) == []


class TestAPageThatIsNotThere:
    @pytest.mark.parametrize("asked", ["4", "99"])
    def test_past_the_end_lands_on_the_last_page(self, client, db_session, asked):
        """A bookmarked page 4 the queue has since shrunk past still shows rows."""
        login(client)
        titles = make_many(db_session, 23)

        html = get_dashboard(client, quick_page=asked)

        assert titles_on_page(html, titles) == titles[16:]
        assert page_label(html) == "3/3"

    @pytest.mark.parametrize("asked", ["0", "-2", "abc", "", "2.5", "1%20"])
    def test_anything_unreadable_lands_on_the_first_page(self, client, db_session, asked):
        login(client)
        titles = make_many(db_session, 23)

        html = get_dashboard(client, quick_page=asked)

        assert titles_on_page(html, titles) == titles[:QUICK_PAGE_SIZE]
        assert page_label(html) == "1/3"


class TestAlongsideTheSourceFilter:
    def test_pages_keep_the_filter(self, client, db_session):
        login(client)
        make_many(db_session, 23, source=RequestSource.email)

        links = pager_links(get_dashboard(client, source="email"))

        assert links == ["/dashboard?source=email&quick_page=2#quick-actions"]

    def test_a_filtered_queue_pages_on_its_own_count(self, client, db_session):
        login(client)
        from_email = make_many(db_session, 10, prefix="Emailed", source=RequestSource.email)
        make_many(db_session, 10, prefix="Formed", source=RequestSource.form)

        html = get_dashboard(client, source="email", quick_page=2)

        assert titles_on_page(html, from_email) == from_email[8:]
        assert showing_line(html) == "9-10 of 10"

    def test_changing_the_filter_starts_again_at_the_first_page(self, client, db_session):
        login(client)
        make_many(db_session, 23)

        html = get_dashboard(client, quick_page=3)

        assert 'href="/dashboard?source=email"' in html


class TestWhatIsOnTheList:
    def test_only_accepted_quick_actions_are_paged(self, client, db_session):
        """A Quick Action still awaiting review belongs to that card, not this
        one, so it must not shift the pages here."""
        login(client)
        titles = make_many(db_session, 8)
        make_quick(db_session, "Not yet reviewed", minutes_old=99, status=RequestStatus.submitted)

        html = get_dashboard(client)

        assert pager_links(html) == []
        assert titles_on_page(html, titles) == titles
        assert "Not yet reviewed" not in quick_card(html)

    def test_a_long_action_does_not_count(self, client, db_session):
        login(client)
        make_many(db_session, 8)
        make_quick(db_session, "A long one", minutes_old=99, request_class=RequestClass.long)

        html = get_dashboard(client)

        assert pager_links(html) == []
        assert "8 quick actions" in html

    def test_the_dashboard_still_needs_a_session(self, client, db_session):
        make_many(db_session, 23)

        resp = client.get("/dashboard?quick_page=2", follow_redirects=False)

        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login")
