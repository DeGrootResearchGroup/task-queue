from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def commit_and_refresh(db: Session, *objects: object) -> None:
    """Commits, then refreshes each given object.

    SessionLocal uses SQLAlchemy's default expire_on_commit=True, which
    marks every attribute on every tracked object as stale after a commit —
    the next access re-queries the database. That's fine within a request,
    but several routes schedule a FastAPI BackgroundTask (e.g. an email
    notification) that reads an object's attributes after the response has
    been returned, by which point this request's `get_db` session dependency
    may have already closed and can no longer lazy-load anything. Refreshing
    before returning avoids that, without needing to disable expiration for
    the whole session (which would also affect server-computed columns like
    created_at on newly inserted rows).
    """
    db.commit()
    for obj in objects:
        db.refresh(obj)
