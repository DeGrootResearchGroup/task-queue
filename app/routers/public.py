from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.antispam import FORM_RENDERED_AT_FIELD, HONEYPOT_FIELD, form_rendered_at_token, is_spam
from app.config import get_settings
from app.deps import get_app_settings, get_db, get_or_create_csrf_token, verify_csrf
from app.flash import flash
from app.models import (
    AppSettings,
    EventType,
    Request as RequestModel,
    RequestClass,
    RequestEvent,
    RequestLink,
    RequestStatus,
)
from app.notifications import notify_owner_new_request, notify_owner_requester_update, notify_requester_received
from app.rate_limit import limiter
from app.schemas import LinkInput, SubmissionInput, validate_submission
from app.security import generate_access_token
from app.state_machine import add_information as sm_add_information
from app.state_machine import respond_to_information, withdraw
from app.templating import render
from app.utils import next_public_number, urgency_level

router = APIRouter()

_settings = get_settings()


def _get_request_by_token_or_404(db: Session, token: str) -> RequestModel:
    req = db.scalar(select(RequestModel).where(RequestModel.access_token == token))
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


def _parse_links(labels: list[str], urls: list[str]) -> list[LinkInput]:
    links = []
    for label, url in zip(labels, urls):
        url = url.strip()
        if not url:
            continue
        links.append(LinkInput(label=label.strip(), url=url))
    return links


def _submit_form_context(
    request: Request,
    settings_row: AppSettings,
    errors: dict,
    values: dict,
    links: list[LinkInput],
) -> dict:
    return {
        "csrf_token": get_or_create_csrf_token(request),
        "form_rendered_at": form_rendered_at_token(),
        "honeypot_field": HONEYPOT_FIELD,
        "form_rendered_at_field": FORM_RENDERED_AT_FIELD,
        "settings_row": settings_row,
        "errors": errors,
        "values": values,
        "links": links,
    }


@router.api_route("/", methods=["GET", "HEAD"])
def submit_form(request: Request, settings_row: AppSettings = Depends(get_app_settings)):
    return render(
        request,
        "public/submit.html",
        _submit_form_context(request, settings_row, {}, {}, []),
    )


@router.post("/", dependencies=[Depends(verify_csrf)])
@limiter.limit(_settings.submit_rate_limit)
async def submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    form = await request.form()

    honeypot_value = str(form.get(HONEYPOT_FIELD, ""))
    rendered_at = str(form.get(FORM_RENDERED_AT_FIELD, ""))
    if is_spam(honeypot_value, rendered_at):
        # Silently pretend success without persisting anything.
        return RedirectResponse(url="/", status_code=303)

    labels = [str(v) for v in form.getlist("link_label")]
    urls = [str(v) for v in form.getlist("link_url")]
    links = _parse_links(labels, urls)

    data = SubmissionInput(
        requester_name=str(form.get("requester_name", "")),
        requester_email=str(form.get("requester_email", "")),
        title=str(form.get("title", "")),
        description=str(form.get("description", "")),
        additional_context=str(form.get("additional_context", "")),
        request_class=str(form.get("request_class", "")),
        desired_completion_date=str(form.get("desired_completion_date", "")),
        desired_date_reason=str(form.get("desired_date_reason", "")),
        confirmation=str(form.get("confirmation", "")) == "on",
        links=links,
    )
    errors = validate_submission(data)
    if errors:
        values = {
            "requester_name": data.requester_name,
            "requester_email": data.requester_email,
            "title": data.title,
            "description": data.description,
            "additional_context": data.additional_context,
            "request_class": data.request_class,
            "desired_completion_date": data.desired_completion_date,
            "desired_date_reason": data.desired_date_reason,
        }
        return render(
            request,
            "public/submit.html",
            _submit_form_context(request, settings_row, errors, values, links),
            status_code=422,
        )

    request_class = RequestClass.quick if data.request_class == "quick" else RequestClass.long
    status = RequestStatus.queued if request_class == RequestClass.quick else RequestStatus.submitted

    req = RequestModel(
        public_number=next_public_number(db),
        access_token=generate_access_token(),
        requester_name=data.requester_name.strip(),
        requester_email=data.requester_email.strip(),
        title=data.title.strip(),
        description=data.description.strip(),
        additional_context=data.additional_context.strip() or None,
        request_class=request_class,
        status=status,
        desired_completion_date=(
            date.fromisoformat(data.desired_completion_date) if data.desired_completion_date else None
        ),
        desired_date_reason=data.desired_date_reason.strip() or None,
    )
    db.add(req)
    db.flush()

    for link in links:
        db.add(RequestLink(request_id=req.id, label=link.label or None, url=link.url))

    db.add(RequestEvent(request_id=req.id, event_type=EventType.submitted))
    if request_class == RequestClass.quick:
        db.add(RequestEvent(request_id=req.id, event_type=EventType.accepted))

    db.commit()
    db.refresh(req)
    db.refresh(settings_row)

    base_url = str(request.base_url)
    background_tasks.add_task(notify_requester_received, base_url, req, settings_row)
    background_tasks.add_task(notify_owner_new_request, base_url, req)

    return RedirectResponse(url=f"/r/{req.access_token}", status_code=303)


def _tracking_context(db: Session, req: RequestModel, settings_row: AppSettings) -> dict:
    ctx: dict = {
        "req": req,
        "urgency": urgency_level(req.desired_completion_date, settings_row),
    }
    if req.status == RequestStatus.submitted:
        ctx["submitted_count"] = db.scalar(
            select(func.count())
            .select_from(RequestModel)
            .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.submitted)
        )
        ctx["queued_count"] = db.scalar(
            select(func.count())
            .select_from(RequestModel)
            .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.queued)
        )
    elif req.status == RequestStatus.queued and req.request_class == RequestClass.long:
        ctx["queue_total"] = db.scalar(
            select(func.count())
            .select_from(RequestModel)
            .where(RequestModel.request_class == RequestClass.long, RequestModel.status == RequestStatus.queued)
        )
    elif req.status == RequestStatus.needs_information:
        open_q = next((ir for ir in reversed(req.info_requests) if ir.responded_at is None), None)
        ctx["open_question"] = open_q
    return ctx


@router.api_route("/r/{token}", methods=["GET", "HEAD"])
def track(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    req = _get_request_by_token_or_404(db, token)
    ctx = _tracking_context(db, req, settings_row)
    ctx["csrf_token"] = get_or_create_csrf_token(request)
    ctx["settings_row"] = settings_row
    ctx["form_rendered_at"] = form_rendered_at_token()
    ctx["honeypot_field"] = HONEYPOT_FIELD
    ctx["form_rendered_at_field"] = FORM_RENDERED_AT_FIELD
    return render(request, "public/track.html", ctx)


@router.post("/r/{token}/add-information", dependencies=[Depends(verify_csrf)])
@limiter.limit(_settings.submit_rate_limit)
def add_information(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    honeypot_value: str = Form("", alias=HONEYPOT_FIELD),
    rendered_at: str = Form("", alias=FORM_RENDERED_AT_FIELD),
    db: Session = Depends(get_db),
):
    if is_spam(honeypot_value, rendered_at):
        # Silently pretend success, same as the main submission form — don't
        # tip off a bot that it was detected.
        return RedirectResponse(url=f"/r/{token}", status_code=303)

    req = _get_request_by_token_or_404(db, token)
    if not content.strip():
        flash(request, "Please enter some information before submitting.", "error")
        return RedirectResponse(url=f"/r/{token}", status_code=303)

    content = content.strip()
    sm_add_information(db, req, content)
    db.commit()
    db.refresh(req)

    background_tasks.add_task(notify_owner_requester_update, str(request.base_url), req, content)

    flash(request, "Additional information added.", "success")
    return RedirectResponse(url=f"/r/{token}", status_code=303)


@router.post("/r/{token}/respond", dependencies=[Depends(verify_csrf)])
@limiter.limit(_settings.submit_rate_limit)
def respond(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    response: str = Form(...),
    honeypot_value: str = Form("", alias=HONEYPOT_FIELD),
    rendered_at: str = Form("", alias=FORM_RENDERED_AT_FIELD),
    db: Session = Depends(get_db),
):
    if is_spam(honeypot_value, rendered_at):
        return RedirectResponse(url=f"/r/{token}", status_code=303)

    req = _get_request_by_token_or_404(db, token)
    respond_to_information(db, req, response)
    db.commit()
    db.refresh(req)

    background_tasks.add_task(notify_owner_requester_update, str(request.base_url), req, response.strip())

    flash(request, "Response received. Your request has returned for processing.", "success")
    return RedirectResponse(url=f"/r/{token}", status_code=303)


@router.post("/r/{token}/withdraw", dependencies=[Depends(verify_csrf)])
def withdraw_request(
    token: str,
    request: Request,
    confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    req = _get_request_by_token_or_404(db, token)
    if confirm != "on":
        flash(request, "Please confirm to withdraw your request.", "error")
        return RedirectResponse(url=f"/r/{token}", status_code=303)
    withdraw(db, req)
    db.commit()
    flash(request, "Your request has been withdrawn.", "success")
    return RedirectResponse(url=f"/r/{token}", status_code=303)
