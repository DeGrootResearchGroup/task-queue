from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_app_settings, get_db, get_or_create_csrf_token, require_owner, verify_csrf
from app.flash import flash
from app.models import AppSettings
from app.security import generate_calendar_token
from app.templating import render

router = APIRouter(dependencies=[Depends(require_owner)])


@router.get("/settings")
def settings_form(
    request: Request,
    settings_row: AppSettings = Depends(get_app_settings),
):
    email_settings = get_settings()
    calendar_url = (
        f"{request.base_url}calendar/{settings_row.calendar_token}.ics"
        if settings_row.calendar_token
        else None
    )
    return render(
        request,
        "owner/settings.html",
        {
            "owner_nav": True,
            "csrf_token": get_or_create_csrf_token(request),
            "settings_row": settings_row,
            "email_enabled": email_settings.email_enabled,
            "smtp_username": email_settings.smtp_username,
            "owner_notification_email": email_settings.owner_notification_email,
            "calendar_url": calendar_url,
        },
    )


@router.post("/settings/calendar-feed", dependencies=[Depends(verify_csrf)])
def regenerate_calendar_token(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    """Generates the .ics feed's secret URL, or replaces an existing one.

    Replacing it is the only way to revoke a feed: a subscription carries
    no credential beyond the URL, so anything already subscribed to the old
    one simply stops resolving.
    """
    existed = settings_row.calendar_token is not None
    settings_row.calendar_token = generate_calendar_token()
    db.commit()
    flash(
        request,
        "Calendar feed URL replaced. Any existing subscription will stop updating."
        if existed
        else "Calendar feed enabled. Subscribe to the URL below.",
        "success",
    )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/calendar-feed/disable", dependencies=[Depends(verify_csrf)])
def disable_calendar_feed(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    settings_row.calendar_token = None
    db.commit()
    flash(request, "Calendar feed disabled. The URL no longer resolves.", "success")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings", dependencies=[Depends(verify_csrf)])
def settings_submit(
    request: Request,
    owner_display_name: str = Form(...),
    meeting_booking_url: str = Form(""),
    meeting_booking_text: str = Form(""),
    quick_action_minutes: int = Form(...),
    urgency_green_days: int = Form(...),
    urgency_red_days: int = Form(...),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    settings_row.owner_display_name = owner_display_name.strip() or "The Owner"
    settings_row.meeting_booking_url = meeting_booking_url.strip() or None
    settings_row.meeting_booking_text = meeting_booking_text.strip() or None
    settings_row.quick_action_minutes = max(1, quick_action_minutes)
    settings_row.urgency_green_days = max(0, urgency_green_days)
    settings_row.urgency_red_days = max(0, urgency_red_days)
    db.commit()
    flash(request, "Settings saved.", "success")
    return RedirectResponse(url="/settings", status_code=303)
