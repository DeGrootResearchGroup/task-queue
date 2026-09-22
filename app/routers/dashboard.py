from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_app_settings, get_db, get_or_create_csrf_token, require_owner, verify_csrf
from app.flash import flash
from app.models import AppSettings, Request as RequestModel, RequestClass, RequestStatus
from app.state_machine import (
    accept_and_queue,
    complete,
    decline,
    move_down,
    move_to_long_actions,
    move_up,
    reorder_queue,
    request_information,
    return_to_queue,
    start_work,
)
from app.templating import render
from app.utils import urgency_level

router = APIRouter(dependencies=[Depends(require_owner)])


def _get_request_or_404(db: Session, request_id: int) -> RequestModel:
    req = db.get(RequestModel, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


def _days_text(desired: date | None) -> str:
    if desired is None:
        return ""
    delta = (desired - date.today()).days
    if delta < 0:
        return f"{abs(delta)}d overdue"
    if delta == 0:
        return "due today"
    return f"{delta}d"


def _row_ctx(req: RequestModel, settings_row: AppSettings) -> dict:
    return {
        "req": req,
        "urgency": urgency_level(req.desired_completion_date, settings_row),
        "days_text": _days_text(req.desired_completion_date),
    }


@router.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    submitted = db.scalars(
        select(RequestModel)
        .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.submitted)
        .order_by(RequestModel.created_at)
    ).all()

    in_progress = db.scalars(
        select(RequestModel)
        .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.in_progress)
        .order_by(RequestModel.started_at)
    ).all()

    queued = db.scalars(
        select(RequestModel)
        .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.queued)
        .order_by(RequestModel.queue_position)
    ).all()

    quick_pending = db.scalars(
        select(RequestModel)
        .where(RequestModel.request_class == RequestClass.quick, RequestModel.status == RequestStatus.queued)
        .order_by(RequestModel.created_at)
    ).all()

    needs_info = db.scalars(
        select(RequestModel)
        .where(RequestModel.status == RequestStatus.needs_information)
        .order_by(RequestModel.updated_at)
    ).all()

    csrf_token = get_or_create_csrf_token(request)
    return render(
        request,
        "owner/dashboard.html",
        {
            "owner_nav": True,
            "csrf_token": csrf_token,
            "submitted": [_row_ctx(r, settings_row) for r in submitted],
            "in_progress": [_row_ctx(r, settings_row) for r in in_progress],
            "queued": [_row_ctx(r, settings_row) for r in queued],
            "quick_pending": quick_pending[:8],
            "quick_pending_count": len(quick_pending),
            "needs_info": needs_info,
        },
    )


@router.get("/requests/{request_id}")
def request_detail(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_request_or_404(db, request_id)
    if req.new_information_flag:
        req.new_information_flag = False
        db.commit()
        db.refresh(req)

    csrf_token = get_or_create_csrf_token(request)
    return render(
        request,
        "owner/request_detail.html",
        {
            "owner_nav": True,
            "csrf_token": csrf_token,
            "req": req,
            "urgency": urgency_level(req.desired_completion_date, settings_row),
            "days_text": _days_text(req.desired_completion_date),
        },
    )


@router.post("/requests/{request_id}/accept", dependencies=[Depends(verify_csrf)])
def accept(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_request_or_404(db, request_id)
    accept_and_queue(db, req)
    db.commit()
    flash(request, f"{req.title} accepted and added to the queue.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/decline", dependencies=[Depends(verify_csrf)])
def decline_request(
    request_id: int,
    request: Request,
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    req = _get_request_or_404(db, request_id)
    decline(db, req, reason)
    db.commit()
    flash(request, f"{req.title} declined.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/request-info", dependencies=[Depends(verify_csrf)])
def request_info(
    request_id: int,
    request: Request,
    question: str = Form(...),
    db: Session = Depends(get_db),
):
    req = _get_request_or_404(db, request_id)
    request_information(db, req, question)
    db.commit()
    flash(request, "Information requested. The requester has been notified via their tracking page.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/start", dependencies=[Depends(verify_csrf)])
def start(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_request_or_404(db, request_id)
    start_work(db, req)
    db.commit()
    flash(request, f"Started work on {req.title}.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/complete", dependencies=[Depends(verify_csrf)])
def complete_request(
    request_id: int,
    request: Request,
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    req = _get_request_or_404(db, request_id)
    complete(db, req, note)
    db.commit()
    flash(request, f"{req.title} marked done.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/return-to-queue", dependencies=[Depends(verify_csrf)])
def return_request_to_queue(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_request_or_404(db, request_id)
    return_to_queue(db, req)
    db.commit()
    flash(request, f"{req.title} returned to the queue.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/move-to-long", dependencies=[Depends(verify_csrf)])
def move_request_to_long(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_request_or_404(db, request_id)
    move_to_long_actions(db, req)
    db.commit()
    flash(request, f"{req.title} moved to Long Actions and queued.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/move-up", dependencies=[Depends(verify_csrf)])
def move_request_up(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_request_or_404(db, request_id)
    move_up(db, req)
    db.commit()
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)


@router.post("/requests/{request_id}/move-down", dependencies=[Depends(verify_csrf)])
def move_request_down(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_request_or_404(db, request_id)
    move_down(db, req)
    db.commit()
    return RedirectResponse(url=request.headers.get("referer", "/dashboard"), status_code=303)


@router.post("/requests/{request_id}/reorder", dependencies=[Depends(verify_csrf)])
def reorder(
    request_id: int,
    request: Request,
    position: int = Form(...),
    db: Session = Depends(get_db),
):
    req = _get_request_or_404(db, request_id)
    reorder_queue(db, req, position)
    db.commit()
    return {"ok": True, "queue_position": req.queue_position}
