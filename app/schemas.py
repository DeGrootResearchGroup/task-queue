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
