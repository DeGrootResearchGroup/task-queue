"""The machine-facing intake endpoint, for the mail sweep.

`POST /intake/requests` takes a batch of items and creates one request per
item at `submitted`, which is the review step the Owner already uses. The
sweep runs unattended, so three properties matter more here than anywhere
else in the app:

**It cannot mint anything the Owner has not seen.** Status is always
`submitted` and `source` is restricted to `email` or `manual`, so a leaked
token buys junk in "Awaiting review" and nothing more. There is no field
through which a caller can set a status, a queue position or a `form`
source; the request model forbids unknown fields outright rather than
ignoring them, so an attempt to is an error rather than a silent no-op.

**It is idempotent.** Each item may carry a `dedupe_key` — the RFC
message-id of the originating mail is the natural value — stored under a
unique constraint. A re-run, or two sweeps that both see the same message,
reports `duplicate` and creates nothing. Without this, automated capture
produces exactly the pile of near-identical copies that sank the previous
email pipeline.

**One bad item does not sink the batch.** Each item is created inside its
own savepoint and reported on individually, so a malformed row is rejected
while its neighbours are stored.

The route is registered only when `INTAKE_TOKEN_HASH` is set (see
`app/main.py`), so an unconfigured instance has no such path at all. A
wrong or missing token gets the same 404 as a path that does not exist,
for the reason the calendar feed does it: a probe learns nothing about
whether there is anything here to find.

Only the token's SHA-256 digest lives on the server, so a leaked .env or
a copied database yields nothing that can be presented here.
"""

import base64
import binascii
import json
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.intake import IntakeError, Link, create_request
from app.models import (
    EventType,
    Request as RequestModel,
    RequestClass,
    RequestEvent,
    RequestSource,
    RequestStatus,
)
from app.rate_limit import limiter
from app.security import intake_token_matches
from app.schemas import (
    MAX_CONTEXT_LEN,
    MAX_DEDUPE_KEY_LEN,
    MAX_DESCRIPTION_LEN,
    MAX_LINK_LABEL_LEN,
    MAX_LINK_URL_LEN,
    MAX_LINKS,
    MAX_REASON_LEN,
    MAX_TITLE_LEN,
)

router = APIRouter()

_settings = get_settings()

# A sweep of a week's mail is a handful of items, not hundreds. Both caps
# exist so an unattended caller that goes wrong — or an attacker with the
# token — can't turn one request into unbounded work.
MAX_INTAKE_ITEMS = 50
MAX_INTAKE_BODY_BYTES = 256 * 1024

DEFAULT_STATUS_LIMIT = 200
MAX_STATUS_LIMIT = 500

# The coarse state the caller actually acts on, owned here so a client
# never has to encode this app's state machine. Adding a RequestStatus
# without adding it here is a test failure, not a client that silently
# starts seeing nulls.
DISPOSITIONS: dict[RequestStatus, str] = {
    RequestStatus.submitted: "awaiting_review",
    RequestStatus.queued: "active",
    RequestStatus.in_progress: "active",
    RequestStatus.needs_information: "active",
    # Active, emphatically. A blocked request is still live work waiting on
    # somebody, so the mail sweep must keep its flag set and leave the
    # originating message in the Inbox. Calling it "closed" would file away
    # the one message that proves what is being waited for.
    RequestStatus.blocked: "active",
    RequestStatus.done: "closed",
    RequestStatus.declined: "closed",
    RequestStatus.withdrawn: "closed",
    RequestStatus.cancelled: "closed",
}


class IntakeLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=MAX_LINK_URL_LEN)
    label: str | None = Field(default=None, max_length=MAX_LINK_LABEL_LEN)


class IntakeItem(BaseModel):
    # extra="forbid" so a caller trying to set something this endpoint does
    # not offer — `status`, `queue_position`, `requester_email` — is told
    # so, rather than having it quietly dropped.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LEN)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LEN)
    request_class: RequestClass
    # Not RequestSource: `form` means a human filled in the public form, and
    # nothing arriving over HTTP from the sweep did.
    source: RequestSource = RequestSource.email
    desired_completion_date: date | None = None
    desired_date_reason: str | None = Field(default=None, max_length=MAX_REASON_LEN)
    additional_context: str | None = Field(default=None, max_length=MAX_CONTEXT_LEN)
    links: list[IntakeLink] = Field(default_factory=list, max_length=MAX_LINKS)
    dedupe_key: str | None = Field(default=None, max_length=MAX_DEDUPE_KEY_LEN)

    # Events only. `calendar_uid` is deliberately NOT accepted -- it is set
    # by the write step once the event is really on the calendar, and a
    # caller supplying one would let an event be marked written without
    # anything having been written.
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _check_event_times(self) -> "IntakeItem":
        if self.request_class == RequestClass.event:
            if self.starts_at is None or self.ends_at is None:
                raise ValueError("An event needs both starts_at and ends_at.")
            if self.ends_at <= self.starts_at:
                raise ValueError("An event's ends_at must be after its starts_at.")
        elif self.starts_at is not None or self.ends_at is not None or self.location is not None:
            raise ValueError("starts_at, ends_at and location apply only to request_class 'event'.")
        return self


def _json_error(status_code: int, detail: str) -> JSONResponse:
    # JSON rather than raising HTTPException: the app's global handler
    # renders error.html, which is right for the browser-facing pages and
    # useless to the script calling this one.
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _not_found() -> JSONResponse:
    return _json_error(404, "Not found.")


def _token_ok(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    # The server holds only a digest, never the token itself: the presented
    # value is hashed and the digests compared in constant time. See
    # app.security.hash_intake_token.
    return intake_token_matches(presented.strip(), _settings.intake_token_hash)


async def _read_capped_body(request: Request) -> bytes | None:
    """Reads the body, giving up as soon as it exceeds the cap.

    Streamed rather than `await request.body()` so an oversized payload is
    abandoned part-way instead of being buffered in full and measured
    afterwards. Returns None when the cap is hit.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_INTAKE_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "body"
    return f"{location}: {error['msg']}"


def _jsonable(value):
    """Dates and datetimes into the audit metadata, which is a JSON column."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _found(req: RequestModel, index: int, status: str) -> dict:
    return {
        "index": index,
        "status": status,
        "request_id": req.id,
        "public_number": req.public_number,
        "dedupe_key": req.dedupe_key,
    }


# How far back to look for an identical title when deciding whether an
# item is the same announcement arriving twice.
DOUBLE_SEND_WINDOW = timedelta(hours=36)


def _possible_double_send(db: Session, item: IntakeItem) -> str | None:
    """Returns the public number of a recent request with the same title, if any.

    `dedupe_key` does NOT cover this case and cannot. It stops one message
    being captured twice; it does nothing about one announcement being
    SENT twice, because two real copies are two messages with two distinct
    RFC message-ids and therefore two distinct keys. Observed live: the
    Dean's "WE Monthly Coffee Hour" arrived twice, four minutes apart, both
    copies genuine.

    This only reports. It does not refuse, and that asymmetry is
    deliberate: a duplicate request costs one click to cancel, while a
    wrongly-blocked capture is an obligation that silently never arrives.
    Titles also legitimately repeat -- every month's PCard reconciliation
    carries the same one -- which is what the time window is for, and why
    a stricter rule here would start eating real work.
    """
    if not item.title.strip():
        return None
    cutoff = datetime.now(UTC).replace(tzinfo=None) - DOUBLE_SEND_WINDOW
    twin = db.scalar(
        select(RequestModel)
        .where(
            RequestModel.title == item.title,
            RequestModel.source == item.source,
            RequestModel.created_at >= cutoff,
        )
        .order_by(RequestModel.created_at.desc())
    )
    return twin.public_number if twin is not None else None


def _store_item(db: Session, item: IntakeItem, index: int) -> dict:
    if item.dedupe_key:
        existing = db.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == item.dedupe_key)
        )
        if existing is not None:
            # Reported rather than re-created, and deliberately regardless of
            # what became of it: if the Owner already declined or cancelled
            # this item, a later sweep must not put it back on his queue.
            return _found(existing, index, "duplicate")

    # Checked before the insert, so the item does not match itself.
    twin = _possible_double_send(db, item)

    try:
        # Savepoint, so a failure here rolls back this item alone and leaves
        # the ones already stored in this batch intact.
        with db.begin_nested():
            req = create_request(
                db,
                title=item.title,
                description=item.description,
                request_class=item.request_class,
                source=item.source,
                desired_completion_date=item.desired_completion_date,
                desired_date_reason=item.desired_date_reason,
                additional_context=item.additional_context,
                links=[Link(url=link.url, label=link.label) for link in item.links],
                dedupe_key=item.dedupe_key,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                location=item.location,
            )
            # Read now, while the instance is attached and unexpired: the
            # commit at the end of the batch expires every attribute.
            result = _found(req, index, "created")
            if twin is not None:
                result["possible_double_send"] = twin
    except IntakeError as exc:
        return {"index": index, "status": "rejected", "error": str(exc)}
    except IntegrityError:
        # Lost a race against a concurrent sweep inserting the same key.
        existing = db.scalar(
            select(RequestModel).where(RequestModel.dedupe_key == item.dedupe_key)
        )
        if existing is not None:
            return _found(existing, index, "duplicate")
        return {"index": index, "status": "rejected", "error": "Could not store this item."}

    return result


@router.post("/intake/requests")
@limiter.limit(_settings.intake_rate_limit)
async def intake_requests(request: Request, db: Session = Depends(get_db)):
    if not _token_ok(request):
        return _not_found()

    body = await _read_capped_body(request)
    if body is None:
        return _json_error(413, f"Body exceeds {MAX_INTAKE_BODY_BYTES} bytes.")

    try:
        payload = json.loads(body or b"")
    except ValueError:
        return _json_error(400, "Body must be JSON.")

    # A bare list is accepted as well as {"items": [...]}, since both read
    # naturally from a script and neither is ambiguous.
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return _json_error(422, 'Expected a JSON list, or an object with an "items" list.')
    if len(items) > MAX_INTAKE_ITEMS:
        # A whole-batch error, not a per-item one: this is a bug in the
        # caller rather than a bad row within an otherwise fine request.
        return _json_error(422, f"At most {MAX_INTAKE_ITEMS} items per request.")

    results: list[dict] = []
    for index, raw in enumerate(items):
        try:
            item = IntakeItem.model_validate(raw)
        except ValidationError as exc:
            results.append({"index": index, "status": "rejected", "error": _first_error(exc)})
            continue
        results.append(_store_item(db, item, index))

    db.commit()

    counts = {state: 0 for state in ("created", "duplicate", "rejected")}
    for result in results:
        counts[result["status"]] += 1
    return {"results": results, **counts}


# ---------------------------------------------------------------------------
# Status read-back
# ---------------------------------------------------------------------------
# So a mail-side job can reflect what became of each captured item back onto
# the message it came from. `dedupe_key` is the RFC message-id, so it is
# already the join key; this endpoint only has to say what the state is now.


def _as_utc_naive(value: datetime) -> datetime:
    """Normalises a client timestamp to match the stored column.

    `updated_at` is naive and in UTC (SQLite's CURRENT_TIMESTAMP), so an
    aware timestamp has to be converted and stripped before it can be
    compared against it; a naive one is taken to be UTC already.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _iso_z(value: datetime) -> str:
    """Renders a stored timestamp as unambiguous UTC.

    Correct for `created_at` / `updated_at`, which the server writes in
    UTC. **Wrong for an event's `starts_at` / `ends_at`** -- see
    `_iso_floating`.
    """
    return f"{value.isoformat()}Z"


def _iso_floating(value: datetime) -> str:
    """Renders an event time as a floating local time, with NO zone suffix.

    An event's start is a wall-clock time in the place it happens: a
    seminar at 3pm is at 3pm, and nothing about it is a UTC instant. The
    caller posts a naive timestamp and SQLite stores it naive.

    This used `_iso_z`, which stamped a `Z` on that naive value and so
    claimed 3pm local was 3pm UTC. A client that believed the suffix and
    converted to Eastern would have put the seminar in the calendar at
    10am. Caught on the first real event, before anything was mis-booked,
    because the write path reads the wall-clock fields and ignores the
    zone.

    Serving no suffix is the honest answer: the value means what it says.
    """
    return value.replace(tzinfo=None).isoformat()


def _encode_cursor(row: RequestModel) -> str:
    return base64.urlsafe_b64encode(f"{row.updated_at.isoformat()}|{row.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    timestamp, _, row_id = raw.rpartition("|")
    return datetime.fromisoformat(timestamp), int(row_id)


@router.get("/intake/requests/status")
@limiter.limit(_settings.intake_rate_limit)
def intake_request_status(
    request: Request,
    since: str = "",
    cursor: str = "",
    limit: int = DEFAULT_STATUS_LIMIT,
    db: Session = Depends(get_db),
):
    """Reports the current state of every request captured from mail.

    Deliberately narrow: `dedupe_key`, `public_number`, `status`,
    `disposition` and `updated_at`, and nothing else. No title and no
    description — the same token now reads as well as writes, and a leaked
    one should expose which messages were captured and what became of them,
    not what the Owner's obligations actually say.

    Only rows with a `dedupe_key` are reported, since those are exactly the
    ones with a message to reconcile against. A public form submission has
    none.
    """
    if not _token_ok(request):
        return _not_found()

    if limit < 1 or limit > MAX_STATUS_LIMIT:
        return _json_error(422, f"limit must be between 1 and {MAX_STATUS_LIMIT}.")

    stmt = select(RequestModel).where(RequestModel.dedupe_key.is_not(None))

    if cursor:
        try:
            cursor_timestamp, cursor_id = _decode_cursor(cursor)
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return _json_error(422, "cursor is not a cursor returned by this endpoint.")
        # Keyset pagination on (updated_at, id). The id tiebreak is what
        # makes this safe at all: updated_at has one-second resolution, so a
        # cursor carrying only a timestamp would stall forever if more rows
        # shared that second than fit in one page.
        stmt = stmt.where(
            or_(
                RequestModel.updated_at > cursor_timestamp,
                (RequestModel.updated_at == cursor_timestamp) & (RequestModel.id > cursor_id),
            )
        )
    elif since:
        try:
            since_timestamp = _as_utc_naive(datetime.fromisoformat(since))
        except ValueError:
            return _json_error(422, "since must be an ISO 8601 timestamp.")
        # Inclusive, not exclusive. updated_at only resolves to the second,
        # so an exclusive filter would silently drop anything updated in the
        # same second as the caller's high-water mark. Re-reporting a row is
        # harmless for a client that is setting a flag; missing one is not.
        stmt = stmt.where(RequestModel.updated_at >= since_timestamp)

    stmt = stmt.order_by(RequestModel.updated_at, RequestModel.id).limit(limit + 1)
    rows = list(db.scalars(stmt))

    has_more = len(rows) > limit
    rows = rows[:limit]

    return {
        "items": [
            {
                "dedupe_key": row.dedupe_key,
                "public_number": row.public_number,
                "status": row.status.value,
                "disposition": DISPOSITIONS[row.status],
                "updated_at": _iso_z(row.updated_at),
            }
            for row in rows
        ],
        "count": len(rows),
        "has_more": has_more,
        "next_cursor": _encode_cursor(rows[-1]) if has_more and rows else None,
    }


class EventWritten(BaseModel):
    """What the sweep reports back after creating a calendar event."""

    model_config = ConfigDict(extra="forbid")

    calendar_uid: str = Field(min_length=1, max_length=300)


@router.get("/intake/events/pending")
@limiter.limit(_settings.intake_rate_limit)
def intake_events_pending(
    request: Request,
    limit: int = DEFAULT_STATUS_LIMIT,
    db: Session = Depends(get_db),
):
    """Accepted events that are not yet on the calendar.

    The sweep reads this, creates each one in the Western Exchange
    calendar, and reports the UID back to `/intake/events/{number}/written`.

    **This endpoint returns titles, and `/intake/requests/status`
    deliberately does not.** That is a real narrowing of the property
    documented there — a leaked token now reveals the text of something,
    not just which messages were captured. It is unavoidable: you cannot
    write a calendar event without its summary. What keeps the exposure
    small is the filter. Only *accepted* events *awaiting a write* appear,
    which is a handful of rows that empty themselves on the next sweep,
    rather than the whole queue.

    The gate is `status == queued`, never `submitted`. An event must reach
    Exchange only once the Owner has accepted it: anything that blocks his
    availability for colleagues booking through Outlook has to be his
    decision, not a consequence of capture. Note this is deliberately NOT
    `CALENDAR_STATUSES`, which includes `submitted` — harmless for the
    feed's transparent deadline markers, wrong here.
    """
    if not _token_ok(request):
        return _not_found()

    if limit < 1 or limit > MAX_STATUS_LIMIT:
        return _json_error(422, f"limit must be between 1 and {MAX_STATUS_LIMIT}.")

    stmt = (
        select(RequestModel)
        .where(
            RequestModel.request_class == RequestClass.event,
            RequestModel.status == RequestStatus.queued,
            RequestModel.calendar_uid.is_(None),
            RequestModel.starts_at.is_not(None),
        )
        .order_by(RequestModel.starts_at)
        .limit(limit)
    )
    rows = list(db.scalars(stmt))

    return {
        "items": [
            {
                "public_number": row.public_number,
                "dedupe_key": row.dedupe_key,
                "title": row.title,
                "location": row.location,
                "starts_at": _iso_floating(row.starts_at),
                "ends_at": _iso_floating(row.ends_at),
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.post("/intake/events/{public_number}/written")
@limiter.limit(_settings.intake_rate_limit)
async def intake_event_written(
    request: Request,
    public_number: str,
    db: Session = Depends(get_db),
):
    """Records the calendar UID for an event the sweep has just created.

    Idempotent in the only way that matters: a request that already has a
    UID is reported as `already_written` and is NOT overwritten. If the
    sweep crashes between creating the event and reporting it, the retry
    creates a second calendar event and this call refuses it — which
    leaves a visible duplicate on the calendar rather than a silently
    orphaned one that nothing can ever delete. The duplicate's UID is
    returned so it can be cleaned up.
    """
    if not _token_ok(request):
        return _not_found()

    try:
        payload = EventWritten.model_validate(await request.json())
    except ValidationError as exc:
        return _json_error(422, exc.errors()[0].get("msg", "Invalid body."))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_error(422, "Body must be JSON.")

    req = db.scalar(select(RequestModel).where(RequestModel.public_number == public_number))
    if req is None:
        return _not_found()
    if req.request_class != RequestClass.event:
        return _json_error(422, f"{public_number} is not an event.")

    if req.calendar_uid is not None:
        return {
            "public_number": public_number,
            "status": "already_written",
            "calendar_uid": req.calendar_uid,
            "rejected_uid": payload.calendar_uid,
        }

    req.calendar_uid = payload.calendar_uid
    req.calendar_written_at = datetime.now(UTC)
    db.commit()
    return {
        "public_number": public_number,
        "status": "recorded",
        "calendar_uid": req.calendar_uid,
    }


class IntakeCorrection(BaseModel):
    """A correction to an already-captured item, keyed on its dedupe_key.

    Only the fields a re-read of the originating email can legitimately
    revise. Title and description are here because a capture made from a
    subject line is exactly the one most likely to be wrong, and status,
    class and queue position are NOT, because those are the Owner's
    decisions and nothing automated should be able to reach them.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_LEN)
    description: str | None = Field(default=None, min_length=1, max_length=MAX_DESCRIPTION_LEN)
    additional_context: str | None = Field(default=None, max_length=MAX_CONTEXT_LEN)
    desired_completion_date: date | None = None
    desired_date_reason: str | None = Field(default=None, max_length=MAX_REASON_LEN)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)
    note: str = Field(min_length=1, max_length=500)


@router.post("/intake/requests/correct")
@limiter.limit(_settings.intake_rate_limit)
async def intake_correct(
    request: Request,
    dedupe_key: str = "",
    db: Session = Depends(get_db),
):
    """Revises a captured request in place, identified by `dedupe_key`.

    Exists because capture was one-way and that turned out to be a real
    gap, not a theoretical one. An item captured with no date, or with a
    date read from a subject line and later contradicted by the body, had
    no route back: intake only created, `dedupe_key` is permanent, and a
    second POST reports `duplicate` by design. The only fix was the Owner
    editing it by hand. Live example: a Coffee Hour captured with no date
    whose real session was the following day.

    Deliberately narrow in three ways.

    It cannot touch `status`, `request_class`, `queue_position` or any of
    the decision fields. Those are the Owner's, and a token that could
    move them could accept work on his behalf.

    It refuses a request that is already closed. Re-dating something he
    has declined or completed would quietly reopen a decision he has
    made; the capture path refuses the same thing for the same reason.

    It requires a `note` saying what prompted the change, recorded on the
    request's own timeline. A silent correction is indistinguishable from
    a bug, and this runs unattended.
    """
    if not _token_ok(request):
        return _not_found()
    if not dedupe_key:
        return _json_error(422, "dedupe_key is required.")

    try:
        payload = IntakeCorrection.model_validate(await request.json())
    except ValidationError as exc:
        return _json_error(422, exc.errors()[0].get("msg", "Invalid body."))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_error(422, "Body must be JSON.")

    req = db.scalar(select(RequestModel).where(RequestModel.dedupe_key == dedupe_key))
    if req is None:
        return _not_found()
    if DISPOSITIONS[req.status] == "closed":
        return _json_error(
            409,
            f"{req.public_number} is {req.status.value}; a closed request is not corrected.",
        )

    fields = payload.model_dump(exclude_unset=True, exclude={"note"})
    if not fields:
        return _json_error(422, "Nothing to change.")

    changed = {}
    for name, value in fields.items():
        before = getattr(req, name)
        if before == value:
            continue
        setattr(req, name, value)
        changed[name] = {"from": _jsonable(before), "to": _jsonable(value)}

    if not changed:
        return {"public_number": req.public_number, "status": "unchanged"}

    # Same rule the capture path enforces: a date must carry its reason,
    # since the recorded date is often computed from a different stated one.
    if req.desired_completion_date and not (req.desired_date_reason or "").strip():
        db.rollback()
        return _json_error(422, "A desired completion date needs a reason recording how it was set.")
    if req.request_class == RequestClass.event and req.starts_at and req.ends_at:
        if req.ends_at <= req.starts_at:
            db.rollback()
            return _json_error(422, "An event must end after it starts.")

    db.add(
        RequestEvent(
            request_id=req.id,
            event_type=EventType.information_added,
            event_metadata={"source": "intake_correction", "note": payload.note, "changed": changed},
        )
    )
    db.commit()
    return {
        "public_number": req.public_number,
        "status": "corrected",
        "changed": sorted(changed),
    }
