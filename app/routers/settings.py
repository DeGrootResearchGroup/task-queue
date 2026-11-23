from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_app_settings, get_db, get_or_create_csrf_token, require_owner, verify_csrf
from app.flash import flash
from app.models import AppSettings, RecurringCommitment, RequestClass, RequestSource
from app.recurrence import next_occurrence
from app.schemas import DAY_RULES, CommitmentInput, parse_commitment_form, validate_commitment
from app.security import generate_calendar_token
from app.templating import render

router = APIRouter(dependencies=[Depends(require_owner)])


def _commitment_rows(db: Session) -> list[dict]:
    """Standing commitments plus their next occurrence.

    Obligations appear as life does — a new committee, an annual report, a
    course picked up — so adding one has to be possible from here.
    `scripts/add_recurring.py` still works and is how a fresh instance gets
    seeded, but it is not the only way in: the repository is public, so the
    real commitments can never live in it and have to be created against
    the running instance.
    """
    today = date.today()
    rows = []
    for commitment in db.scalars(
        select(RecurringCommitment).order_by(
            RecurringCommitment.active.desc(), RecurringCommitment.title
        )
    ):
        try:
            stated, effective = next_occurrence(commitment, today)
        except ValueError:
            # A schedule that can't resolve is a configuration error, not a
            # reason to fail the whole settings page.
            stated = effective = None
        rows.append(
            {
                "commitment": commitment,
                "stated": stated,
                "effective": effective,
                "fields": _form_values(commitment),
            }
        )
    return rows


def _form_values(commitment: RecurringCommitment) -> dict[str, str]:
    """A stored commitment as the settings form's own field names.

    The form collapses the two mutually exclusive day columns into a rule
    plus a value, so that "exactly one of them" can't be violated from the
    UI at all; this maps back the other way for prefilling.
    """
    if commitment.business_days_before_month_end is not None:
        day_rule, day_value = "business_days_before_month_end", commitment.business_days_before_month_end
    else:
        day_rule, day_value = "day_of_month", commitment.day_of_month
    return {
        "title": commitment.title,
        "description": commitment.description,
        "additional_context": commitment.additional_context or "",
        "request_class": commitment.request_class.value,
        "source": commitment.source.value,
        "months": " ".join(str(m) for m in commitment.months) if commitment.months else "",
        "day_rule": day_rule,
        "day_value": "" if day_value is None else str(day_value),
        "lead_business_days": str(commitment.lead_business_days),
        "date_reason": commitment.date_reason or "",
        "generate_days_ahead": str(commitment.generate_days_ahead),
        "reference_url": commitment.reference_url or "",
        "reference_label": commitment.reference_label or "",
    }


def _settings_context(
    request: Request,
    db: Session,
    settings_row: AppSettings,
    commitment_form: dict | None = None,
) -> dict:
    email_settings = get_settings()
    calendar_url = (
        f"{request.base_url}calendar/{settings_row.calendar_token}.ics"
        if settings_row.calendar_token
        else None
    )
    return {
        "owner_nav": True,
        "csrf_token": get_or_create_csrf_token(request),
        "settings_row": settings_row,
        "email_enabled": email_settings.email_enabled,
        "smtp_username": email_settings.smtp_username,
        "owner_notification_email": email_settings.owner_notification_email,
        "calendar_url": calendar_url,
        "commitments": _commitment_rows(db),
        # Set only when a commitment form failed validation: it carries the
        # rejected values and their errors back so the page can reopen that
        # one form with the Owner's input still in it. The key is "fields",
        # not "values": Jinja resolves `x.values` on a dict to the dict's own
        # .values method, which renders as a bound method and silently
        # prefills nothing.
        "commitment_form": commitment_form,
        "day_rules": DAY_RULES,
    }


@router.get("/settings")
def settings_form(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    return render(request, "owner/settings.html", _settings_context(request, db, settings_row))


@router.post("/settings/calendar-feed", dependencies=[Depends(verify_csrf)])
def regenerate_calendar_token(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    """Generates the .ics feed's secret URL, or replaces an existing one.

    Replacing it is the only way to revoke a feed: a subscription carries
    no credential beyond the URL, so anything already subscribed to the old
    one simply stops resolving.
    """
    existed = settings_row.calendar_token is not None
    settings_row.calendar_token = generate_calendar_token()
    db.commit()
    flash(
        request,
        "Calendar feed URL replaced. Any existing subscription will stop updating."
        if existed
        else "Calendar feed enabled. Subscribe to the URL below.",
        "success",
    )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/calendar-feed/disable", dependencies=[Depends(verify_csrf)])
def disable_calendar_feed(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    settings_row.calendar_token = None
    db.commit()
    flash(request, "Calendar feed disabled. The URL no longer resolves.", "success")
    return RedirectResponse(url="/settings", status_code=303)


# ---------------------------------------------------------------------------
# Standing commitments
# ---------------------------------------------------------------------------


def _get_commitment_or_404(db: Session, commitment_id: int) -> RecurringCommitment:
    commitment = db.get(RecurringCommitment, commitment_id)
    if commitment is None:
        raise HTTPException(status_code=404, detail="Standing commitment not found.")
    return commitment


def _apply(commitment: RecurringCommitment, data: CommitmentInput) -> None:
    commitment.title = data.title.strip()
    commitment.description = data.description.strip()
    commitment.additional_context = data.additional_context
    commitment.request_class = RequestClass(data.request_class)
    commitment.source = RequestSource(data.source)
    commitment.months = data.months
    commitment.day_of_month = data.day_of_month
    commitment.business_days_before_month_end = data.business_days_before_month_end
    commitment.lead_business_days = data.lead_business_days
    commitment.date_reason = data.date_reason
    commitment.generate_days_ahead = data.generate_days_ahead
    commitment.reference_url = data.reference_url
    commitment.reference_label = data.reference_label


async def _read_commitment_form(request: Request) -> tuple[CommitmentInput, dict, dict]:
    """Parses and validates the posted form. Returns (data, errors, raw values)."""
    form = {key: str(value) for key, value in (await request.form()).items()}
    data, errors = parse_commitment_form(form)
    if not errors:
        errors = validate_commitment(data)
    return data, errors, form


def _resolution_error(commitment: RecurringCommitment) -> str | None:
    """Rejects a schedule that can never produce an occurrence.

    Worth checking before saving, as `scripts/add_recurring.py` also does:
    a commitment that silently never fires is the one failure mode this
    whole feature exists to prevent.
    """
    try:
        next_occurrence(commitment, date.today())
    except ValueError as exc:
        return str(exc)
    return None


@router.post("/settings/recurring", dependencies=[Depends(verify_csrf)])
async def create_commitment(
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    data, errors, values = await _read_commitment_form(request)
    commitment = RecurringCommitment(active=True)
    if not errors:
        _apply(commitment, data)
        problem = _resolution_error(commitment)
        if problem:
            errors["months"] = problem

    if errors:
        return render(
            request,
            "owner/settings.html",
            _settings_context(
                request,
                db,
                settings_row,
                {"mode": "create", "id": None, "errors": errors, "fields": values},
            ),
            status_code=422,
        )

    db.add(commitment)
    db.commit()
    stated, effective = next_occurrence(commitment, date.today())
    flash(
        request,
        f"{commitment.title} recorded. Next due {effective:%b %-d, %Y}"
        + (f" (stated {stated:%b %-d, %Y})." if stated != effective else "."),
        "success",
    )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/recurring/{commitment_id}", dependencies=[Depends(verify_csrf)])
async def update_commitment(
    commitment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    commitment = _get_commitment_or_404(db, commitment_id)
    data, errors, values = await _read_commitment_form(request)

    if not errors:
        _apply(commitment, data)
        problem = _resolution_error(commitment)
        if problem:
            errors["months"] = problem

    if errors:
        # The in-session object may already carry the rejected values from
        # _apply; expiring it puts the stored ones back before the list on
        # this page is rendered from them.
        db.rollback()
        return render(
            request,
            "owner/settings.html",
            _settings_context(
                request,
                db,
                settings_row,
                {"mode": "edit", "id": commitment_id, "errors": errors, "fields": values},
            ),
            status_code=422,
        )

    db.commit()
    flash(request, f"{commitment.title} updated.", "success")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/recurring/{commitment_id}/active", dependencies=[Depends(verify_csrf)])
def set_commitment_active(
    commitment_id: int,
    request: Request,
    active: str = Form(""),
    db: Session = Depends(get_db),
):
    """Turns a commitment on or off without deleting it.

    Deactivating rather than deleting is the point: an obligation that
    lapses — a course no longer taught — stops generating work, but the
    requests it already produced keep the rule that explains them.
    """
    commitment = _get_commitment_or_404(db, commitment_id)
    commitment.active = active == "on"
    db.commit()
    flash(
        request,
        f"{commitment.title} {'reactivated' if commitment.active else 'deactivated'}.",
        "success",
    )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings", dependencies=[Depends(verify_csrf)])
def settings_submit(
    request: Request,
    owner_display_name: str = Form(...),
    meeting_booking_url: str = Form(""),
    meeting_booking_text: str = Form(""),
    quick_action_minutes: int = Form(...),
    urgency_green_days: int = Form(...),
    urgency_red_days: int = Form(...),
    db: Session = Depends(get_db),
    settings_row: AppSettings = Depends(get_app_settings),
):
    settings_row.owner_display_name = owner_display_name.strip() or "The Owner"
    settings_row.meeting_booking_url = meeting_booking_url.strip() or None
    settings_row.meeting_booking_text = meeting_booking_text.strip() or None
    settings_row.quick_action_minutes = max(1, quick_action_minutes)
    settings_row.urgency_green_days = max(0, urgency_green_days)
    settings_row.urgency_red_days = max(0, urgency_red_days)
    db.commit()
    flash(request, "Settings saved.", "success")
    return RedirectResponse(url="/settings", status_code=303)
