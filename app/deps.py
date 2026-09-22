from fastapi import Depends, HTTPException, Request, status
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
        defaults = get_settings()
        settings_row = AppSettings(
            id=1,
            owner_display_name=defaults.owner_display_name,
            meeting_booking_url=defaults.meeting_booking_url or None,
            meeting_booking_text=defaults.meeting_booking_text,
            quick_action_minutes=defaults.quick_action_minutes,
            urgency_green_days=defaults.urgency_green_days,
            urgency_red_days=defaults.urgency_red_days,
        )
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    return settings_row
