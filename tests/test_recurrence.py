"""Tests for standing commitments.

The date math is tested against the obligations that actually drove the
design — a monthly reconciliation on the second-last business day backed
off three days for an approval, an annual deadline on a fixed date, and a
per-term booking — because those are the shapes the schedule model has to
express, and a generic test of "monthly" would not catch getting any of
them wrong.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.models import (
    RecurringCommitment,
    Request as RequestModel,
    RequestClass,
    RequestSource,
    RequestStatus,
)
from app.recurrence import (
    compose_date_reason,
    due_occurrence,
    effective_date,
    generate_due_requests,
    next_occurrence,
    next_stated_date,
    nth_last_business_day,
    stated_date_in,
    subtract_business_days,
)


def commitment(**kwargs) -> RecurringCommitment:
    defaults = dict(
        title="Reconcile PCard statement",
        description="Match receipts, code the lines, submit for approval.",
        request_class=RequestClass.long,
        source=RequestSource.manual,
        months=None,
        day_of_month=None,
        business_days_before_month_end=1,
        lead_business_days=3,
        date_reason=None,
        generate_days_ahead=14,
        reference_url=None,
        reference_label=None,
        active=True,
        last_generated_stated_date=None,
    )
    defaults.update(kwargs)
    return RecurringCommitment(**defaults)


class TestBusinessDayArithmetic:
    def test_subtracting_zero_leaves_the_date_alone(self):
        # Including a weekend: the caller asked for no adjustment, and
        # silently shifting the date would hide that.
        saturday = date(2026, 11, 28)
        assert subtract_business_days(saturday, 0) == saturday

    def test_steps_over_a_weekend(self):
        # Monday Nov 30 minus one business day is Friday Nov 27.
        assert subtract_business_days(date(2026, 11, 30), 1) == date(2026, 11, 27)

    def test_steps_over_two_weekends(self):
        assert subtract_business_days(date(2026, 11, 30), 6) == date(2026, 11, 20)

    def test_rejects_a_negative_count(self):
        with pytest.raises(ValueError):
            subtract_business_days(date(2026, 11, 30), -1)

    def test_last_business_day_backs_off_a_weekend_month_end(self):
        # October 2026 ends on Saturday the 31st.
        assert nth_last_business_day(2026, 10, 0) == date(2026, 10, 30)

    def test_second_last_business_day(self):
        assert nth_last_business_day(2026, 10, 1) == date(2026, 10, 29)

    def test_last_business_day_of_a_weekday_month_end(self):
        # November 2026 ends on Monday the 30th.
        assert nth_last_business_day(2026, 11, 0) == date(2026, 11, 30)
        assert nth_last_business_day(2026, 11, 1) == date(2026, 11, 27)


class TestStatedDate:
    def test_fixed_day_of_month(self):
        c = commitment(business_days_before_month_end=None, day_of_month=30, months=[11])
        assert stated_date_in(c, 2026, 11) == date(2026, 11, 30)

    def test_fixed_day_clamps_to_a_short_month(self):
        c = commitment(business_days_before_month_end=None, day_of_month=31)
        assert stated_date_in(c, 2027, 2) == date(2027, 2, 28)

    def test_needs_one_of_the_two_day_rules(self):
        c = commitment(business_days_before_month_end=None, day_of_month=None)
        with pytest.raises(ValueError):
            stated_date_in(c, 2026, 11)

    def test_null_months_means_every_month(self):
        c = commitment(months=None)
        assert next_stated_date(c, after=date(2026, 11, 1)) == date(2026, 11, 27)
        assert next_stated_date(c, after=date(2026, 11, 27)) == date(2026, 12, 30)

    def test_listed_months_skip_the_others(self):
        c = commitment(months=[4, 8, 12], business_days_before_month_end=None, day_of_month=1)
        assert next_stated_date(c, after=date(2026, 5, 1)) == date(2026, 8, 1)

    def test_an_annual_commitment_rolls_to_next_year(self):
        c = commitment(months=[11], business_days_before_month_end=None, day_of_month=30)
        assert next_stated_date(c, after=date(2026, 11, 30)) == date(2027, 11, 30)

    def test_an_impossible_schedule_raises_rather_than_hanging(self):
        c = commitment(months=[13])
        with pytest.raises(ValueError):
            next_stated_date(c, after=date(2026, 1, 1))


class TestTheRealCommitments:
    def test_pcard_is_recorded_three_business_days_before_the_stated_date(self):
        # Monthly, second-last business day, minus 3 for the approval step.
        c = commitment(business_days_before_month_end=1, lead_business_days=3)
        stated, effective = next_occurrence(c, date(2026, 11, 1))
        assert stated == date(2026, 11, 27)  # Friday
        assert effective == date(2026, 11, 24)  # Tuesday — over the weekend

    def test_flex_credits_keeps_its_stated_date(self):
        c = commitment(
            title="Western Flex Credits",
            months=[11],
            business_days_before_month_end=None,
            day_of_month=30,
            lead_business_days=0,
        )
        stated, effective = next_occurrence(c, date(2026, 11, 1))
        assert (stated, effective) == (date(2026, 11, 30), date(2026, 11, 30))

    def test_exam_scanning_is_recorded_two_business_days_early(self):
        c = commitment(
            title="Book exam scanning",
            months=[4, 8, 12],
            business_days_before_month_end=None,
            day_of_month=1,
            lead_business_days=2,
        )
        stated, effective = next_occurrence(c, date(2026, 11, 1))
        assert stated == date(2026, 12, 1)  # Tuesday
        assert effective == date(2026, 11, 27)  # Friday


class TestNextOccurrence:
    def test_an_occurrence_stated_today_is_still_live(self):
        c = commitment(business_days_before_month_end=1, lead_business_days=0)
        stated, _ = next_occurrence(c, date(2026, 11, 27))
        assert stated == date(2026, 11, 27)

    def test_a_missed_occurrence_is_skipped_not_backfilled(self):
        # Last generated in October; it is now December. November's
        # occurrence is gone and must not appear on the queue.
        c = commitment(last_generated_stated_date=date(2026, 10, 29))
        stated, _ = next_occurrence(c, date(2026, 12, 10))
        assert stated == date(2026, 12, 30)

    def test_an_overdue_but_unmissed_occurrence_is_still_returned(self):
        # Effective date has passed, stated date has not: late, not missed.
        c = commitment(business_days_before_month_end=1, lead_business_days=3)
        stated, effective = next_occurrence(c, date(2026, 11, 26))
        assert stated == date(2026, 11, 27)
        assert effective < date(2026, 11, 26)


class TestDueWindow:
    def test_nothing_is_due_outside_the_window(self):
        c = commitment(generate_days_ahead=14)
        assert due_occurrence(c, date(2026, 11, 1)) is None

    def test_due_once_inside_the_window(self):
        c = commitment(generate_days_ahead=14)
        assert due_occurrence(c, date(2026, 11, 15)) == (date(2026, 11, 27), date(2026, 11, 24))

    def test_a_wider_window_pulls_it_forward(self):
        c = commitment(generate_days_ahead=40)
        assert due_occurrence(c, date(2026, 11, 1)) is not None


class TestDateReason:
    def test_records_the_stated_date_when_it_was_moved(self):
        c = commitment(lead_business_days=3, date_reason="An approval step follows.")
        text = compose_date_reason(c, date(2026, 11, 27), date(2026, 11, 24))
        assert "November 27, 2026" in text
        assert "3 business days earlier" in text
        assert "An approval step follows." in text

    def test_says_nothing_about_an_adjustment_that_did_not_happen(self):
        c = commitment(lead_business_days=0)
        text = compose_date_reason(c, date(2026, 11, 30), date(2026, 11, 30))
        assert "earlier" not in text
        assert "November 30, 2026" in text

    def test_singular_business_day(self):
        c = commitment(lead_business_days=1)
        text = compose_date_reason(c, date(2026, 11, 30), date(2026, 11, 27))
        assert "1 business day " in text


class TestGeneration:
    def _add(self, db_session, **kwargs) -> RecurringCommitment:
        c = commitment(**kwargs)
        db_session.add(c)
        db_session.commit()
        return c

    def test_creates_a_request_at_submitted_for_review(self, db_session):
        c = self._add(db_session)
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()

        req = db_session.scalar(select(RequestModel))
        assert req.status == RequestStatus.submitted
        assert req.recurrence_id == c.id
        assert req.desired_completion_date == date(2026, 11, 24)
        assert "November 27, 2026" in req.desired_date_reason

    def test_dates_the_title_so_occurrences_are_distinguishable(self, db_session):
        self._add(db_session)
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        req = db_session.scalar(select(RequestModel))
        assert req.title == "Reconcile PCard statement — November 2026"

    def test_running_twice_does_not_duplicate(self, db_session):
        self._add(db_session)
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        assert len(db_session.scalars(select(RequestModel)).all()) == 1

    def test_generates_the_next_occurrence_once_its_window_opens(self, db_session):
        self._add(db_session)
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        generate_due_requests(db_session, today=date(2026, 12, 20))
        db_session.commit()
        titles = sorted(r.title for r in db_session.scalars(select(RequestModel)).all())
        assert titles == [
            "Reconcile PCard statement — December 2026",
            "Reconcile PCard statement — November 2026",
        ]

    def test_skips_inactive_commitments(self, db_session):
        self._add(db_session, active=False)
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_attaches_the_standing_reference_link(self, db_session):
        self._add(db_session, reference_url="https://example.edu/pcard", reference_label="PCard portal")
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        req = db_session.scalar(select(RequestModel))
        assert [(link.label, link.url) for link in req.links] == [
            ("PCard portal", "https://example.edu/pcard")
        ]

    def test_dry_run_writes_nothing(self, db_session):
        c = self._add(db_session)
        planned = generate_due_requests(db_session, today=date(2026, 11, 15), dry_run=True)
        db_session.rollback()
        assert len(planned) == 1
        assert db_session.scalars(select(RequestModel)).all() == []
        assert c.last_generated_stated_date is None

    def test_generated_requests_notify_nobody(self, db_session, monkeypatch):
        from unittest.mock import patch

        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
        monkeypatch.setattr(settings, "smtp_password", "app-password")
        monkeypatch.setattr(settings, "owner_notification_email", "owner@example.com")

        self._add(db_session)
        with patch("app.email.send_email") as mock_send:
            generate_due_requests(db_session, today=date(2026, 11, 15))
            db_session.commit()
        assert mock_send.call_count == 0

    def test_carries_the_commitment_class_and_source(self, db_session):
        self._add(db_session, request_class=RequestClass.quick, source=RequestSource.email)
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        req = db_session.scalar(select(RequestModel))
        assert req.request_class == RequestClass.quick
        assert req.source == RequestSource.email


class TestScriptCli:
    def test_rejects_an_out_of_range_month(self):
        from scripts.add_recurring import _validate, build_parser

        args = build_parser().parse_args(
            ["--title", "t", "--description", "d", "--class", "long", "--months", "13",
             "--day-of-month", "1"]
        )
        with pytest.raises(SystemExit):
            _validate(args)

    def test_requires_exactly_one_day_rule(self):
        from scripts.add_recurring import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["--title", "t", "--description", "d", "--class", "long"])
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["--title", "t", "--description", "d", "--class", "long",
                 "--day-of-month", "1", "--business-days-before-month-end", "0"]
            )

    def test_generator_reports_nothing_due(self, engine, monkeypatch, capsys):
        import scripts.generate_recurring as script
        from sqlalchemy.orm import sessionmaker

        monkeypatch.setattr(
            script, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine)
        )
        assert script.main(["--as-of", "2026-11-15"]) == 0
        assert "Nothing due" in capsys.readouterr().out

    def test_generator_creates_and_reports(self, engine, db_session, monkeypatch, capsys):
        import scripts.generate_recurring as script
        from sqlalchemy.orm import sessionmaker

        db_session.add(commitment())
        db_session.commit()
        monkeypatch.setattr(
            script, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine)
        )
        assert script.main(["--as-of", "2026-11-15"]) == 0
        out = capsys.readouterr().out
        assert "Created:" in out
        assert "Reconcile PCard statement — November 2026" in out
        assert db_session.scalar(select(RequestModel)) is not None

    def test_generator_list_shows_the_next_occurrence(self, engine, db_session, monkeypatch, capsys):
        import scripts.generate_recurring as script
        from sqlalchemy.orm import sessionmaker

        db_session.add(commitment())
        db_session.commit()
        monkeypatch.setattr(
            script, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine)
        )
        assert script.main(["--list", "--as-of", "2026-11-01"]) == 0
        out = capsys.readouterr().out
        assert "Reconcile PCard statement" in out
        assert "Nov 27, 2026" in out


def test_effective_date_is_the_stated_date_when_no_second_party_acts():
    c = commitment(lead_business_days=0)
    assert effective_date(c, date(2026, 11, 30)) == date(2026, 11, 30)


class TestSettingsPage:
    def test_lists_a_commitment_and_its_next_date(self, client, db_session):
        from tests.conftest import login

        db_session.add(commitment(title="Reconcile PCard statement"))
        db_session.commit()
        login(client)
        page = client.get("/settings")
        assert page.status_code == 200
        assert "Reconcile PCard statement" in page.text
        assert "Standing commitments" in page.text

    def test_says_so_when_there_are_none(self, client):
        from tests.conftest import login

        login(client)
        assert "None recorded." in client.get("/settings").text

    def test_a_broken_schedule_does_not_break_the_page(self, client, db_session):
        from tests.conftest import login

        db_session.add(commitment(title="Impossible", months=[13]))
        db_session.commit()
        login(client)
        page = client.get("/settings")
        assert page.status_code == 200
        assert "does not resolve" in page.text
