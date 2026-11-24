from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import commit_and_refresh
from app.deps import get_app_settings, get_db, get_or_create_csrf_token, require_owner, verify_csrf
from app.flash import flash
from app.models import AppSettings, Request as RequestModel, RequestClass, RequestSource, RequestStatus
from app.notifications import (
    notify_requester_accepted,
    notify_requester_blocked,
    notify_requester_cancelled,
    notify_requester_completed,
    notify_requester_declined,
    notify_requester_needs_information,
)
from app.state_machine import (
    accept_and_queue,
    block,
    cancel,
    complete,
    decline,
    move_down,
    move_to_long_actions,
    move_up,
    reorder_queue,
    request_information,
    return_to_queue,
    start_work,
    unblock,
)
from app.templating import render
from app.utils import urgency_level

router = APIRouter(dependencies=[Depends(require_owner)])

# Statuses a request passes through while it is still live work. Used for
# the per-source counts beside the dashboard's filter, so the numbers track
# what's actually on the plate rather than everything ever submitted.
OPEN_STATUSES = (
    RequestStatus.submitted,
    RequestStatus.queued,
    RequestStatus.in_progress,
    RequestStatus.needs_information,
    RequestStatus.blocked,
)


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


def _source_filter(source: str) -> RequestSource | None:
    """Parses the dashboard's ?source= query parameter.

    Anything unrecognised (including the empty string) means "no filter",
    so a stale or hand-edited URL degrades to showing everything rather
    than to an error page or an empty dashboard.
    """
    try:
        return RequestSource(source)
    except ValueError:
        return None


def _row_ctx(req: RequestModel, settings_row: AppSettings) -> dict:
    return {
        "req": req,
        "urgency": urgency_level(req.desired_completion_date, settings_row),
        "days_text": _days_text(req.desired_completion_date),
    }


@router.get("/dashboard")
def dashboard(
    request: Request,
    source: str = "",
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    active_source = _source_filter(source)

    def scoped(stmt):
        """Applies the source filter, if one is active, to a section query."""
        if active_source is None:
            return stmt
        return stmt.where(RequestModel.source == active_source)

    # Awaiting review is not restricted to Long Actions: a server-side
    # insert (scripts/add_request.py) can leave a Quick Action at
    # `submitted` so it gets the same accept/decline review as anything
    # else, and those would otherwise be invisible here.
    submitted = db.scalars(
        scoped(
            select(RequestModel)
            .where(RequestModel.status == RequestStatus.submitted)
            .order_by(RequestModel.created_at)
        )
    ).all()

    in_progress = db.scalars(
        scoped(
            select(RequestModel)
            .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.in_progress)
            .order_by(RequestModel.started_at)
        )
    ).all()

    queued = db.scalars(
        scoped(
            select(RequestModel)
            .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.queued)
            .order_by(RequestModel.queue_position)
        )
    ).all()

    quick_pending = db.scalars(
        scoped(
            select(RequestModel)
            .where(RequestModel.request_class == RequestClass.quick, RequestModel.status == RequestStatus.queued)
            .order_by(RequestModel.created_at)
        )
    ).all()

    needs_info = db.scalars(
        scoped(
            select(RequestModel)
            .where(RequestModel.status == RequestStatus.needs_information)
            .order_by(RequestModel.updated_at)
        )
    ).all()

    # Ordered by the chase date, nulls last, so the ones that have been
    # waiting past their nudge date sit at the top where they get seen. A
    # hold with no date still appears — it just has nothing pulling it up.
    blocked = db.scalars(
        scoped(
            select(RequestModel)
            .where(RequestModel.status == RequestStatus.blocked)
            .order_by(RequestModel.blocked_until.is_(None), RequestModel.blocked_until)
        )
    ).all()

    source_counts = {
        s.value: db.scalar(
            select(func.count())
            .select_from(RequestModel)
            .where(RequestModel.source == s, RequestModel.status.in_(OPEN_STATUSES))
        )
        for s in RequestSource
    }

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
            "blocked": [
                {"req": r, "overdue": r.blocked_until is not None and r.blocked_until <= date.today()}
                for r in blocked
            ],
            "active_source": active_source.value if active_source else "",
            "source_counts": source_counts,
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
        commit_and_refresh(db, req)

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
def accept(
    request_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_request_or_404(db, request_id)
    accept_and_queue(db, req)
    commit_and_refresh(db, req, settings_row)
    background_tasks.add_task(notify_requester_accepted, str(request.base_url), req, settings_row)
    flash(request, f"{req.title} accepted and added to the queue.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/decline", dependencies=[Depends(verify_csrf)])
def decline_request(
    request_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    # Not Form(...): whether a reason is required depends on the request,
    # not on the route. state_machine.decline enforces it where it applies
    # — when there is a requester who will be sent it.
    req = _get_request_or_404(db, request_id)
    decline(db, req, reason)
    commit_and_refresh(db, req, settings_row)
    background_tasks.add_task(notify_requester_declined, str(request.base_url), req, settings_row)
    flash(request, f"{req.title} declined.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/request-info", dependencies=[Depends(verify_csrf)])
def request_info(
    request_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    question: str = Form(...),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_request_or_404(db, request_id)
    request_information(db, req, question)
    commit_and_refresh(db, req, settings_row)
    background_tasks.add_task(
        notify_requester_needs_information, str(request.base_url), req, question.strip(), settings_row
    )
    flash(request, "Information requested. The requester has been notified via their tracking page.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/block", dependencies=[Depends(verify_csrf)])
def block_request(
    request_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    waiting_on: str = Form(...),
    until: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_request_or_404(db, request_id)
    # Parsed here rather than typed as `date | None` on the parameter,
    # because an empty string is what an untouched date input posts and
    # FastAPI would 422 on it. A malformed one is treated as absent: the
    # chase date is a convenience, and refusing the whole hold over it
    # would be the wrong trade.
    try:
        chase_date = date.fromisoformat(until) if until.strip() else None
    except ValueError:
        chase_date = None
    block(db, req, waiting_on, chase_date, note)
    commit_and_refresh(db, req, settings_row)
    background_tasks.add_task(notify_requester_blocked, str(request.base_url), req, settings_row)
    flash(request, f"{req.title} is on hold, waiting on {req.blocked_on}.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/unblock", dependencies=[Depends(verify_csrf)])
def unblock_request(request_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_request_or_404(db, request_id)
    unblock(db, req)
    db.commit()
    flash(request, f"{req.title} is off hold.", "success")
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
    background_tasks: BackgroundTasks,
    note: str = Form(""),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_request_or_404(db, request_id)
    complete(db, req, note)
    commit_and_refresh(db, req, settings_row)
    background_tasks.add_task(notify_requester_completed, str(request.base_url), req, settings_row)
    flash(request, f"{req.title} marked done.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/cancel", dependencies=[Depends(verify_csrf)])
def cancel_request(
    request_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    """Closes a request that no longer applies, without claiming it was done.

    The notification no-ops when there is no requester, so cancelling one
    of the Owner's own generated occurrences mails nobody.
    """
    req = _get_request_or_404(db, request_id)
    cancel(db, req, reason)
    commit_and_refresh(db, req, settings_row)
    background_tasks.add_task(notify_requester_cancelled, str(request.base_url), req, settings_row)
    flash(request, f"{req.title} cancelled.", "success")
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
