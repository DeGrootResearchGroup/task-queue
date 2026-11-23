#!/usr/bin/env python3
"""Create requests for standing commitments that are coming due.

Meant to run on a schedule (cron or a systemd timer, daily is plenty).
Each commitment produces at most one request per run, and
`last_generated_stated_date` advances as it does, so running it more often
than necessary is harmless — a second run the same day generates nothing.

Nothing here sends mail, for the same reason scripts/add_request.py
doesn't: a system for clearing the Inbox must not mail the Inbox. Work
arrives on the dashboard at `submitted`, where it is accepted or declined
like anything else.

Usage:
    uv run scripts/generate_recurring.py            # create what's due
    uv run scripts/generate_recurring.py --dry-run  # show it without writing
    uv run scripts/generate_recurring.py --list     # every commitment and its next date
    uv run scripts/generate_recurring.py --as-of 2026-11-30   # pretend it's that day

Crontab entry (08:00 daily), using the deployment's own interpreter:

    0 8 * * * cd /srv/task-queue && /usr/local/bin/uv run scripts/generate_recurring.py
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import RecurringCommitment  # noqa: E402
from app.recurrence import generate_due_requests, next_occurrence  # noqa: E402


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date (YYYY-MM-DD).") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate requests for standing commitments coming due.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be created without writing anything.",
    )
    parser.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        help="List every commitment with its next occurrence, and generate nothing.",
    )
    parser.add_argument(
        "--as-of",
        type=_iso_date,
        help="Treat this date as today. For checking a schedule, mostly.",
    )
    return parser


def _list_commitments(db, today: date) -> int:
    commitments = db.scalars(
        select(RecurringCommitment).order_by(RecurringCommitment.active.desc(), RecurringCommitment.id)
    ).all()
    if not commitments:
        print("No standing commitments recorded. See scripts/add_recurring.py.")
        return 0
    for c in commitments:
        months = ", ".join(str(m) for m in c.months) if c.months else "every month"
        try:
            stated, effective = next_occurrence(c, today)
            when = f"stated {stated:%b %-d, %Y}, recorded {effective:%b %-d, %Y}"
        except ValueError as exc:
            when = f"NO NEXT OCCURRENCE — {exc}"
        flag = "" if c.active else "  [inactive]"
        print(f"{c.id:>3}  {c.title}{flag}")
        print(f"     months: {months}  |  next: {when}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = args.as_of or date.today()

    db = SessionLocal()
    try:
        if args.list_only:
            return _list_commitments(db, today)

        generated = generate_due_requests(db, today=today, dry_run=args.dry_run)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        # Built before the session closes: expire_on_commit means every
        # attribute reloads on access, which fails once the session is gone.
        lines = [
            "{}{}  due {:%b %-d, %Y} (stated {:%b %-d, %Y})".format(
                f"{item['public_number']}  " if "public_number" in item else "",
                item["title"],
                item["desired_completion_date"],
                item["stated_date"],
            )
            for item in generated
        ]
    finally:
        db.close()

    if not lines:
        print("Nothing due.")
        return 0
    print("Would create:" if args.dry_run else "Created:")
    for line in lines:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
