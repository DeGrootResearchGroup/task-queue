"""Single source of truth for Request status transitions (spec §34).

Every function here takes an already-loaded `Request` and mutates it in
place, appends a `RequestEvent`, and leaves the caller responsible for
`db.commit()`. This keeps each transition transactionally atomic with
whatever else the caller needs to do.
"""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    EventType,
    InformationRequest,
    Request,
    RequestClass,
    RequestEvent,
    RequestStatus,
    RequestUpdate,
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


def _clear_block_fields(req: Request) -> None:
    """Drops the "waiting on" detail when a request stops being live.

    Same reasoning as clearing `previous_status`: these describe a hold that
    is on its way back, and a withdrawn or cancelled request is not. The
    event log still has what it was waiting on.
    """
    req.blocked_on = None
    req.blocked_until = None
    req.blocked_note = None
    req.blocked_at = None


# ---------------------------------------------------------------------------
# Long Action transitions
# ---------------------------------------------------------------------------


def accept_and_queue(db: Session, req: Request) -> None:
    # Keyed on status alone, not status + class. Public form submissions
    # still send Quick Actions straight to `queued` and never reach here,
    # but a server-side insert can deliberately park a Quick Action at
    # `submitted` so it gets the same review step; accepting one puts it at
    # the bottom of the Quick queue, which is exactly the right outcome.
    if req.status != RequestStatus.submitted:
        raise TransitionError("Only a Submitted request can be accepted.")
    if req.request_class == RequestClass.event:
        # An event is not queued work. Its time is already fixed, so
        # ordering it against the Quick and Long queues would be
        # meaningless -- and worse, it would pad the prioritised list the
        # queue exists to produce. It goes to `queued` all the same,
        # because that is the status the rest of the system reads as
        # "live": the mail sweep keeps its flag set, and the write step
        # picks it up from there.
        req.status = RequestStatus.queued
        req.queue_position = None
    else:
        _insert_into_queue(db, req, target_position=None)  # bottom of queue
    _log(db, req, EventType.accepted)


def decline(db: Session, req: Request, reason: str | None = None) -> None:
    """Refuses a submitted request.

    The reason is required only when there is a requester to receive it.
    It goes into the notification they get, and declining somebody's
    request with no explanation would be rude — but an `email` or `manual`
    request is one of the Owner's own, with nobody on the other end, and
    demanding a justification nobody will read is pure friction on a path
    that has to stay fast. Declining is an expected outcome of triage, not
    an exception.

    The predicate is `requester_email`, not `source`: that is exactly the
    condition under which the reason has an audience. Keying on
    `source == form` would give the same answer today, for the wrong
    reason, and would be wrong the first time something is captured on
    somebody else's behalf.

    A reason given is always stored, audience or not — it is worth having
    in the archive either way. Compare `cancel`, where it is always
    optional.
    """
    if req.status != RequestStatus.submitted:
        raise TransitionError("Only a Submitted request can be declined.")
    reason = reason.strip() if reason else ""
    if not reason and req.requester_email:
        raise TransitionError("A decline reason is required.")
    req.status = RequestStatus.declined
    req.decline_reason = reason or None
    req.declined_at = _now()
    _log(db, req, EventType.declined, {"reason": reason} if reason else None)


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
    # Keyed on `requester_email` for the same reason `decline` is: that is
    # exactly the condition under which the question has an audience. Without
    # one this transition is a trap — the notification no-ops, and the only
    # exit is `respond_to_information`, reachable solely from the requester's
    # public tracking page. The request would sit in `needs_information`
    # waiting on somebody who does not exist. `block` is the transition that
    # was actually wanted.
    if not req.requester_email:
        raise TransitionError(
            "There is no requester to ask. Put the request on hold instead, "
            "naming who is being waited on."
        )
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
# Blocked — waiting on somebody outside the queue
# ---------------------------------------------------------------------------

# The same three statuses `request_information` accepts. A request can stall
# on somebody else at any point while it is live: before it is even accepted
# (the thing it asks for depends on a form a student has not sent), once it
# is queued, or halfway through the work.
_BLOCK_ELIGIBLE = {
    RequestStatus.submitted,
    RequestStatus.queued,
    RequestStatus.in_progress,
}


def block(db: Session, req: Request, waiting_on: str, until: date | None = None, note: str | None = None) -> None:
    """Parks a live request because a third party owes something.

    Deliberately *not* `needs_information`. That status means a registered
    requester was asked a question and can answer it on their tracking page
    — `respond_to_information` is reachable from nowhere else. Applied to a
    request captured from the Owner's own mailbox it is a trap: there is no
    requester, the notification is a no-op, and the only exit is the Owner
    opening the public tracking URL and role-playing the person who does not
    exist.

    What was actually missing is this: the thing is real, accepted and still
    wanted, but it cannot move until somebody outside the system acts. The
    Owner releases it, because the Owner is the only one who will know.

    `waiting_on` is required. A hold that does not name its second party
    cannot be chased, and an unchaseable hold is a dropped task with a
    tidier label.
    """
    if req.status not in _BLOCK_ELIGIBLE:
        raise TransitionError("Only an active request can be put on hold.")
    if not waiting_on or not waiting_on.strip():
        raise TransitionError("Say who or what is being waited on.")

    req.previous_status = req.status
    if req.status == RequestStatus.queued:
        req.previous_queue_position = req.queue_position
        _remove_from_queue_and_renumber(db, req)

    req.status = RequestStatus.blocked
    req.blocked_on = waiting_on.strip()
    req.blocked_until = until
    req.blocked_note = note.strip() if note and note.strip() else None
    req.blocked_at = _now()
    _log(
        db,
        req,
        EventType.blocked,
        {"waiting_on": req.blocked_on, "until": until.isoformat() if until else None},
    )


def unblock(db: Session, req: Request) -> None:
    """Returns a blocked request to exactly where it was waiting.

    Mirrors `respond_to_information`, including the queue-position restore,
    so a long action that was third in line goes back to third rather than
    to the bottom. Being blocked was not its fault.
    """
    if req.status != RequestStatus.blocked:
        raise TransitionError("This request is not on hold.")
    if req.previous_status is None:
        raise TransitionError("No previous state recorded; cannot restore request.")

    restored_status = req.previous_status
    if restored_status == RequestStatus.queued:
        _insert_into_queue(db, req, target_position=req.previous_queue_position)
        req.previous_queue_position = None
    else:
        req.status = restored_status

    req.previous_status = None
    was_waiting_on = req.blocked_on
    # Cleared so the next hold starts clean and nothing reads a stale
    # "waiting on" off a request that is moving again. The event log keeps
    # the history; these fields describe the present.
    req.blocked_on = None
    req.blocked_until = None
    req.blocked_note = None
    req.blocked_at = None
    _log(db, req, EventType.unblocked, {"was_waiting_on": was_waiting_on} if was_waiting_on else None)


# ---------------------------------------------------------------------------
# Statuses in which a request is still "open" — the requester can still
# withdraw it or add information to it. Anything outside this set (done,
# declined, withdrawn) is closed and shouldn't accept further requester
# input through any route.
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = {
    RequestStatus.submitted,
    RequestStatus.queued,
    RequestStatus.in_progress,
    RequestStatus.needs_information,
    RequestStatus.blocked,
}


# ---------------------------------------------------------------------------
# Withdrawal (requester-initiated, any class) — spec §18
# ---------------------------------------------------------------------------


def withdraw(db: Session, req: Request) -> None:
    if req.status not in _ACTIVE_STATUSES:
        raise TransitionError("This request can no longer be withdrawn.")
    if req.status == RequestStatus.queued:
        _remove_from_queue_and_renumber(db, req)
    req.status = RequestStatus.withdrawn
    req.withdrawn_at = _now()
    req.previous_status = None
    req.previous_queue_position = None
    _clear_block_fields(req)
    _log(db, req, EventType.withdrawn)


# ---------------------------------------------------------------------------
# Cancellation (Owner-initiated, any active status)
# ---------------------------------------------------------------------------


def cancel(db: Session, req: Request, reason: str | None = None) -> None:
    """Closes a request because the occasion for it went away.

    A fourth terminal state, rather than reusing one of the three that
    existed, because none of them is true here:

    - `done` asserts the work happened. A month with no PCard transactions
      to reconcile is not a month in which the reconciliation was
      performed, and recording it as one makes the archive lie.
    - `declined` is the Owner refusing a request somebody else made. It
      carries a required reason addressed to that person, and it is only
      reachable from `submitted`, so an occurrence already accepted into
      the queue could not use it in any case.
    - `withdrawn` is requester-initiated retraction from the public
      tracking page. A generated occurrence has no requester to retract it.

    Reusing any of them would have made the archive's record untrue, which
    is the one thing a cancellation has to avoid.

    The reason is optional. "No transactions this month" is a routine
    non-event, and demanding a sentence about it every cycle is how an
    honest state stops getting used.

    Reachable from any active status, unlike `decline`, because an
    occurrence can stop applying at any point before it is finished.
    """
    if req.status not in _ACTIVE_STATUSES:
        raise TransitionError("Only an active request can be cancelled.")
    if req.status == RequestStatus.queued:
        _remove_from_queue_and_renumber(db, req)
    req.status = RequestStatus.cancelled
    req.cancelled_at = _now()
    req.cancel_reason = reason.strip() if reason and reason.strip() else None
    # Cleared for the same reason withdraw() clears them: they only mean
    # something while a request is on its way back, and this one is not.
    req.previous_status = None
    req.previous_queue_position = None
    _clear_block_fields(req)
    _log(db, req, EventType.cancelled, {"reason": req.cancel_reason} if req.cancel_reason else None)


# ---------------------------------------------------------------------------
# Requester-provided additional information — spec §19
# ---------------------------------------------------------------------------


def add_information(db: Session, req: Request, content: str) -> None:
    if req.status not in _ACTIVE_STATUSES:
        raise TransitionError("This request is no longer active; information can't be added to it.")
    if not content or not content.strip():
        raise TransitionError("Additional information is required.")
    db.add(RequestUpdate(request_id=req.id, author_type="requester", content=content.strip()))
    req.new_information_flag = True
    _log(db, req, EventType.information_added)


# ---------------------------------------------------------------------------
# Quick Action specific transitions — spec §11, §63
# ---------------------------------------------------------------------------


def move_to_long_actions(db: Session, req: Request) -> None:
    if req.request_class != RequestClass.quick:
        raise TransitionError("Only a Quick Action can be moved to Long Actions.")
    if req.status not in (RequestStatus.queued, RequestStatus.in_progress):
        raise TransitionError("Only a Queued or In Progress Quick Action can be moved.")
    if req.status == RequestStatus.queued:
        # Must renumber the old (Quick) queue while req.request_class still
        # reflects it — _remove_from_queue_and_renumber filters siblings by
        # req.request_class, so this has to happen before it's reassigned
        # below, or the Quick queue is left with a permanent position gap.
        _remove_from_queue_and_renumber(db, req)
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
