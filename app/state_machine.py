"""Single source of truth for Request status transitions (spec §34).

Every function here takes an already-loaded `Request` and mutates it in
place, appends a `RequestEvent`, and leaves the caller responsible for
`db.commit()`. This keeps each transition transactionally atomic with
whatever else the caller needs to do.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    EventType,
    InformationRequest,
    Request,
    RequestClass,
    RequestEvent,
    RequestStatus,
)


class TransitionError(Exception):
    """Raised when a requested transition is not valid for the request's current state."""


def _now() -> datetime:
    return datetime.now(UTC)


def _log(db: Session, req: Request, event_type: EventType, metadata: dict | None = None) -> None:
    db.add(RequestEvent(request_id=req.id, event_type=event_type, event_metadata=metadata))


def _queued_siblings(db: Session, request_class: RequestClass, exclude_id: int | None = None) -> list[Request]:
    stmt = (
        select(Request)
        .where(Request.request_class == request_class, Request.status == RequestStatus.queued)
        .order_by(Request.queue_position)
    )
    rows = list(db.scalars(stmt))
    if exclude_id is not None:
        rows = [r for r in rows if r.id != exclude_id]
    return rows


def _insert_into_queue(db: Session, req: Request, target_position: int | None) -> None:
    """Place `req` into the queue for its class at (as close as practical to)
    `target_position`, 1-indexed. `None` or an out-of-range value appends to the bottom.
    Renumbers all queued siblings contiguously.
    """
    siblings = _queued_siblings(db, req.request_class, exclude_id=req.id)
    if target_position is None:
        index = len(siblings)
    else:
        index = max(0, min(target_position - 1, len(siblings)))
    siblings.insert(index, req)
    for position, sibling in enumerate(siblings, start=1):
        sibling.queue_position = position
    req.status = RequestStatus.queued


def _remove_from_queue_and_renumber(db: Session, req: Request) -> None:
    siblings = _queued_siblings(db, req.request_class, exclude_id=req.id)
    req.queue_position = None
    for position, sibling in enumerate(siblings, start=1):
        sibling.queue_position = position


# ---------------------------------------------------------------------------
# Long Action transitions
# ---------------------------------------------------------------------------


def accept_and_queue(db: Session, req: Request) -> None:
    if req.request_class != RequestClass.long or req.status != RequestStatus.submitted:
        raise TransitionError("Only a Submitted Long Action can be accepted.")
    _insert_into_queue(db, req, target_position=None)  # bottom of queue
    _log(db, req, EventType.accepted)


def decline(db: Session, req: Request, reason: str) -> None:
    if req.request_class != RequestClass.long or req.status != RequestStatus.submitted:
        raise TransitionError("Only a Submitted Long Action can be declined.")
    if not reason or not reason.strip():
        raise TransitionError("A decline reason is required.")
    req.status = RequestStatus.declined
    req.decline_reason = reason.strip()
    req.declined_at = _now()
    _log(db, req, EventType.declined, {"reason": reason.strip()})


def start_work(db: Session, req: Request) -> None:
    if req.request_class != RequestClass.long or req.status != RequestStatus.queued:
        raise TransitionError("Only a Queued Long Action can be started.")
    req.previous_queue_position = req.queue_position
    _remove_from_queue_and_renumber(db, req)
    req.status = RequestStatus.in_progress
    req.started_at = _now()
    _log(db, req, EventType.started)


def return_to_queue(db: Session, req: Request) -> None:
    if req.status != RequestStatus.in_progress:
        raise TransitionError("Only an In Progress request can be returned to the queue.")
    _insert_into_queue(db, req, target_position=req.previous_queue_position)
    req.previous_queue_position = None
    _log(db, req, EventType.returned_to_queue)


def complete(db: Session, req: Request, note: str | None = None) -> None:
    if req.status not in (RequestStatus.queued, RequestStatus.in_progress):
        raise TransitionError("Only a Queued or In Progress request can be completed.")
    if req.status == RequestStatus.queued:
        _remove_from_queue_and_renumber(db, req)
    req.status = RequestStatus.done
    req.completed_at = _now()
    if note and note.strip():
        req.completion_note = note.strip()
    _log(db, req, EventType.completed)


# ---------------------------------------------------------------------------
# Needs Information (shared by both classes) — spec §16, §57
# ---------------------------------------------------------------------------

_INFO_ELIGIBLE = {
    RequestStatus.submitted,
    RequestStatus.queued,
    RequestStatus.in_progress,
}


def request_information(db: Session, req: Request, question: str) -> None:
    if req.status not in _INFO_ELIGIBLE:
        raise TransitionError("Information can only be requested for an active request.")
    if not question or not question.strip():
        raise TransitionError("A question is required when requesting information.")

    req.previous_status = req.status
    if req.status == RequestStatus.queued:
        req.previous_queue_position = req.queue_position
        _remove_from_queue_and_renumber(db, req)

    req.status = RequestStatus.needs_information
    db.add(InformationRequest(request_id=req.id, owner_question=question.strip()))
    _log(db, req, EventType.info_requested, {"question": question.strip()})
    # Flush so `req.info_requests` reflects the new row immediately for any
    # caller that reads it later in the same session (the app session is
    # configured with autoflush=False).
    db.flush()


def respond_to_information(db: Session, req: Request, response: str) -> None:
    if req.status != RequestStatus.needs_information:
        raise TransitionError("This request is not waiting on requester information.")
    if not response or not response.strip():
        raise TransitionError("A response is required.")
    if req.previous_status is None:
        raise TransitionError("No previous state recorded; cannot restore request.")

    open_info_request = next(
        (ir for ir in reversed(req.info_requests) if ir.responded_at is None), None
    )
    if open_info_request is None:
        raise TransitionError("No open information request found.")
    open_info_request.requester_response = response.strip()
    open_info_request.responded_at = _now()

    restored_status = req.previous_status
    if restored_status == RequestStatus.queued:
        _insert_into_queue(db, req, target_position=req.previous_queue_position)
        req.previous_queue_position = None
    else:
        req.status = restored_status

    req.previous_status = None
    req.new_information_flag = True
    _log(db, req, EventType.info_responded, {"response": response.strip()})


# ---------------------------------------------------------------------------
# Withdrawal (requester-initiated, any class) — spec §18
# ---------------------------------------------------------------------------

_WITHDRAWABLE = {
    RequestStatus.submitted,
    RequestStatus.queued,
    RequestStatus.in_progress,
    RequestStatus.needs_information,
}


def withdraw(db: Session, req: Request) -> None:
    if req.status not in _WITHDRAWABLE:
        raise TransitionError("This request can no longer be withdrawn.")
    if req.status == RequestStatus.queued:
        _remove_from_queue_and_renumber(db, req)
    req.status = RequestStatus.withdrawn
    req.withdrawn_at = _now()
    req.previous_status = None
    req.previous_queue_position = None
    _log(db, req, EventType.withdrawn)


# ---------------------------------------------------------------------------
# Quick Action specific transitions — spec §11, §63
# ---------------------------------------------------------------------------


def move_to_long_actions(db: Session, req: Request) -> None:
    if req.request_class != RequestClass.quick:
        raise TransitionError("Only a Quick Action can be moved to Long Actions.")
    if req.status not in (RequestStatus.queued, RequestStatus.in_progress):
        raise TransitionError("Only a Queued or In Progress Quick Action can be moved.")
    req.request_class = RequestClass.long
    req.started_at = None
    _insert_into_queue(db, req, target_position=None)  # bottom of Long queue
    _log(db, req, EventType.moved_to_long)


# ---------------------------------------------------------------------------
# Queue reordering (not a status transition) — spec §49
# ---------------------------------------------------------------------------


def reorder_queue(db: Session, req: Request, new_position: int) -> None:
    if req.status != RequestStatus.queued:
        raise TransitionError("Only a queued request can be reordered.")
    from_position = req.queue_position
    _insert_into_queue(db, req, target_position=new_position)
    _log(db, req, EventType.reordered, {"from": from_position, "to": req.queue_position})


def move_up(db: Session, req: Request) -> None:
    if req.status != RequestStatus.queued or req.queue_position is None:
        raise TransitionError("Only a queued request can be reordered.")
    reorder_queue(db, req, req.queue_position - 1)


def move_down(db: Session, req: Request) -> None:
    if req.status != RequestStatus.queued or req.queue_position is None:
        raise TransitionError("Only a queued request can be reordered.")
    reorder_queue(db, req, req.queue_position + 1)
