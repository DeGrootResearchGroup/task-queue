"""Composes the actual subject/body text for each notification event and
hands it off to app.email.send_email. Kept separate from app/email.py so
the transport layer stays generic and this module can own the copy.

Every function here is meant to be called via FastAPI's BackgroundTasks —
none of them return anything meaningful, and app.email.send_email already
swallows its own errors, so these are safe to fire-and-forget.
"""

from app.config import get_settings
from app.email import send_email
from app.models import AppSettings
from app.models import Request as RequestModel


def _tracking_url(base_url: str, req: RequestModel) -> str:
    return f"{base_url}r/{req.access_token}"


def _admin_url(base_url: str, req: RequestModel) -> str:
    return f"{base_url}requests/{req.id}"


def notify_requester_received(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    if req.request_class.value == "quick":
        status_line = "It's a Quick Action, so it's already in the queue to be processed shortly."
    else:
        status_line = "It's a Long Action, so it's awaiting review before it's accepted into the queue."
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" has been received.\n\n'
        f"{status_line}\n\n"
        f"Track its status any time at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(req.requester_email, f"[{req.public_number}] Request received: {req.title}", body)


def notify_requester_accepted(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" has been accepted and added to the queue.\n\n'
        f"Track its position any time at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(req.requester_email, f"[{req.public_number}] Request accepted: {req.title}", body)


def notify_requester_declined(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    reason_line = f"\n\nReason: {req.decline_reason}" if req.decline_reason else ""
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" was declined.{reason_line}\n\n'
        f"Details at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(req.requester_email, f"[{req.public_number}] Request declined: {req.title}", body)


def notify_requester_needs_information(
    base_url: str, req: RequestModel, question: str, settings_row: AppSettings
) -> None:
    body = (
        f"Hi {req.requester_name},\n\n"
        f'{settings_row.owner_display_name} needs more information on your request "{req.title}" '
        f"before continuing:\n\n"
        f"    {question}\n\n"
        f"Respond at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(req.requester_email, f"[{req.public_number}] Action needed: {req.title}", body)


def notify_requester_completed(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    note_line = f"\n\n{req.completion_note}" if req.completion_note else ""
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" has been completed.{note_line}\n\n'
        f"Details at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(req.requester_email, f"[{req.public_number}] Request completed: {req.title}", body)


def notify_owner_new_request(base_url: str, req: RequestModel) -> None:
    settings = get_settings()
    if not settings.owner_notification_email:
        return
    kind = "Quick Action" if req.request_class.value == "quick" else "Long Action"
    body = (
        f"New {kind} submitted by {req.requester_name} ({req.requester_email}):\n\n"
        f'"{req.title}"\n\n'
        f"{req.description}\n\n"
        f"Review at:\n{_admin_url(base_url, req)}"
    )
    send_email(
        settings.owner_notification_email,
        f"[{req.public_number}] New {kind.lower()}: {req.title}",
        body,
    )


def notify_owner_requester_update(base_url: str, req: RequestModel, content: str) -> None:
    settings = get_settings()
    if not settings.owner_notification_email:
        return
    body = (
        f'{req.requester_name} added information to "{req.title}":\n\n'
        f"{content}\n\n"
        f"Review at:\n{_admin_url(base_url, req)}"
    )
    send_email(
        settings.owner_notification_email,
        f"[{req.public_number}] New information: {req.title}",
        body,
    )
