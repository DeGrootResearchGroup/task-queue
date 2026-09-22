from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app.models import Request as RequestModel, RequestClass, RequestStatus
from app.security import generate_access_token
from tests.conftest import login


def _insert_done_request(engine, *, created_at: datetime, title: str) -> None:
    """Inserts a completed request with an explicit created_at, bypassing the
    HTTP layer (which always stamps created_at as "now") so we can test the
    date-range filter against a known timestamp."""
    session_local = sessionmaker(bind=engine)
    db = session_local()
    try:
        db.add(
            RequestModel(
                public_number=f"REQ-{title[:4].upper()}",
                access_token=generate_access_token(),
                requester_name="Test Requester",
                requester_email="requester@example.com",
                title=title,
                description="Test description",
                request_class=RequestClass.quick,
                status=RequestStatus.done,
                created_at=created_at,
            )
        )
        db.commit()
    finally:
        db.close()


def test_date_to_filter_includes_requests_from_the_target_day(client, engine):
    login(client)
    # A timestamp late in the day, not midnight — this is exactly the case
    # that a bare string-vs-datetime comparison against '2026-09-22' would
    # have excluded.
    _insert_done_request(engine, created_at=datetime(2026, 9, 22, 23, 45, 0), title="LateInTheDay")

    resp = client.get("/archive", params={"date_to": "2026-09-22"})
    assert "LateInTheDay" in resp.text


def test_date_to_filter_excludes_requests_after_the_target_day(client, engine):
    login(client)
    _insert_done_request(engine, created_at=datetime(2026, 9, 23, 0, 30, 0), title="NextDayEarly")

    resp = client.get("/archive", params={"date_to": "2026-09-22"})
    assert "NextDayEarly" not in resp.text
