import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["OWNER_USERNAME"] = "owner"
os.environ["ENVIRONMENT"] = "test"
os.environ["MIN_FORM_FILL_SECONDS"] = "0"
os.environ["SUBMIT_RATE_LIMIT"] = "1000/minute"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (ensures models are registered on Base.metadata)
from app.config import get_settings
from app.db import Base
from app.security import hash_password

TEST_OWNER_PASSWORD = "test-owner-password"
get_settings().owner_password_hash = hash_password(TEST_OWNER_PASSWORD)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def db_session(engine):
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def get_csrf_and_cookies(client, url="/"):
    resp = client.get(url)
    import re

    match = re.search(r'name="csrf_token" value="([^"]*)"', resp.text)
    return match.group(1), resp


def login(client) -> None:
    csrf, _ = get_csrf_and_cookies(client, "/login")
    resp = client.post(
        "/login",
        data={"csrf_token": csrf, "username": "owner", "password": TEST_OWNER_PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def submit_request(client, **overrides) -> str:
    """Submits a request as a requester and returns its tracking URL (/r/<token>)."""
    import re

    from app.antispam import FORM_RENDERED_AT_FIELD, HONEYPOT_FIELD

    resp = client.get("/")
    csrf = re.search(r'name="csrf_token" value="([^"]*)"', resp.text).group(1)
    rendered_at = re.search(rf'name="{FORM_RENDERED_AT_FIELD}" value="([^"]*)"', resp.text).group(1)

    payload = {
        "csrf_token": csrf,
        FORM_RENDERED_AT_FIELD: rendered_at,
        HONEYPOT_FIELD: "",
        "requester_name": "Alice",
        "requester_email": "alice@example.com",
        "title": "Review manuscript introduction",
        "description": "Review sections 1-2 and tell me if the argument is clear.",
        "additional_context": "",
        "request_class": "long",
        "desired_completion_date": "",
        "desired_date_reason": "",
        "confirmation": "on",
    }
    payload.update(overrides)
    resp = client.post("/", data=payload, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return resp.headers["location"]
