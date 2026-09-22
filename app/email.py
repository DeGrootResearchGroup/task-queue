"""Best-effort outbound email over Gmail's SMTP relay (spec §35, deferred
in v1, now optional). Every call here is designed to be safe to run from a
FastAPI BackgroundTask: it never raises, so a failed or unconfigured send
can never break the request/response cycle or roll back a state change
that already committed.
"""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.config import get_settings

logger = logging.getLogger("app.email")


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.email_enabled:
        logger.debug("Email notifications not configured; skipping send to %s", to)
        return
    if not to:
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_username))
    msg["To"] = to
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
    except Exception:
        logger.exception("Failed to send email to %s (subject: %s)", to, subject)
