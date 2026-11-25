from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import commit_and_refresh
from app.deps import get_app_settings, get_db, get_or_create_csrf_token, require_owner, verify_csrf
from app.flash import flash
from app.models import (
    AppSettings,
    EventType,
    Request as RequestModel,
    RequestClass,
    RequestEvent,
    RequestSource,
    RequestStatus,
)
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
from app.schemas import MAX_REASON_LEN
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


def _others_awaiting_review(db: Session, req: RequestModel) -> int:
    """How many *other* requests are awaiting review.

    Excludes `req` whatever its status, so the number beside the triage button
    reads the same before and after an action on this one: how many are left
    after this page.
    """
    return db.scalar(
        select(func.count())
        .select_from(RequestModel)
        .where(RequestModel.status == RequestStatus.submitted, RequestModel.id != req.id)
    )


def _next_submitted_id(db: Session, req: RequestModel) -> int | None:
    """The next request awaiting review after `req`, or None if there is none.

    Ordered by `created_at` like the dashboard's own Awaiting review list, so
    triaging straight through here walks that list in the order it is shown.
    `id` breaks ties, which the bulk email sweep produces routinely -- several
    candidates POSTed in one run share a timestamp.

    Wraps around to the oldest one still submitted when `req` is the last in
    that order, so Skip on the final item does not dead-end: anything skipped
    earlier in the pass comes back round. Once nothing else is submitted this
    returns None and the button is not rendered at all.
    """
    base = select(RequestModel).where(
        RequestModel.status == RequestStatus.submitted,
        RequestModel.id != req.id,
    )
    after = db.scalars(
        base.where(
            or_(
                RequestModel.created_at > req.created_at,
                and_(RequestModel.created_at == req.created_at, RequestModel.id > req.id),
            )
        )
        .order_by(RequestModel.created_at, RequestModel.id)
        .limit(1)
    ).first()
    if after is not None:
        return after.id
    first = db.scalars(base.order_by(RequestModel.created_at, RequestModel.id).limit(1)).first()
    return first.id if first is not None else None


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
            # Triage navigation: lets Accept/Decline/hold be followed by the
            # next item without a detour through the dashboard. Computed on
            # every detail view, not just while this one is submitted, so the
            # button is already there on the page an action redirects back to.
            "next_review_id": _next_submitted_id(db, req),
            # Whether to offer the date form at all. Decided here rather
            # than by listing statuses in the template, so the rule the
            # route enforces and the one the page shows cannot drift.
            "can_edit_date": req.status in OPEN_STATUSES,
            "others_awaiting_review": _others_awaiting_review(db, req),
        },
    )


@router.post("/requests/{request_id}/date", dependencies=[Depends(verify_csrf)])
def set_date(
    request_id: int,
    request: Request,
    desired_completion_date: str = Form(""),
    desired_date_reason: str = Form(""),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    """Sets, changes or clears the desired completion date by hand.

    The capture gate reads a date out of the mail and routinely does not
    find one: it is in an attachment, or in a sentence the gate is
    deliberately too narrow to count. `/intake/requests/correct` can
    revise a capture from the sweep's side, but only while that mail is
    still in front of it -- noticing days later, or being told the date in
    a corridor, left no route in at all short of editing the database.

    Open requests only, on the same reasoning the correction endpoint
    refuses a closed one: re-dating something already declined, done or
    cancelled reopens a decision rather than records a fact.
    `OPEN_STATUSES` is exactly that set.

    Not a state transition, so it is not in `state_machine`: the status is
    untouched, and a request with no date is in no different a state from
    one with a date. Nothing is sent to the requester either -- the date
    and its reason are both already on their tracking page, so the change
    shows up there without an email claiming more significance than a
    correction has.
    """
    req = _get_request_or_404(db, request_id)
    if req.status not in OPEN_STATUSES:
        flash(
            request,
            f"{req.public_number} is {req.status.value}; a closed request is not re-dated.",
            "error",
        )
        return RedirectResponse(url=f"/requests/{request_id}", status_code=303)

    raw = desired_completion_date.strip()
    new_date: date | None = None
    if raw:
        try:
            new_date = date.fromisoformat(raw)
        except ValueError:
            # Only reachable from a hand-made POST; the browser's own date
            # input cannot submit this.
            flash(request, "That date could not be read.", "error")
            return RedirectResponse(url=f"/requests/{request_id}", status_code=303)

    reason = desired_date_reason.strip()
    if len(reason) > MAX_REASON_LEN:
        flash(request, "That reason is too long.", "error")
        return RedirectResponse(url=f"/requests/{request_id}", status_code=303)
    # The same rule the public form, the capture path and the correction
    # endpoint all enforce: a recorded date carries how it was arrived at,
    # because it is so often not the date anybody stated -- an effective
    # date computed back from a stated one, or a working day chosen next to
    # a weekend deadline.
    if new_date is not None and not reason:
        flash(request, "A date needs a reason recording how it was set.", "error")
        return RedirectResponse(url=f"/requests/{request_id}", status_code=303)
    # Clearing the date clears the reason with it. A reason left behind
    # explains a date that is no longer there, and would then be shown on
    # the tracking page under no date at all.
    if new_date is None:
        reason = ""

    was_date = req.desired_completion_date
    was_reason = req.desired_date_reason or ""
    if new_date == was_date and reason == was_reason:
        flash(request, "The date is unchanged.", "info")
        return RedirectResponse(url=f"/requests/{request_id}", status_code=303)

    req.desired_completion_date = new_date
    req.desired_date_reason = reason or None
    # Recorded for the same reason the correction endpoint requires a note:
    # a date that changes with nothing on the timeline is indistinguishable
    # from a bug, and this one feeds the calendar feed and the urgency
    # colours on the dashboard.
    db.add(
        RequestEvent(
            request_id=req.id,
            event_type=EventType.date_changed,
            event_metadata={
                "source": "owner_edit",
                "from": was_date.isoformat() if was_date else None,
                "to": new_date.isoformat() if new_date else None,
                "reason": reason or None,
            },
        )
    )
    commit_and_refresh(db, req, settings_row)
    if new_date is None:
        flash(request, f"Date cleared on {req.title}.", "success")
    else:
        flash(request, f"Date set to {new_date.strftime('%B %-d, %Y')} on {req.title}.", "success")
    return RedirectResponse(url=f"/requests/{request_id}", status_code=303)


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
