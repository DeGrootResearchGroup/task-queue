from datetime import date

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import AppSettings, RequestCounter


def next_public_number(db: Session) -> str:
    """Atomically assigns the next sequential public request number.

    A plain Python read-modify-write here (SELECT next_value, then
    UPDATE next_value = old + 1) is a lost-update race: two concurrent
    submissions can both read the same value and both write value+1,
    handing out a duplicate public_number and only advancing the counter
    once. Doing the increment as a single SQL-side UPDATE ... RETURNING
    means SQLite's own write-lock serializes concurrent callers — the
    second caller's UPDATE can't even start until the first commits, and
    by then it reads the already-incremented value.
    """
    # Race-safe row bootstrap: if two callers both find the counter row
    # missing, only one INSERT wins — the other becomes a no-op instead of
    # raising a duplicate-primary-key IntegrityError.
    db.execute(
        sqlite_insert(RequestCounter).values(id=1, next_value=1).on_conflict_do_nothing(index_elements=["id"])
    )
    new_value = db.execute(
        update(RequestCounter)
        .where(RequestCounter.id == 1)
        .values(next_value=RequestCounter.next_value + 1)
        .returning(RequestCounter.next_value)
    ).scalar_one()
    assigned_value = new_value - 1
    return f"REQ-{assigned_value:04d}"


def urgency_level(
    desired_date: date | None, settings_row: AppSettings, today: date | None = None
) -> str:
    """Returns 'none' | 'green' | 'yellow' | 'red' per spec §22 / §50."""
    if desired_date is None:
        return "none"
    today = today or date.today()
    days_remaining = (desired_date - today).days
    if days_remaining <= settings_row.urgency_red_days:
        return "red"
    if days_remaining <= settings_row.urgency_green_days:
        return "yellow"
    return "green"
