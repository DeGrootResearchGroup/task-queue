"""Renders the queue's dated requests as an iCalendar (.ics) feed.

The calendar is *generated from* the queue rather than maintained beside
it: one source of truth, no double entry. A subscribed feed is also
read-only by construction rather than by discipline, which is what makes
it safe to point a calendar client at work items.

Every entry is an all-day marker and is published as TRANSPARENT, so it
shows the date without consuming availability — the Owner tracks when he
is free somewhere else, and a deadline is not a commitment to be busy.

Note the model has no notion of a timed event: a request carries a
`desired_completion_date` (a date) and nothing finer. So this feed holds
deadline markers only. Anything that is genuinely "be in this room at
2pm" still belongs in the calendar directly.
"""

from datetime import UTC, date, datetime, timedelta

from app.models import Request, RequestSource, RequestStatus

# Statuses worth putting on a calendar: work that is still live. A done,
# declined or withdrawn request has no deadline left to miss, and leaving
# them in would make the feed grow without bound.
CALENDAR_STATUSES = (
    RequestStatus.submitted,
    RequestStatus.queued,
    RequestStatus.in_progress,
    RequestStatus.needs_information,
)

_SOURCE_TEXT = {
    RequestSource.form: "Request",
    RequestSource.email: "From email",
    RequestSource.manual: "Added by me",
}


# Per RFC 5545 §3.3.11 a TEXT value escapes backslash, semicolon, comma
# and newline. Done character by character off this table rather than as a
# chain of .replace() calls, where the backslash rule has to run first or
# it re-escapes the backslashes the later rules have just introduced.
_TEXT_ESCAPES = {"\\": "\\\\", ";": "\\;", ",": "\\,", "\n": "\\n"}


def _escape(value: str) -> str:
    """Escapes a value for an iCalendar TEXT property (RFC 5545 §3.3.11)."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(_TEXT_ESCAPES.get(ch, ch) for ch in value)


def _fold(line: str) -> list[str]:
    """Folds a content line to 75 octets, per RFC 5545 §3.1.

    Folding is defined on octets, not characters, so this splits the UTF-8
    encoding and is careful never to cut a multi-byte character in half.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]

    out: list[str] = []
    start = 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Back off to a UTF-8 character boundary (continuation bytes are 10xxxxxx).
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        out.append(raw[start:end].decode("utf-8"))
        start = end
        limit = 74  # continuation lines carry a leading space
    return [out[0]] + [" " + chunk for chunk in out[1:]]


def _event(req: Request, admin_base_url: str, dtstamp: str) -> list[str]:
    due: date = req.desired_completion_date
    details = [
        f"Status: {req.status.value.replace('_', ' ')}",
        f"Type: {'Quick Action' if req.request_class.value == 'quick' else 'Long Action'}",
        f"Source: {_SOURCE_TEXT.get(req.source, req.source.value)}",
    ]
    if req.requester_name:
        details.append(f"Requested by: {req.requester_name}")
    if req.desired_date_reason:
        details.append(f"Date: {req.desired_date_reason}")
    details.append(f"{admin_base_url}requests/{req.id}")

    lines = [
        "BEGIN:VEVENT",
        # Stable across regenerations so a subscribed client updates the
        # existing entry instead of accumulating duplicates. The row id is
        # used rather than the access token, which must not leave the app.
        f"UID:request-{req.id}@personal-request-queue",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{due:%Y%m%d}",
        # DTEND is exclusive for all-day events, so this is a single day.
        f"DTEND;VALUE=DATE:{due + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escape(f'Due: {req.title}')}",
        f"DESCRIPTION:{_escape(chr(10).join(details))}",
        f"URL:{_escape(f'{admin_base_url}requests/{req.id}')}",
        # The two properties that keep these out of free/busy: TRANSPARENT
        # is the standard one; the X- property is what Outlook reads.
        "TRANSP:TRANSPARENT",
        "X-MICROSOFT-CDO-BUSYSTATUS:FREE",
        f"CATEGORIES:{_escape(_SOURCE_TEXT.get(req.source, req.source.value))}",
        "END:VEVENT",
    ]
    return lines


def render_calendar(
    requests: list[Request],
    *,
    calendar_name: str,
    admin_base_url: str,
    now: datetime | None = None,
) -> str:
    """Returns a complete .ics document, CRLF-terminated as RFC 5545 requires."""
    dtstamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Personal Request Queue//Deadlines//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        f"X-WR-CALDESC:{_escape('Deadline markers generated from the request queue. Read-only.')}",
    ]
    for req in requests:
        if req.desired_completion_date is None:
            continue
        lines.extend(_event(req, admin_base_url, dtstamp))
    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"
