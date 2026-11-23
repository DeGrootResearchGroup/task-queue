#!/usr/bin/env python3
"""Add a request to the queue from the server side, without notifying anyone.

For dated obligations the Owner captures himself — lifted out of his
mailbox by a triage sweep (`--source email`), or entered directly
(`--source manual`). Requests created here land at `submitted`, the
existing review step, so they appear on the dashboard alongside everything
else waiting to be accepted or declined.

Nothing here sends mail. The owner-facing "new request" notification is
scheduled by the public submission route, never by `app.intake`, so this
path can't fire it — which is the point, since this feeds an
inbox-clearing workflow.

Usage:
    uv run scripts/add_request.py \\
        --title "Reconcile October PCard statement" \\
        --description "Match receipts, code the lines, submit for approval." \\
        --class long \\
        --source email \\
        --date 2026-11-25 \\
        --date-reason "Stated deadline Nov 28; recorded 3 business days earlier because an approval step follows." \\
        --link "message://%3C1234@uwo.ca%3E" --link-label "Originating email"

`--link` may be repeated; `--link-label` is optional and is paired with
the links in the order both are given.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.intake import IntakeError, Link, create_request  # noqa: E402
from app.models import RequestClass, RequestSource  # noqa: E402


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date (YYYY-MM-DD).") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a queue request server-side, with no notification.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--title", required=True, help="Short title for the request.")
    parser.add_argument("--description", required=True, help="What actually needs doing.")
    parser.add_argument(
        "--class",
        dest="request_class",
        required=True,
        choices=[c.value for c in RequestClass],
        help="quick (a two-minute job) or long (real work).",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=[s.value for s in RequestSource if s != RequestSource.form],
        help="Where this came from. `form` is reserved for the public submission form.",
    )
    parser.add_argument(
        "--date",
        dest="desired_completion_date",
        type=_iso_date,
        help=(
            "The effective date to work to. Where a second party must act "
            "afterwards, this is the stated deadline backed off, not the "
            "stated deadline itself."
        ),
    )
    parser.add_argument(
        "--date-reason",
        dest="desired_date_reason",
        help="Required with --date. Record the stated deadline and any adjustment here.",
    )
    parser.add_argument("--context", dest="additional_context", help="Optional additional context.")
    parser.add_argument(
        "--link",
        dest="links",
        action="append",
        default=[],
        help="Supporting URL; repeatable. A message:// URL back to the originating email is fine.",
    )
    parser.add_argument(
        "--link-label",
        dest="link_labels",
        action="append",
        default=[],
        help="Optional label, paired with --link in the order given; repeatable.",
    )
    parser.add_argument(
        "--requester-name",
        help="Only for an obligation that genuinely has a second party; normally omitted.",
    )
    parser.add_argument("--requester-email", help="See --requester-name.")
    return parser


def _pair_links(urls: list[str], labels: list[str]) -> list[Link]:
    if len(labels) > len(urls):
        raise SystemExit("More --link-label values than --link values.")
    padded = labels + [None] * (len(urls) - len(labels))
    return [Link(url=url, label=label) for url, label in zip(urls, padded)]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    links = _pair_links(args.links, args.link_labels)

    db = SessionLocal()
    try:
        req = create_request(
            db,
            title=args.title,
            description=args.description,
            request_class=RequestClass(args.request_class),
            source=RequestSource(args.source),
            desired_completion_date=args.desired_completion_date,
            desired_date_reason=args.desired_date_reason,
            additional_context=args.additional_context,
            links=links,
            requester_name=args.requester_name,
            requester_email=args.requester_email,
        )
        db.commit()
        # Read the columns we want to print while the instance is still
        # attached. SessionLocal uses expire_on_commit=True, so every
        # attribute is stale after the commit above and reloads on access —
        # which raises DetachedInstanceError once the session is closed.
        summary = (f"{req.public_number}  {req.title}", f"Awaiting review at /requests/{req.id}")
    except IntakeError as exc:
        db.rollback()
        print(f"Could not add the request: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
