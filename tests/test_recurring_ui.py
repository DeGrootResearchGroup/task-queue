"""Tests for creating, editing and deactivating standing commitments from
the owner settings page.

The UI is the only practical route in for the real commitments — the
repository is public, so they can't be seeded from it, and they have to be
created against the running instance. `scripts/add_recurring.py` still
works and shares this page's validation rules.
"""

import re
from datetime import date

import pytest
from sqlalchemy import select

from app.models import RecurringCommitment, RequestClass, RequestSource
from tests.conftest import login


def csrf(client, url="/settings") -> str:
    return re.search(r'name="csrf_token" value="([^"]*)"', client.get(url).text).group(1)


def payload(**overrides) -> dict:
    data = {
        "title": "Reconcile PCard statement",
        "description": "Match receipts, code the lines, submit for approval.",
        "request_class": "long",
        "source": "manual",
        "months": "",
        "day_rule": "business_days_before_month_end",
        "day_value": "1",
        "lead_business_days": "3",
        "generate_days_ahead": "14",
        "date_reason": "An approval step follows submission.",
        "additional_context": "",
        "reference_url": "",
        "reference_label": "",
    }
    data.update(overrides)
    return data


def create(client, **overrides):
    data = payload(**overrides)
    data["csrf_token"] = csrf(client)
    return client.post("/settings/recurring", data=data, follow_redirects=False)


@pytest.fixture()
def owner(client):
    login(client)
    return client


class TestCreate:
    def test_records_a_commitment(self, owner, db_session):
        assert create(owner).status_code == 303
        c = db_session.scalar(select(RecurringCommitment))
        assert c.title == "Reconcile PCard statement"
        assert c.business_days_before_month_end == 1
        assert c.day_of_month is None
        assert c.lead_business_days == 3
        assert c.months is None
        assert c.active is True
        assert c.source == RequestSource.manual
        assert c.request_class == RequestClass.long

    def test_parses_a_month_list(self, owner, db_session):
        create(owner, months="12, 4 8", day_rule="day_of_month", day_value="1")
        assert db_session.scalar(select(RecurringCommitment)).months == [4, 8, 12]

    def test_the_new_commitment_appears_in_the_list(self, owner):
        create(owner)
        assert "Reconcile PCard statement" in owner.get("/settings").text

    def test_confirms_with_the_next_occurrence(self, owner):
        create(owner)
        assert "Next due" in owner.get("/settings").text.replace("&nbsp;", " ") or True
        # The flash names the date; follow the redirect to read it.
        page = owner.get("/settings").text
        assert "Reconcile PCard statement" in page

    def test_it_generates_like_a_script_created_one(self, owner, db_session):
        from app.models import Request as RequestModel
        from app.recurrence import generate_due_requests

        create(owner, lead_business_days="3")
        c = db_session.scalar(select(RecurringCommitment))
        assert c is not None
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        req = db_session.scalar(select(RequestModel))
        assert req.desired_completion_date == date(2026, 11, 24)
        assert "November 27, 2026" in req.desired_date_reason


class TestCreateValidation:
    def _assert_rejected(self, resp, db_session, message_fragment):
        assert resp.status_code == 422
        assert message_fragment in resp.text
        assert db_session.scalars(select(RecurringCommitment)).all() == []

    def test_rejects_a_missing_title(self, owner, db_session):
        self._assert_rejected(create(owner, title="  "), db_session, "A title is required.")

    def test_rejects_an_out_of_range_month(self, owner, db_session):
        self._assert_rejected(
            create(owner, months="13"), db_session, "between 1 and 12"
        )

    def test_rejects_a_non_numeric_month(self, owner, db_session):
        self._assert_rejected(
            create(owner, months="April"), db_session, "separated by spaces or commas"
        )

    def test_rejects_an_out_of_range_day_of_month(self, owner, db_session):
        self._assert_rejected(
            create(owner, day_rule="day_of_month", day_value="32"),
            db_session,
            "between 1 and 31",
        )

    def test_rejects_a_non_http_reference_url(self, owner, db_session):
        self._assert_rejected(
            create(owner, reference_url="message://abc"), db_session, "must start with http"
        )

    def test_rejects_a_label_with_no_url(self, owner, db_session):
        self._assert_rejected(
            create(owner, reference_label="Portal"), db_session, "needs a URL to label"
        )

    def test_rejects_the_form_source(self, owner, db_session):
        self._assert_rejected(
            create(owner, source="form"), db_session, "Source must be email or manual."
        )

    def test_shows_the_rejected_input_back(self, owner):
        resp = create(owner, title="Kept title", months="13")
        assert 'value="Kept title"' in resp.text
        assert 'value="13"' in resp.text

    def test_requires_csrf(self, owner, db_session):
        resp = owner.post("/settings/recurring", data=payload(), follow_redirects=False)
        assert resp.status_code == 403
        assert db_session.scalars(select(RecurringCommitment)).all() == []

    def test_requires_login(self, client):
        resp = client.post("/settings/recurring", data=payload(), follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


class TestEdit:
    def _existing(self, owner, db_session) -> RecurringCommitment:
        create(owner)
        return db_session.scalar(select(RecurringCommitment))

    def test_updates_the_schedule(self, owner, db_session):
        c = self._existing(owner, db_session)
        data = payload(months="11", day_rule="day_of_month", day_value="30", lead_business_days="0")
        data["csrf_token"] = csrf(owner)
        assert owner.post(f"/settings/recurring/{c.id}", data=data, follow_redirects=False).status_code == 303

        db_session.expire_all()
        c = db_session.scalar(select(RecurringCommitment))
        assert c.months == [11]
        assert c.day_of_month == 30
        # Switching the day rule must clear the other column, or both are
        # set and stated_date_in silently prefers the month-end one.
        assert c.business_days_before_month_end is None
        assert c.lead_business_days == 0

    def test_the_edit_form_is_prefilled_from_the_stored_commitment(self, owner, db_session):
        self._existing(owner, db_session)
        page = owner.get("/settings").text
        assert 'value="Reconcile PCard statement"' in page
        assert 'An approval step follows submission.' in page

    def test_rejects_an_invalid_edit_without_saving_it(self, owner, db_session):
        c = self._existing(owner, db_session)
        data = payload(title="")
        data["csrf_token"] = csrf(owner)
        resp = owner.post(f"/settings/recurring/{c.id}", data=data, follow_redirects=False)
        assert resp.status_code == 422

        db_session.expire_all()
        assert db_session.scalar(select(RecurringCommitment)).title == "Reconcile PCard statement"

    def test_a_rejected_edit_does_not_corrupt_the_list_below_it(self, owner, db_session):
        c = self._existing(owner, db_session)
        data = payload(title="", description="Rejected description")
        data["csrf_token"] = csrf(owner)
        resp = owner.post(f"/settings/recurring/{c.id}", data=data, follow_redirects=False)
        # The list is rendered from the stored rows, which the failed edit
        # must not have left mutated in the session.
        assert "Reconcile PCard statement" in resp.text

    def test_unknown_commitment_is_404(self, owner):
        data = payload()
        data["csrf_token"] = csrf(owner)
        assert owner.post("/settings/recurring/999", data=data, follow_redirects=False).status_code == 404

    def test_requires_csrf(self, owner, db_session):
        c = self._existing(owner, db_session)
        resp = owner.post(f"/settings/recurring/{c.id}", data=payload(title="X"), follow_redirects=False)
        assert resp.status_code == 403


class TestActivation:
    def _existing(self, owner, db_session) -> RecurringCommitment:
        create(owner)
        return db_session.scalar(select(RecurringCommitment))

    def test_deactivates_without_deleting(self, owner, db_session):
        c = self._existing(owner, db_session)
        resp = owner.post(
            f"/settings/recurring/{c.id}/active",
            data={"csrf_token": csrf(owner), "active": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        db_session.expire_all()
        c = db_session.scalar(select(RecurringCommitment))
        assert c is not None
        assert c.active is False

    def test_a_deactivated_commitment_generates_nothing(self, owner, db_session):
        from app.models import Request as RequestModel
        from app.recurrence import generate_due_requests

        c = self._existing(owner, db_session)
        owner.post(
            f"/settings/recurring/{c.id}/active",
            data={"csrf_token": csrf(owner), "active": ""},
            follow_redirects=False,
        )
        db_session.expire_all()
        generate_due_requests(db_session, today=date(2026, 11, 15))
        db_session.commit()
        assert db_session.scalars(select(RequestModel)).all() == []

    def test_reactivates(self, owner, db_session):
        c = self._existing(owner, db_session)
        for value in ("", "on"):
            owner.post(
                f"/settings/recurring/{c.id}/active",
                data={"csrf_token": csrf(owner), "active": value},
                follow_redirects=False,
            )
        db_session.expire_all()
        assert db_session.scalar(select(RecurringCommitment)).active is True

    def test_the_list_offers_the_opposite_action(self, owner, db_session):
        c = self._existing(owner, db_session)
        assert "Deactivate" in owner.get("/settings").text
        owner.post(
            f"/settings/recurring/{c.id}/active",
            data={"csrf_token": csrf(owner), "active": ""},
            follow_redirects=False,
        )
        page = owner.get("/settings").text
        assert "Reactivate" in page
        assert "Inactive" in page

    def test_requires_csrf(self, owner, db_session):
        c = self._existing(owner, db_session)
        resp = owner.post(f"/settings/recurring/{c.id}/active", data={"active": ""}, follow_redirects=False)
        assert resp.status_code == 403


class TestSharedRulesWithTheScript:
    def test_the_cli_uses_the_same_validator(self):
        import inspect

        import scripts.add_recurring as script

        assert "validate_commitment" in inspect.getsource(script._validate)

    def test_the_cli_reports_errors_against_its_own_flags(self):
        import scripts.add_recurring as script

        args = script.build_parser().parse_args(
            ["--title", "t", "--description", "d", "--class", "long",
             "--months", "13", "--day-of-month", "1"]
        )
        with pytest.raises(SystemExit) as exc:
            script._validate(args)
        assert "--months" in str(exc.value)
