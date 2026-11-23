"""Tests for Request.source — the three-way split between a real request
from someone else (`form`), an obligation lifted out of the Owner's
mailbox (`email`), and one the Owner added directly (`manual`)."""

from sqlalchemy import select

from app.models import Request as RequestModel, RequestClass, RequestSource, RequestStatus
from app.security import generate_access_token
from tests.conftest import login, submit_request


def make_request(db_session, source=RequestSource.manual, **kwargs) -> RequestModel:
    defaults = dict(
        public_number=f"REQ-{abs(hash(kwargs.get('title', 'x'))) % 9000 + 1000}",
        access_token=generate_access_token(),
        title="Reconcile PCard statement",
        description="Match receipts and submit.",
        request_class=RequestClass.long,
        status=RequestStatus.submitted,
        source=source,
    )
    defaults.update(kwargs)
    req = RequestModel(**defaults)
    db_session.add(req)
    db_session.commit()
    return req


class TestSourceDefaults:
    def test_public_submission_is_form_sourced(self, client, db_session):
        submit_request(client)
        req = db_session.scalar(select(RequestModel))
        assert req.source == RequestSource.form

    def test_source_defaults_to_form_when_unset(self, db_session):
        req = RequestModel(
            public_number="REQ-9999",
            access_token=generate_access_token(),
            requester_name="Alice",
            requester_email="alice@example.com",
            title="t",
            description="d",
            request_class=RequestClass.long,
            status=RequestStatus.submitted,
        )
        db_session.add(req)
        db_session.commit()
        assert req.source == RequestSource.form


class TestRequesterIsOptional:
    def test_request_can_be_stored_with_no_requester(self, db_session):
        req = make_request(db_session, source=RequestSource.email)
        assert req.requester_name is None
        assert req.requester_email is None

    def test_detail_page_shows_source_instead_of_a_blank_requester(self, client, db_session):
        req = make_request(db_session, source=RequestSource.email)
        login(client)
        page = client.get(f"/requests/{req.id}")
        assert page.status_code == 200
        assert "From email" in page.text
        # The requester block must not render an empty "<>" pair.
        assert "&lt;&gt;" not in page.text


class TestDashboardFilter:
    def _seed(self, client, db_session):
        submit_request(client)  # source=form
        make_request(db_session, source=RequestSource.email, title="Email-sourced item")
        make_request(db_session, source=RequestSource.manual, title="Manual item")

    def test_unfiltered_dashboard_shows_all_three(self, client, db_session):
        self._seed(client, db_session)
        login(client)
        page = client.get("/dashboard")
        assert "Review manuscript introduction" in page.text
        assert "Email-sourced item" in page.text
        assert "Manual item" in page.text

    def test_filtering_by_source_excludes_the_others(self, client, db_session):
        self._seed(client, db_session)
        login(client)
        page = client.get("/dashboard?source=email")
        assert "Email-sourced item" in page.text
        assert "Manual item" not in page.text
        assert "Review manuscript introduction" not in page.text

    def test_unknown_source_value_falls_back_to_showing_everything(self, client, db_session):
        self._seed(client, db_session)
        login(client)
        page = client.get("/dashboard?source=nonsense")
        assert page.status_code == 200
        assert "Email-sourced item" in page.text
        assert "Manual item" in page.text

    def test_quick_actions_awaiting_review_are_visible(self, client, db_session):
        # A Quick Action at `submitted` is only reachable server-side, and
        # would be invisible if the review section were still Long-only.
        make_request(
            db_session,
            source=RequestSource.email,
            request_class=RequestClass.quick,
            title="Approve Kuali form",
        )
        login(client)
        page = client.get("/dashboard")
        assert "Approve Kuali form" in page.text
