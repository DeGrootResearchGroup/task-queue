import enum
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base


class RequestClass(str, enum.Enum):
    quick = "quick"
    long = "long"


class RequestStatus(str, enum.Enum):
    submitted = "submitted"
    queued = "queued"
    in_progress = "in_progress"
    needs_information = "needs_information"
    done = "done"
    declined = "declined"
    withdrawn = "withdrawn"


class EventType(str, enum.Enum):
    submitted = "submitted"
    accepted = "accepted"
    info_requested = "info_requested"
    info_responded = "info_responded"
    started = "started"
    returned_to_queue = "returned_to_queue"
    completed = "completed"
    declined = "declined"
    withdrawn = "withdrawn"
    moved_to_long = "moved_to_long"
    information_added = "information_added"
    reordered = "reordered"


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        # status is filtered on its own (e.g. the dashboard's "needs
        # information" query) and combined with request_class (the
        # dashboard's other queries, quick_session's next-action lookup,
        # state_machine's _queued_siblings) — leading with status lets this
        # one composite index serve both the status-only and status+class
        # query shapes, rather than needing two separate indexes.
        Index("ix_requests_status_request_class", "status", "request_class"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    access_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    requester_name: Mapped[str] = mapped_column(String(200))
    requester_email: Mapped[str] = mapped_column(String(320))

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    additional_context: Mapped[str | None] = mapped_column(Text, default=None)

    request_class: Mapped[RequestClass] = mapped_column(Enum(RequestClass))
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus))
    previous_status: Mapped[RequestStatus | None] = mapped_column(
        Enum(RequestStatus), default=None
    )

    desired_completion_date: Mapped[date | None] = mapped_column(Date, default=None)
    desired_date_reason: Mapped[str | None] = mapped_column(Text, default=None)

    queue_position: Mapped[int | None] = mapped_column(Integer, default=None)
    previous_queue_position: Mapped[int | None] = mapped_column(Integer, default=None)

    decline_reason: Mapped[str | None] = mapped_column(Text, default=None)
    completion_note: Mapped[str | None] = mapped_column(Text, default=None)

    new_information_flag: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    declined_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    links: Mapped[list["RequestLink"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestLink.id"
    )
    updates: Mapped[list["RequestUpdate"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestUpdate.created_at"
    )
    info_requests: Mapped[list["InformationRequest"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="InformationRequest.requested_at",
    )
    events: Mapped[list["RequestEvent"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestEvent.created_at"
    )


class RequestLink(Base):
    __tablename__ = "request_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    label: Mapped[str | None] = mapped_column(String(200), default=None)
    url: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    request: Mapped["Request"] = relationship(back_populates="links")


class RequestUpdate(Base):
    """Requester 'Add information' entries and Owner completion notes."""

    __tablename__ = "request_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    author_type: Mapped[str] = mapped_column(String(20))  # "owner" | "requester"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    request: Mapped["Request"] = relationship(back_populates="updates")


class InformationRequest(Base):
    __tablename__ = "information_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    owner_question: Mapped[str] = mapped_column(Text)
    requester_response: Mapped[str | None] = mapped_column(Text, default=None)
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    request: Mapped["Request"] = relationship(back_populates="info_requests")


class RequestEvent(Base):
    __tablename__ = "request_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    event_type: Mapped[EventType] = mapped_column(Enum(EventType))
    event_metadata: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    request: Mapped["Request"] = relationship(back_populates="events")

    # Internal-only events (not shown on requester tracking page, per spec §21)
    REQUESTER_HIDDEN = {EventType.reordered}


class AppSettings(Base):
    """Singleton row (id=1) holding Owner-configurable settings, per spec §77."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner_display_name: Mapped[str] = mapped_column(String(200), default="The Owner")
    meeting_booking_url: Mapped[str | None] = mapped_column(String(2000), default=None)
    meeting_booking_text: Mapped[str | None] = mapped_column(Text, default=None)
    quick_action_minutes: Mapped[int] = mapped_column(Integer, default=10)
    urgency_green_days: Mapped[int] = mapped_column(Integer, default=7)
    urgency_red_days: Mapped[int] = mapped_column(Integer, default=2)


class RequestCounter(Base):
    """Singleton row (id=1) tracking the next sequential public request number."""

    __tablename__ = "request_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_value: Mapped[int] = mapped_column(Integer, default=1)
