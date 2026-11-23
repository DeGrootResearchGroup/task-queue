#!/usr/bin/env python3
"""Record a standing commitment — an obligation that arrives on a cycle.

Recording one is what stops the originating email being the trigger: the
queue produces the request on schedule whether or not the notice arrives,
or arrives somewhere it gets missed.

A schedule is "which months" plus "which day of that month":

  --months        a list of month numbers; omit for every month
  --day-of-month  a fixed day (clamped to short months), or
  --business-days-before-month-end
                  count back from the month's last business day
                  (0 = last, 1 = second-last)

and, where a second party must act afterwards, `--lead-business-days`
backs the recorded date off the stated one. See app/recurrence.py.

Examples:

    # Monthly, second-last business day, backed off 3 days for approval.
    uv run scripts/add_recurring.py \\
        --title "Reconcile PCard statement" \\
        --description "Match receipts, code the lines, submit for approval." \\
        --class long \\
        --business-days-before-month-end 1 \\
        --lead-business-days 3 \\
        --date-reason "An approval step follows submission."

    # Annual, a fixed date, no adjustment.
    uv run scripts/add_recurring.py \\
        --title "Western Flex Credits election" \\
        --description "Choose this year's Flex Credit allocation." \\
        --class quick \\
        --months 11 --day-of-month 30

    # Per term.
    uv run scripts/add_recurring.py \\
        --title "Book exam scanning" \\
        --description "Send the booking form to the exam office." \\
        --class quick \\
        --months 4 8 12 --day-of-month 1 \\
        --lead-business-days 2 \\
        --date-reason "A third party must schedule the slot."
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models import RecurringCommitment, RequestClass, RequestSource  # noqa: E402
from app.recurrence import next_occurrence  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record a standing commitment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--title", required=True, help="Name of the obligation, without a date.")
    parser.add_argument("--description", required=True, help="What doing it actually involves.")
    parser.add_argument(
        "--class",
        dest="request_class",
        required=True,
        choices=[c.value for c in RequestClass],
        help="quick (a two-minute job) or long (real work).",
    )
    parser.add_argument(
        "--source",
        default=RequestSource.manual.value,
        choices=[s.value for s in RequestSource if s != RequestSource.form],
        help="Source stamped on each generated request (default: manual).",
    )
    parser.add_argument(
        "--months",
        nargs="+",
        type=int,
        default=None,
        metavar="N",
        help="Month numbers it falls in (1-12). Omit for every month.",
    )
    day = parser.add_mutually_exclusive_group(required=True)
    day.add_argument("--day-of-month", type=int, help="Fixed day, clamped to short months.")
    day.add_argument(
        "--business-days-before-month-end",
        type=int,
        help="0 = the month's last business day, 1 = second-last, and so on.",
    )
    parser.add_argument(
        "--lead-business-days",
        type=int,
        default=0,
        help="Business days to record the deadline ahead of the stated one (default 0).",
    )
    parser.add_argument(
        "--date-reason",
        help="Why the date moved, or anything else worth carrying onto every occurrence.",
    )
    parser.add_argument("--context", dest="additional_context", help="Optional additional context.")
    parser.add_argument(
        "--generate-days-ahead",
        type=int,
        default=14,
        help="How many days before the effective date the request appears (default 14).",
    )
    parser.add_argument("--reference-url", help="A standing link to attach to each occurrence.")
    parser.add_argument("--reference-label", help="Label for --reference-url.")
    return parser


def _validate(args) -> None:
    if args.months is not None:
        if not args.months:
            raise SystemExit("--months was given with no values; omit it for every month.")
        if any(not 1 <= m <= 12 for m in args.months):
            raise SystemExit("--months values must be between 1 and 12.")
    if args.day_of_month is not None and not 1 <= args.day_of_month <= 31:
        raise SystemExit("--day-of-month must be between 1 and 31.")
    if args.business_days_before_month_end is not None and args.business_days_before_month_end < 0:
        raise SystemExit("--business-days-before-month-end must not be negative.")
    if args.lead_business_days < 0:
        raise SystemExit("--lead-business-days must not be negative.")
    if args.generate_days_ahead < 0:
        raise SystemExit("--generate-days-ahead must not be negative.")
    if args.reference_label and not args.reference_url:
        raise SystemExit("--reference-label needs a --reference-url to label.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _validate(args)

    commitment = RecurringCommitment(
        title=args.title.strip(),
        description=args.description.strip(),
        additional_context=(args.additional_context or "").strip() or None,
        request_class=RequestClass(args.request_class),
        source=RequestSource(args.source),
        months=sorted(set(args.months)) if args.months else None,
        day_of_month=args.day_of_month,
        business_days_before_month_end=args.business_days_before_month_end,
        lead_business_days=args.lead_business_days,
        date_reason=(args.date_reason or "").strip() or None,
        generate_days_ahead=args.generate_days_ahead,
        reference_url=(args.reference_url or "").strip() or None,
        reference_label=(args.reference_label or "").strip() or None,
    )

    db = SessionLocal()
    try:
        # Resolve the next occurrence before committing: a schedule that
        # can't produce one is misconfigured, and it's far better to say so
        # now than to leave a commitment that silently never fires.
        stated, effective = next_occurrence(commitment, date.today())
        db.add(commitment)
        db.commit()
        summary = (
            f"Recorded: {commitment.title}",
            f"Next occurrence stated {stated:%B %-d, %Y}, recorded for {effective:%B %-d, %Y}",
        )
    except ValueError as exc:
        db.rollback()
        print(f"Could not record the commitment: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
