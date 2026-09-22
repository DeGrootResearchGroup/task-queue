import time

from app.config import get_settings

HONEYPOT_FIELD = "website"  # decoy field real users never see or fill
FORM_RENDERED_AT_FIELD = "form_rendered_at"


def form_rendered_at_token() -> str:
    return str(time.time())


def is_spam(honeypot_value: str, form_rendered_at: str) -> bool:
    if honeypot_value:
        return True
    try:
        rendered_at = float(form_rendered_at)
    except (TypeError, ValueError):
        return True
    elapsed = time.time() - rendered_at
    return elapsed < get_settings().min_form_fill_seconds
