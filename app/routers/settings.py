from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_app_settings, get_db, get_or_create_csrf_token, require_owner, verify_csrf
from app.flash import flash
from app.models import AppSettings
from app.templating import render

router = APIRouter(dependencies=[Depends(require_owner)])


@router.get("/settings")
def settings_form(
    request: Request,
    settings_row: AppSettings = Depends(get_app_settings),
):
    return render(
        request,
        "owner/settings.html",
        {
            "owner_nav": True,
            "csrf_token": get_or_create_csrf_token(request),
            "settings_row": settings_row,
        },
    )


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
