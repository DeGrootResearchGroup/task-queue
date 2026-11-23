"""Server-side request intake, for obligations the Owner captures himself.

This is the counterpart to `app/routers/public.py`'s submission handler,
for requests that arrive from the Owner's own mailbox or straight from
him rather than through the public form. Two things differ:

**Nothing here notifies anyone.** The owner-facing "new request" email is
scheduled by the public router as a BackgroundTask, never by the model
layer, so a function in this module is structurally incapable of firing
it. That matters: this feeds an inbox-clearing workflow, and a system for
clearing the Inbox must not mail the Inbox.

**Links are not restricted to http(s).** `validate_submission` rejects
anything else because the public form is an untrusted input; the whole
point of a captured email, though, is to keep a `message://` URL pointing
back at the message it came from. Callers here are the Owner.

Created requests land at `submitted`, which is the existing review step —
`state_machine.accept_and_queue` and `decline` act on exactly that status,
so a captured item appears on the dashboard where the Owner already
accepts and declines things, and needs no separate review interface.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    EventType,
    Request,
    RequestClass,
    RequestEvent,
    RequestLink,
    RequestSource,
    RequestStatus,
)
from app.schemas import (
    MAX_CONTEXT_LEN,
    MAX_DEDUPE_KEY_LEN,
    MAX_DESCRIPTION_LEN,
    MAX_EMAIL_LEN,
    MAX_LINK_LABEL_LEN,
    MAX_LINK_URL_LEN,
    MAX_LINKS,
    MAX_NAME_LEN,
    MAX_REASON_LEN,
    MAX_TITLE_LEN,
)
from app.security import generate_access_token
from app.utils import next_public_number


@dataclass(frozen=True)
class Link:
    """A supporting link to attach to the created request.

    For a captured email this is normally the `message://` URL of the
    originating message, so the request carries its own provenance.
    """

    url: str
    label: str | None = None


class IntakeError(ValueError):
    """Raised when the supplied request data can't be stored as-is."""


def _require(value: str, field: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise IntakeError(f"{field} is required.")
    return stripped


def _bounded(value: str | None, limit: int, field: str) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > limit:
        raise IntakeError(f"{field} is too long (limit {limit} characters).")
    return stripped


def create_request(
    db: Session,
    *,
    title: str,
    description: str,
    request_class: RequestClass,
    source: RequestSource,
    desired_completion_date: date | None = None,
    desired_date_reason: str | None = None,
    additional_context: str | None = None,
    links: Sequence[Link] = (),
    requester_name: str | None = None,
    requester_email: str | None = None,
    status: RequestStatus = RequestStatus.submitted,
    dedupe_key: str | None = None,
) -> Request:
    """Creates a request at `status` (default `submitted`) and returns it.

    Does not commit — the caller owns the transaction, matching
    `app/state_machine.py`'s convention. Does not notify anybody.

    `dedupe_key` is an optional caller-supplied idempotency key, unique
    across all requests. Storing a second request with a key already in use
    raises IntegrityError on flush; callers that care (the HTTP intake
    route) check for the key first and treat the error as the lost side of
    a race.
    """
    if source == RequestSource.form:
        # The form source means "a human filled in the public form". Letting
        # a server-side insert claim it would make the dashboard's filter
        # lie about what the queue is holding.
        raise IntakeError("Use the public submission form for form-sourced requests.")

    title = _require(title, "Title")
    if len(title) > MAX_TITLE_LEN:
        raise IntakeError(f"Title is too long (limit {MAX_TITLE_LEN} characters).")
    description = _require(description, "Description")
    if len(description) > MAX_DESCRIPTION_LEN:
        raise IntakeError(f"Description is too long (limit {MAX_DESCRIPTION_LEN} characters).")

    if desired_date_reason and not desired_completion_date:
        raise IntakeError("A desired-date reason without a date has nothing to explain.")
    if desired_completion_date and not (desired_date_reason or "").strip():
        # Mirrors the public form's rule, and matters more here: the recorded
        # date is often a computed one, backed off from the stated deadline,
        # and the reason is where the stated date survives.
        raise IntakeError("A desired completion date needs a reason recording how it was set.")

    if len(links) > MAX_LINKS:
        raise IntakeError(f"At most {MAX_LINKS} links.")
    for link in links:
        if not link.url.strip():
            raise IntakeError("A link needs a URL.")
        if len(link.url) > MAX_LINK_URL_LEN:
            raise IntakeError(f"A link URL is too long (limit {MAX_LINK_URL_LEN} characters).")
        if link.label and len(link.label) > MAX_LINK_LABEL_LEN:
            raise IntakeError(f"A link label is too long (limit {MAX_LINK_LABEL_LEN} characters).")

    dedupe_key = _bounded(dedupe_key, MAX_DEDUPE_KEY_LEN, "Dedupe key")

    req = Request(
        public_number=next_public_number(db),
        access_token=generate_access_token(),
        requester_name=_bounded(requester_name, MAX_NAME_LEN, "Requester name"),
        requester_email=_bounded(requester_email, MAX_EMAIL_LEN, "Requester email"),
        title=title,
        description=description,
        additional_context=_bounded(additional_context, MAX_CONTEXT_LEN, "Additional context"),
        request_class=request_class,
        status=status,
        source=source,
        desired_completion_date=desired_completion_date,
        desired_date_reason=_bounded(desired_date_reason, MAX_REASON_LEN, "Desired date reason"),
        dedupe_key=dedupe_key,
    )
    db.add(req)
    db.flush()

    for link in links:
        db.add(RequestLink(request_id=req.id, label=(link.label or None), url=link.url.strip()))

    db.add(RequestEvent(request_id=req.id, event_type=EventType.submitted))
    return req
