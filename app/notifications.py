"""Composes the actual subject/body text for each notification event and
hands it off to app.email.send_email. Kept separate from app/email.py so
the transport layer stays generic and this module can own the copy.

Every function here is meant to be called via FastAPI's BackgroundTasks —
none of them return anything meaningful, and app.email.send_email already
swallows its own errors, so these are safe to fire-and-forget.
"""

import functools
from collections.abc import Callable

from app.config import get_settings
from app.email import send_email
from app.models import AppSettings, class_label
from app.models import Request as RequestModel


def _requires_requester(fn: Callable) -> Callable:
    """No-op the wrapped notification when the request has no requester.

    `email` and `manual` requests are the Owner's own obligations and carry
    no requester_name/requester_email, so every requester-facing message is
    meaningless for them. Guarding here rather than at each call site keeps
    the Owner's dashboard actions (Accept, Decline, Complete, ...) free of
    source-specific branching.
    """

    @functools.wraps(fn)
    def wrapper(base_url: str, req: RequestModel, *args, **kwargs):
        if not req.requester_email:
            return None
        return fn(base_url, req, *args, **kwargs)

    return wrapper


def _tracking_url(base_url: str, req: RequestModel) -> str:
    return f"{base_url}r/{req.access_token}"


def _admin_url(base_url: str, req: RequestModel) -> str:
    return f"{base_url}requests/{req.id}"


def _from_name(settings_row: AppSettings) -> str:
    return f"{settings_row.owner_display_name}'s Task Queue"


@_requires_requester
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
    send_email(
        req.requester_email, f"[{req.public_number}] Request received: {req.title}", body, _from_name(settings_row)
    )


@_requires_requester
def notify_requester_accepted(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" has been accepted and added to the queue.\n\n'
        f"Track its position any time at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(
        req.requester_email, f"[{req.public_number}] Request accepted: {req.title}", body, _from_name(settings_row)
    )


@_requires_requester
def notify_requester_declined(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    reason_line = f"\n\nReason: {req.decline_reason}" if req.decline_reason else ""
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" was declined.{reason_line}\n\n'
        f"Details at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(
        req.requester_email, f"[{req.public_number}] Request declined: {req.title}", body, _from_name(settings_row)
    )


@_requires_requester
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
    send_email(req.requester_email, f"[{req.public_number}] Action needed: {req.title}", body, _from_name(settings_row))


@_requires_requester
def notify_requester_blocked(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    """Tells a requester their request has stalled, and on what.

    No-op without a requester, like every other message here -- which is the
    common case for this status, since blocking exists mainly for the
    Owner's own captured items. It earns its place for the form requests:
    a tracking page that silently stops moving reads as "forgotten", and
    "waiting on the Registrar" is the one fact that makes the wait make
    sense. There is nothing for them to do, so the message says so.
    """
    until_line = (
        f"\n\nI expect to chase this up around {req.blocked_until.strftime('%B %-d, %Y')}."
        if req.blocked_until
        else ""
    )
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" is on hold. It has not been forgotten and it has not '
        f"been declined — it just cannot move until something outside it happens.\n\n"
        f"Waiting on: {req.blocked_on}{until_line}\n\n"
        f"There is nothing you need to do. Follow it at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(
        req.requester_email, f"[{req.public_number}] On hold: {req.title}", body, _from_name(settings_row)
    )


@_requires_requester
def notify_requester_cancelled(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    reason_line = f"\n\nReason: {req.cancel_reason}" if req.cancel_reason else ""
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" has been cancelled — it is no longer needed, '
        f"rather than having been completed or declined.{reason_line}\n\n"
        f"Details:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(
        req.requester_email, f"[{req.public_number}] Request cancelled: {req.title}", body, _from_name(settings_row)
    )


@_requires_requester
def notify_requester_completed(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    note_line = f"\n\n{req.completion_note}" if req.completion_note else ""
    body = (
        f"Hi {req.requester_name},\n\n"
        f'Your request "{req.title}" has been completed.{note_line}\n\n'
        f"Details at:\n{_tracking_url(base_url, req)}\n\n"
        f"— {settings_row.owner_display_name}"
    )
    send_email(
        req.requester_email, f"[{req.public_number}] Request completed: {req.title}", body, _from_name(settings_row)
    )


def notify_owner_new_request(base_url: str, req: RequestModel, settings_row: AppSettings) -> None:
    settings = get_settings()
    if not settings.owner_notification_email:
        return
    kind = class_label(req.request_class)
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
        _from_name(settings_row),
    )


def notify_owner_requester_update(base_url: str, req: RequestModel, content: str, settings_row: AppSettings) -> None:
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
        _from_name(settings_row),
    )
