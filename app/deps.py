from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AppSettings
from app.security import csrf_tokens_match, generate_csrf_token


def require_owner(request: Request) -> None:
    if not request.session.get("owner_authenticated"):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Not authenticated",
            headers={"Location": "/login"},
        )


def get_or_create_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = generate_csrf_token()
        request.session["csrf_token"] = token
    return token


async def verify_csrf(request: Request) -> None:
    form = await request.form()
    submitted = form.get("csrf_token")
    expected = request.session.get("csrf_token")
    if not csrf_tokens_match(submitted, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def get_app_settings(db: Session = Depends(get_db)) -> AppSettings:
    settings_row = db.get(AppSettings, 1)
    if settings_row is None:
        # Race-safe bootstrap: this dependency runs on nearly every route, so
        # two requests landing close together on a fresh deploy (before this
        # row exists) can both reach this branch. A plain INSERT would have
        # the second one raise an unhandled IntegrityError on the duplicate
        # primary key; ON CONFLICT DO NOTHING makes the loser's insert a
        # no-op instead, and both then just re-fetch the row the winner made.
        defaults = get_settings()
        db.execute(
            sqlite_insert(AppSettings)
            .values(
                id=1,
                owner_display_name=defaults.owner_display_name,
                meeting_booking_url=defaults.meeting_booking_url or None,
                meeting_booking_text=defaults.meeting_booking_text,
                quick_action_minutes=defaults.quick_action_minutes,
                urgency_green_days=defaults.urgency_green_days,
                urgency_red_days=defaults.urgency_red_days,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        db.commit()
        settings_row = db.get(AppSettings, 1)
    return settings_row
