"""Standing commitments: obligations that arrive on a cycle.

The monthly PCard reconciliation, the annual Flex Credits deadline, the
per-term exam-scanning booking. Each is recorded once as a
`RecurringCommitment`, and a generated `Request` appears shortly before
each occurrence falls due. The point is that the originating email stops
being the trigger and becomes a redundant confirmation — a missed email
no longer means a missed deadline.

Two things the schedule has to express, taken from the obligations that
actually exist:

**Which months.** `months` is a list of month numbers, or null for every
month. Monthly PCard is null; annual Flex Credits is `[11]`; a per-term
booking is simply a list of three months. One field covers all three
cycles, so there is no "frequency" enum to keep in step with reality.

**Which day.** Either a fixed `day_of_month` (Flex Credits: November 30)
or a count back from the end of the month in business days
(`business_days_before_month_end`; PCard: the second-last business day,
which is 1). Exactly one of the two is set.

**The effective date rule.** Where a second party must act after the
Owner, the stated deadline is not his deadline. `lead_business_days`
backs the recorded date off the stated one, and the stated date survives
in the generated request's `desired_date_reason` alongside the reason it
moved. This mirrors what a triage sweep does by hand for one-off items.

Business days here are Monday to Friday. There is no holiday calendar:
adding one would need a jurisdiction and a maintenance burden, and the
lead times in use (two and three days) already absorb a stat holiday.
"""

from calendar import monthrange
from datetime import date, timedelta

WEEKEND = (5, 6)  # date.weekday(): 5 = Saturday, 6 = Sunday

# A commitment that can't find its next occurrence within this many months
# is misconfigured (an empty `months` list, say) rather than merely sparse.
# Bounding the search turns that into an error instead of a hung script.
MAX_MONTHS_AHEAD = 120


def is_business_day(day: date) -> bool:
    return day.weekday() not in WEEKEND


def subtract_business_days(day: date, count: int) -> date:
    """Steps `count` business days back from `day`.

    `count == 0` returns `day` unchanged, weekend or not — the caller
    asked for no adjustment, and silently moving the date would hide that.
    """
    if count < 0:
        raise ValueError("count must not be negative.")
    result = day
    remaining = count
    while remaining > 0:
        result -= timedelta(days=1)
        if is_business_day(result):
            remaining -= 1
    return result


def nth_last_business_day(year: int, month: int, n: int) -> date:
    """The business day `n` places before the month's last one.

    `n == 0` is the last business day of the month, `n == 1` the
    second-last, and so on.
    """
    if n < 0:
        raise ValueError("n must not be negative.")
    day = date(year, month, monthrange(year, month)[1])
    while not is_business_day(day):
        day -= timedelta(days=1)
    return subtract_business_days(day, n)


def _advance_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def stated_date_in(commitment, year: int, month: int) -> date:
    """The commitment's stated deadline within one given month."""
    if commitment.business_days_before_month_end is not None:
        return nth_last_business_day(year, month, commitment.business_days_before_month_end)
    if commitment.day_of_month is None:
        raise ValueError(
            "A commitment needs either day_of_month or business_days_before_month_end."
        )
    # Clamped so a day_of_month of 31 lands on the last day of a short
    # month rather than raising.
    return date(year, month, min(commitment.day_of_month, monthrange(year, month)[1]))


def occurs_in_month(commitment, month: int) -> bool:
    """Null or empty `months` means every month."""
    return not commitment.months or month in commitment.months


def next_stated_date(commitment, after: date) -> date:
    """The commitment's first stated deadline strictly after `after`."""
    year, month = after.year, after.month
    for _ in range(MAX_MONTHS_AHEAD):
        if occurs_in_month(commitment, month):
            stated = stated_date_in(commitment, year, month)
            if stated > after:
                return stated
        year, month = _advance_month(year, month)
    raise ValueError(
        f"No occurrence found within {MAX_MONTHS_AHEAD} months; check the commitment's months."
    )


def effective_date(commitment, stated: date) -> date:
    """The date to actually work to, per the effective date rule."""
    return subtract_business_days(stated, commitment.lead_business_days)


def next_occurrence(commitment, today: date) -> tuple[date, date]:
    """Returns (stated, effective) for the commitment's next live occurrence.

    Occurrences whose stated date has already passed are skipped rather
    than generated late: a standing commitment that was missed in October
    is October's problem, and backfilling it in December would put a
    request on the queue that can no longer be acted on. An occurrence
    whose *effective* date has passed but whose stated date has not is
    still live, and is returned — late, but not yet missed.
    """
    anchor = commitment.last_generated_stated_date
    if anchor is None or anchor < today - timedelta(days=1):
        # Start the search from yesterday so an occurrence stated *today*
        # is still found; `next_stated_date` is strictly-after.
        anchor = today - timedelta(days=1)

    stated = next_stated_date(commitment, after=anchor)
    while stated < today:
        stated = next_stated_date(commitment, after=stated)
    return stated, effective_date(commitment, stated)


def due_occurrence(commitment, today: date) -> tuple[date, date] | None:
    """The next occurrence if it is close enough to generate, else None."""
    stated, effective = next_occurrence(commitment, today)
    if (effective - today).days <= commitment.generate_days_ahead:
        return stated, effective
    return None


def compose_date_reason(commitment, stated: date, effective: date) -> str:
    """The `desired_date_reason` for a generated request.

    The stated deadline always survives here, because the recorded date is
    frequently not it. Without this the adjustment is invisible and the
    backed-off date just looks like the deadline, which is how a computed
    date quietly becomes a wrong one.
    """
    parts = [f"Standing commitment. Stated deadline {stated:%B %-d, %Y}."]
    if effective != stated:
        days = commitment.lead_business_days
        parts.append(
            f"Recorded {days} business day{'' if days == 1 else 's'} earlier "
            f"({effective:%B %-d, %Y}) because a second party must act afterwards."
        )
    if commitment.date_reason:
        parts.append(commitment.date_reason.strip())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Generating requests from commitments
# ---------------------------------------------------------------------------
# Kept below the date math, which deliberately knows nothing about the ORM
# so the schedule rules can be tested without a database.


def generate_due_requests(db, today: date | None = None, dry_run: bool = False) -> list[dict]:
    """Creates a request for every commitment whose next occurrence is due.

    Returns one summary dict per commitment generated. At most one
    occurrence per commitment per run: `last_generated_stated_date` is
    advanced as each is created, so a second run the same day — or an
    hourly cron — produces nothing further.

    Like everything on the server-side insert path, this notifies nobody.
    """
    from sqlalchemy import select

    from app.intake import Link, create_request
    from app.models import RecurringCommitment

    today = today or date.today()
    generated: list[dict] = []

    commitments = db.scalars(
        select(RecurringCommitment)
        .where(RecurringCommitment.active.is_(True))
        .order_by(RecurringCommitment.id)
    ).all()

    for commitment in commitments:
        due = due_occurrence(commitment, today)
        if due is None:
            continue
        stated, effective = due

        summary = {
            "commitment_id": commitment.id,
            "title": f"{commitment.title} — {stated:%B %Y}",
            "stated_date": stated,
            "desired_completion_date": effective,
        }
        if dry_run:
            generated.append(summary)
            continue

        links = (
            [Link(url=commitment.reference_url, label=commitment.reference_label)]
            if commitment.reference_url
            else []
        )
        req = create_request(
            db,
            title=summary["title"],
            description=commitment.description,
            additional_context=commitment.additional_context,
            request_class=commitment.request_class,
            source=commitment.source,
            desired_completion_date=effective,
            desired_date_reason=compose_date_reason(commitment, stated, effective),
            links=links,
        )
        req.recurrence_id = commitment.id
        commitment.last_generated_stated_date = stated
        db.flush()
        summary["public_number"] = req.public_number
        summary["request_id"] = req.id
        generated.append(summary)

    return generated
