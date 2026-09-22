"""Regression tests for two race conditions found in code review: a
read-modify-write on the public-number counter, and an unguarded singleton
row creation for AppSettings. Both only manifest under genuine concurrent
access to a real (file-backed) SQLite database — an in-memory StaticPool
DB shares one connection across "threads" and won't exercise SQLite's
actual locking behavior, so these use a temp file DB with each thread
opening its own connection, matching how the app really runs.
"""

import tempfile
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.deps import get_app_settings
from app.models import AppSettings, RequestCounter
from app.utils import next_public_number


@pytest.fixture()
def file_db_sessionmaker():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "concurrency_test.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(bind=engine)
        yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
        engine.dispose()


def test_next_public_number_is_race_free_under_concurrency(file_db_sessionmaker):
    results = []
    errors = []

    def worker():
        db = file_db_sessionmaker()
        try:
            results.append(next_public_number(db))
            db.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors: {errors}"
    assert len(results) == 10
    assert len(set(results)) == 10, f"duplicate public numbers assigned: {results}"

    check_db = file_db_sessionmaker()
    try:
        counter = check_db.get(RequestCounter, 1)
        assert counter.next_value == 11  # started at 1, 10 successful increments
    finally:
        check_db.close()


def test_get_app_settings_bootstrap_is_race_free_under_concurrency(file_db_sessionmaker):
    results = []
    errors = []

    def worker():
        db = file_db_sessionmaker()
        try:
            results.append(get_app_settings(db).id)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors (e.g. IntegrityError from a duplicate insert): {errors}"
    assert results == [1] * 10

    check_db = file_db_sessionmaker()
    try:
        assert check_db.query(AppSettings).count() == 1
    finally:
        check_db.close()
