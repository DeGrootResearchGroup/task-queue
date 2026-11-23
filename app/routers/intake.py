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

The route is registered only when `INTAKE_TOKEN` is set (see
`app/main.py`), so an unconfigured instance has no such path at all. A
wrong or missing token gets the same 404 as a path that does not exist,
for the reason the calendar feed does it: a probe learns nothing about
whether there is anything here to find.
"""

import hmac
import json
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.intake import IntakeError, Link, create_request
from app.models import Request as RequestModel, RequestClass, RequestSource
from app.rate_limit import limiter
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
    if scheme.lower() != "bearer" or not presented:
        return False
    # compare_digest on both the configured and presented token keeps the
    # check constant-time; the guard above means `presented` is non-empty.
    return hmac.compare_digest(presented, _settings.intake_token)


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


def _found(req: RequestModel, index: int, status: str) -> dict:
    return {
        "index": index,
        "status": status,
        "request_id": req.id,
        "public_number": req.public_number,
        "dedupe_key": req.dedupe_key,
    }


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
            )
            # Read now, while the instance is attached and unexpired: the
            # commit at the end of the batch expires every attribute.
            result = _found(req, index, "created")
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
