from datetime import date

from sqlalchemy.orm import Session

from app.models import AppSettings, RequestCounter


def next_public_number(db: Session) -> str:
    counter = db.get(RequestCounter, 1)
    if counter is None:
        counter = RequestCounter(id=1, next_value=1)
        db.add(counter)
        db.flush()
    value = counter.next_value
    counter.next_value = value + 1
    return f"REQ-{value:04d}"


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
