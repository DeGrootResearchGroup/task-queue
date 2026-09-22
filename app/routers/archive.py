from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.deps import get_db, get_or_create_csrf_token, require_owner
from app.models import Request as RequestModel, RequestClass, RequestStatus
from app.templating import render

router = APIRouter(dependencies=[Depends(require_owner)])

ARCHIVE_STATUSES = (RequestStatus.done, RequestStatus.declined, RequestStatus.withdrawn)


@router.get("/archive")
def archive(
    request: Request,
    q: str = "",
    status: str = "",
    request_class: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    stmt = select(RequestModel)

    if status and status in {s.value for s in ARCHIVE_STATUSES}:
        stmt = stmt.where(RequestModel.status == RequestStatus(status))
    else:
        stmt = stmt.where(RequestModel.status.in_(ARCHIVE_STATUSES))

    if request_class in ("quick", "long"):
        stmt = stmt.where(RequestModel.request_class == RequestClass(request_class))

    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                RequestModel.public_number.ilike(pattern),
                RequestModel.title.ilike(pattern),
                RequestModel.requester_name.ilike(pattern),
                RequestModel.requester_email.ilike(pattern),
            )
        )

    if date_from:
        try:
            stmt = stmt.where(RequestModel.created_at >= date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            stmt = stmt.where(RequestModel.created_at <= date.fromisoformat(date_to))
        except ValueError:
            pass

    stmt = stmt.order_by(RequestModel.updated_at.desc()).limit(200)
    results = db.scalars(stmt).all()

    return render(
        request,
        "owner/archive.html",
        {
            "owner_nav": True,
            "csrf_token": get_or_create_csrf_token(request),
            "results": results,
            "q": q,
            "status": status,
            "request_class": request_class,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
