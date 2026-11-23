"""Server-side validation helpers for HTML form submissions.

These are plain functions rather than a Pydantic model because the
submission form needs to redisplay the user's input alongside field-level
error messages on failure — which is more natural to handle by collecting
an errors dict than by catching a ValidationError.
"""

from dataclasses import dataclass, field
from datetime import date

MAX_NAME_LEN = 200
MAX_EMAIL_LEN = 320
MAX_TITLE_LEN = 300
MAX_DESCRIPTION_LEN = 10_000
MAX_CONTEXT_LEN = 10_000
MAX_LINK_URL_LEN = 2000
MAX_LINK_LABEL_LEN = 200
MAX_LINKS = 10
MAX_REASON_LEN = 2000
# Generous: an RFC message-id is short, but a caller may legitimately
# qualify one ("<id>#register") to raise two tasks from a single message.
MAX_DEDUPE_KEY_LEN = 500


@dataclass
class LinkInput:
    label: str
    url: str


@dataclass
class SubmissionInput:
    requester_name: str
    requester_email: str
    title: str
    description: str
    additional_context: str
    request_class: str
    desired_completion_date: str
    desired_date_reason: str
    confirmation: bool
    links: list[LinkInput] = field(default_factory=list)


def _too_long(value: str, limit: int) -> bool:
    return len(value) > limit


def validate_submission(data: SubmissionInput) -> dict[str, str]:
    errors: dict[str, str] = {}

    if not data.requester_name.strip():
        errors["requester_name"] = "Name is required."
    elif _too_long(data.requester_name, MAX_NAME_LEN):
        errors["requester_name"] = "Name is too long."

    email = data.requester_email.strip()
    if not email:
        errors["requester_email"] = "Email address is required."
    elif "@" not in email or " " in email or _too_long(email, MAX_EMAIL_LEN):
        errors["requester_email"] = "Enter a valid email address."

    if not data.title.strip():
        errors["title"] = "A request title is required."
    elif _too_long(data.title, MAX_TITLE_LEN):
        errors["title"] = "Title is too long."

    if not data.description.strip():
        errors["description"] = "Please describe the action or decision you need."
    elif _too_long(data.description, MAX_DESCRIPTION_LEN):
        errors["description"] = "Description is too long."

    if _too_long(data.additional_context, MAX_CONTEXT_LEN):
        errors["additional_context"] = "Additional context is too long."

    if data.request_class not in ("quick", "long"):
        errors["request_class"] = "Please select whether this is quick or takes longer."

    if data.desired_completion_date:
        try:
            date.fromisoformat(data.desired_completion_date)
        except ValueError:
            # Guards against app/routers/public.py's later unconditional
            # date.fromisoformat() call, which has no try/except of its own —
            # a malformed value reaching that point crashes with an
            # unhandled 500 instead of a normal field validation error. The
            # <input type="date"> constrains this in a browser, but this is
            # a public, unauthenticated POST endpoint reachable directly.
            errors["desired_completion_date"] = "Enter a valid date."

    if data.desired_completion_date and not data.desired_date_reason.strip():
        errors["desired_date_reason"] = "Please explain why this date matters."
    if data.desired_date_reason and _too_long(data.desired_date_reason, MAX_REASON_LEN):
        errors["desired_date_reason"] = "Reason is too long."

    if not data.confirmation:
        errors["confirmation"] = "Please confirm before submitting."

    if len(data.links) > MAX_LINKS:
        errors["links"] = f"Please provide at most {MAX_LINKS} links."
    for link in data.links:
        if _too_long(link.url, MAX_LINK_URL_LEN) or _too_long(link.label, MAX_LINK_LABEL_LEN):
            errors["links"] = "One of the links or labels is too long."
        if link.url and not (link.url.startswith("http://") or link.url.startswith("https://")):
            errors["links"] = "Links must start with http:// or https://."

    return errors


# ---------------------------------------------------------------------------
# Standing commitments
# ---------------------------------------------------------------------------
# Shared by the owner settings form and scripts/add_recurring.py, so the two
# routes in can't drift on what a valid schedule is. The validator works on
# already-parsed values; turning form strings into them is `parse_commitment_form`
# below, which argparse does for the CLI.

DAY_RULES = ("day_of_month", "business_days_before_month_end")
MAX_GENERATE_DAYS_AHEAD = 366
MAX_LEAD_BUSINESS_DAYS = 60


@dataclass
class CommitmentInput:
    title: str
    description: str
    request_class: str
    source: str
    months: list[int] | None = None
    day_of_month: int | None = None
    business_days_before_month_end: int | None = None
    lead_business_days: int = 0
    date_reason: str | None = None
    additional_context: str | None = None
    generate_days_ahead: int = 14
    reference_url: str | None = None
    reference_label: str | None = None


def validate_commitment(data: CommitmentInput) -> dict[str, str]:
    """Returns field-name -> message for everything wrong with a commitment."""
    errors: dict[str, str] = {}

    if not data.title.strip():
        errors["title"] = "A title is required."
    elif _too_long(data.title, MAX_TITLE_LEN):
        errors["title"] = "Title is too long."

    if not data.description.strip():
        errors["description"] = "Describe what doing it involves."
    elif _too_long(data.description, MAX_DESCRIPTION_LEN):
        errors["description"] = "Description is too long."

    if data.request_class not in ("quick", "long"):
        errors["request_class"] = "Choose whether this is quick or takes longer."

    # `form` means a human filled in the public submission form; a generated
    # request never did, so it must not be able to claim that source.
    if data.source not in ("email", "manual"):
        errors["source"] = "Source must be email or manual."

    if data.months is not None:
        if not data.months:
            errors["months"] = "Leave this blank for every month, rather than empty."
        elif any(not 1 <= m <= 12 for m in data.months):
            errors["months"] = "Months must be numbers between 1 and 12."

    has_day = data.day_of_month is not None
    has_month_end = data.business_days_before_month_end is not None
    if has_day == has_month_end:
        errors["day_rule"] = "Set exactly one of day of month or business days before month end."
    elif has_day and not 1 <= data.day_of_month <= 31:
        errors["day_of_month"] = "Day of month must be between 1 and 31."
    elif has_month_end and not 0 <= data.business_days_before_month_end <= 25:
        errors["business_days_before_month_end"] = "Must be between 0 and 25."

    if not 0 <= data.lead_business_days <= MAX_LEAD_BUSINESS_DAYS:
        errors["lead_business_days"] = f"Must be between 0 and {MAX_LEAD_BUSINESS_DAYS}."

    if not 0 <= data.generate_days_ahead <= MAX_GENERATE_DAYS_AHEAD:
        errors["generate_days_ahead"] = f"Must be between 0 and {MAX_GENERATE_DAYS_AHEAD}."

    if data.date_reason and _too_long(data.date_reason, MAX_REASON_LEN):
        errors["date_reason"] = "Reason is too long."
    if data.additional_context and _too_long(data.additional_context, MAX_CONTEXT_LEN):
        errors["additional_context"] = "Additional context is too long."

    url = (data.reference_url or "").strip()
    if url:
        if _too_long(url, MAX_LINK_URL_LEN):
            errors["reference_url"] = "Reference URL is too long."
        elif not (url.startswith("http://") or url.startswith("https://")):
            errors["reference_url"] = "Reference URL must start with http:// or https://."
    elif (data.reference_label or "").strip():
        errors["reference_label"] = "A label needs a URL to label."
    if data.reference_label and _too_long(data.reference_label, MAX_LINK_LABEL_LEN):
        errors["reference_label"] = "Reference label is too long."

    return errors


def parse_months(raw: str) -> list[int] | None:
    """Parses "4 8 12" or "4,8,12" into month numbers; blank means every month.

    Raises ValueError on anything that isn't a list of integers, so the
    caller can report that separately from an out-of-range month.
    """
    cleaned = raw.replace(",", " ").split()
    if not cleaned:
        return None
    return sorted({int(part) for part in cleaned})


def parse_commitment_form(form: dict) -> tuple[CommitmentInput, dict[str, str]]:
    """Turns raw settings-form strings into a CommitmentInput.

    Returns the input alongside any errors from parsing itself (a
    non-numeric month, say). Range and presence rules belong to
    `validate_commitment`, which runs on the result; both error dicts are
    keyed by form field name so the template can show them inline.
    """
    errors: dict[str, str] = {}

    def _text(name: str) -> str:
        return str(form.get(name, "")).strip()

    def _int(name: str, default: int | None = None) -> int | None:
        raw = _text(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            errors[name] = "Enter a whole number."
            return default

    try:
        months = parse_months(_text("months"))
    except ValueError:
        months = None
        errors["months"] = "Enter month numbers separated by spaces or commas."

    day_rule = _text("day_rule")
    day_value = _int("day_value")
    if day_rule not in DAY_RULES:
        errors["day_rule"] = "Choose how the day within the month is set."
    elif day_value is None and "day_value" not in errors:
        errors["day_value"] = "Enter a value for the chosen rule."

    data = CommitmentInput(
        title=_text("title"),
        description=_text("description"),
        request_class=_text("request_class"),
        source=_text("source") or "manual",
        months=months,
        day_of_month=day_value if day_rule == "day_of_month" else None,
        business_days_before_month_end=(
            day_value if day_rule == "business_days_before_month_end" else None
        ),
        lead_business_days=_int("lead_business_days", 0) or 0,
        date_reason=_text("date_reason") or None,
        additional_context=_text("additional_context") or None,
        generate_days_ahead=_int("generate_days_ahead", 14) if _text("generate_days_ahead") else 14,
        reference_url=_text("reference_url") or None,
        reference_label=_text("reference_label") or None,
    )
    if data.generate_days_ahead is None:
        data.generate_days_ahead = 14
    return data, errors
