"""The token-protected, read-only .ics feed.

Protected by a secret path segment rather than a login because that is the
only credential a subscribed calendar client can carry: Apple Calendar
fetches the URL on its own schedule with no opportunity to log in. The
token is 32 random bytes, so the URL is unguessable; regenerating it from
/settings is the revocation mechanism.

The route is GET/HEAD only. There is no write path at all — read-only by
construction, which is the whole reason the queue publishes a feed instead
of writing entries into the calendar.
"""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar_feed import CALENDAR_STATUSES, render_calendar
from app.deps import get_app_settings, get_db
from app.models import AppSettings, Request as RequestModel

router = APIRouter()


@router.api_route("/calendar/{token}.ics", methods=["GET", "HEAD"])
def calendar_feed(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    configured = settings_row.calendar_token
    # 404 rather than 403 on a bad token, and the same 404 when no feed has
    # been generated at all: a wrong guess learns nothing about whether a
    # feed exists. compare_digest keeps the check constant-time.
    if not configured or not hmac.compare_digest(token, configured):
        raise HTTPException(status_code=404, detail="No such calendar feed.")

    requests = db.scalars(
        select(RequestModel)
        .where(
            RequestModel.status.in_(CALENDAR_STATUSES),
            RequestModel.desired_completion_date.is_not(None),
        )
        .order_by(RequestModel.desired_completion_date)
    ).all()

    body = render_calendar(
        list(requests),
        calendar_name=f"{settings_row.owner_display_name} — Deadlines",
        admin_base_url=str(request.base_url),
    )
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="task-queue.ics"',
            # Subscribed clients poll on their own schedule; nothing here is
            # worth a shared cache holding on to.
            "Cache-Control": "no-store, private",
        },
    )
