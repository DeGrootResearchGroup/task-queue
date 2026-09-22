from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_app_settings, get_db, get_or_create_csrf_token, require_owner, verify_csrf
from app.flash import flash
from app.models import AppSettings, Request as RequestModel, RequestClass, RequestStatus
from app.notifications import notify_requester_completed, notify_requester_needs_information
from app.state_machine import complete, move_to_long_actions, request_information
from app.templating import render
from app.utils import urgency_level

router = APIRouter(dependencies=[Depends(require_owner)])


def _next_quick_action(db: Session) -> RequestModel | None:
    """Ordering per spec §54: overdue first, then soonest desired date, then oldest first."""
    pending = list(
        db.scalars(
            select(RequestModel).where(
                RequestModel.request_class == RequestClass.quick,
                RequestModel.status == RequestStatus.queued,
            )
        )
    )
    if not pending:
        return None

    today = date.today()

    def sort_key(req: RequestModel):
        d = req.desired_completion_date
        overdue = d is not None and d < today
        has_date = d is not None
        return (
            0 if overdue else 1,
            d if has_date else date.max,
            req.created_at,
        )

    pending.sort(key=sort_key)
    return pending[0]


@router.get("/quick-session")
def quick_session(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _next_quick_action(db)
    csrf_token = get_or_create_csrf_token(request)
    if req is None:
        return render(request, "owner/quick_session.html", {"owner_nav": True, "req": None, "csrf_token": csrf_token})
    return render(
        request,
        "owner/quick_session.html",
        {
            "owner_nav": True,
            "req": req,
            "urgency": urgency_level(req.desired_completion_date, settings_row),
            "csrf_token": csrf_token,
        },
    )


def _get_quick_request_or_404(db: Session, request_id: int) -> RequestModel:
    req = db.get(RequestModel, request_id)
    if req is None or req.request_class != RequestClass.quick:
        raise HTTPException(status_code=404, detail="Quick Action not found")
    return req


@router.post("/quick-session/{request_id}/done", dependencies=[Depends(verify_csrf)])
def quick_done(
    request_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_quick_request_or_404(db, request_id)
    complete(db, req)
    db.commit()
    db.refresh(req)
    db.refresh(settings_row)
    background_tasks.add_task(notify_requester_completed, str(request.base_url), req, settings_row)
    flash(request, f"Marked done: {req.title}", "success")
    return RedirectResponse(url="/quick-session", status_code=303)


@router.post("/quick-session/{request_id}/need-info", dependencies=[Depends(verify_csrf)])
def quick_need_info(
    request_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    question: str = Form(...),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_quick_request_or_404(db, request_id)
    request_information(db, req, question)
    db.commit()
    db.refresh(req)
    db.refresh(settings_row)
    background_tasks.add_task(
        notify_requester_needs_information, str(request.base_url), req, question.strip(), settings_row
    )
    flash(request, f"Information requested for: {req.title}", "success")
    return RedirectResponse(url="/quick-session", status_code=303)


@router.post("/quick-session/{request_id}/move-to-long", dependencies=[Depends(verify_csrf)])
def quick_move_to_long(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_quick_request_or_404(db, request_id)
    move_to_long_actions(db, req)
    db.commit()
    flash(request, f"Moved to Long Actions: {req.title}", "success")
    return RedirectResponse(url="/quick-session", status_code=303)
