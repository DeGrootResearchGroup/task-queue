import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base


class RequestClass(str, enum.Enum):
    quick = "quick"
    long = "long"
    # Not work to be worked through -- a commitment at a fixed time. An
    # accepted event is pushed to the Western Exchange calendar so it
    # consumes availability for colleagues booking through Outlook; the
    # .ics feed cannot do that, because a subscribed feed is never part of
    # the Exchange mailbox that free/busy is read from.
    #
    # Events deliberately carry no queue_position. They are not ordered
    # against Quick and Long Actions, because their time is already fixed
    # and a prioritised list is for deciding what to do next.
    event = "event"


_CLASS_LABELS = {"quick": "Quick Action", "long": "Long Action", "event": "Event"}


def class_label(request_class: "RequestClass") -> str:
    """Display name for a request class.

    Exists because the label was written as an inline
    `"Quick Action" if ... == "quick" else "Long Action"` in four places,
    every one of which silently called an Event a Long Action the moment
    a third class existed.
    """
    return _CLASS_LABELS.get(request_class.value, request_class.value.title())


class RequestSource(str, enum.Enum):
    """Where a request came from.

    The queue was built as a service desk for other people's requests, so
    self-generated admin needs to stay visually separable from the real
    thing rather than being mixed into the same undifferentiated list.
    """

    form = "form"  # a true reverse task — someone else filled in the public form
    email = "email"  # derived from the Owner's mailbox by a triage sweep
    manual = "manual"  # added by the Owner directly, or via an assistant


class RequestStatus(str, enum.Enum):
    submitted = "submitted"
    queued = "queued"
    in_progress = "in_progress"
    needs_information = "needs_information"
    done = "done"
    declined = "declined"
    withdrawn = "withdrawn"
    # Closed without being done and without being refused: the occasion for
    # it went away. A month with no PCard transactions to reconcile, a term
    # with no exams to book scanning for. See state_machine.cancel for why
    # none of the three above could carry this.
    cancelled = "cancelled"


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
    cancelled = "cancelled"
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

    # Nullable because a request need not have a second party: `email` and
    # `manual` sources are the Owner's own obligations, with nobody to name
    # or notify. A sentinel ("self@localhost") was the alternative and was
    # rejected — it would match the Archive's requester search, print as a
    # fake address on the detail page, and turn "is there a requester?"
    # into a string comparison against a magic value in every caller.
    requester_name: Mapped[str | None] = mapped_column(String(200), default=None)
    requester_email: Mapped[str | None] = mapped_column(String(320), default=None)

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    additional_context: Mapped[str | None] = mapped_column(Text, default=None)

    source: Mapped[RequestSource] = mapped_column(
        Enum(RequestSource), default=RequestSource.form, server_default=RequestSource.form.value
    )

    request_class: Mapped[RequestClass] = mapped_column(Enum(RequestClass))
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus))
    previous_status: Mapped[RequestStatus | None] = mapped_column(
        Enum(RequestStatus), default=None
    )

    desired_completion_date: Mapped[date | None] = mapped_column(Date, default=None)
    desired_date_reason: Mapped[str | None] = mapped_column(Text, default=None)

    # --- Events only ---------------------------------------------------
    # A request carries a date and nothing finer, which is right for a
    # deadline and not enough for an event. These are null for every other
    # class.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    location: Mapped[str | None] = mapped_column(String(300), default=None)

    # The UID Calendar.app returns when the event is created. Stored
    # because reconciliation needs it and searching for the event instead
    # is not practical: `events of cal whose uid is ...` against the
    # Exchange calendar takes over three minutes and needs an extended
    # AppleEvent timeout to complete at all (measured 2026-11-24). A
    # date-bounded window is no faster -- the cost is Calendar resolving
    # the account, not the size of the range.
    #
    # Set only once the event is actually on the calendar, so it doubles
    # as the write marker: accepted event + null uid == still to write.
    calendar_uid: Mapped[str | None] = mapped_column(String(300), default=None)
    calendar_written_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Caller-supplied idempotency key for the HTTP intake endpoint, normally
    # the RFC message-id of the originating email. Unique, so a sweep that
    # re-runs — or two sweeps that both see the same message — cannot create
    # a second request for it. Nullable, and SQLite treats NULLs in a unique
    # index as distinct, so everything that arrives by any other route
    # coexists without needing a key at all.
    dedupe_key: Mapped[str | None] = mapped_column(String(500), unique=True, default=None)

    # Set when this request was generated from a standing commitment, so a
    # generated instance can be traced back to the rule that produced it.
    recurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_commitments.id"), default=None, index=True
    )

    queue_position: Mapped[int | None] = mapped_column(Integer, default=None)
    previous_queue_position: Mapped[int | None] = mapped_column(Integer, default=None)

    decline_reason: Mapped[str | None] = mapped_column(Text, default=None)
    cancel_reason: Mapped[str | None] = mapped_column(Text, default=None)
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
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

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
    recurrence: Mapped["RecurringCommitment | None"] = relationship(back_populates="generated")


class RecurringCommitment(Base):
    """An obligation that arrives on a cycle, recorded once.

    See app/recurrence.py for how the schedule fields combine and why the
    model is "which months + which day" rather than a frequency enum.
    Generating a Request from one of these is the only way the queue holds
    work that nobody has asked for yet.
    """

    __tablename__ = "recurring_commitments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    additional_context: Mapped[str | None] = mapped_column(Text, default=None)
    request_class: Mapped[RequestClass] = mapped_column(Enum(RequestClass))
    source: Mapped[RequestSource] = mapped_column(Enum(RequestSource), default=RequestSource.manual)

    # Which months it falls in: a list of month numbers, or null/empty for
    # every month. Monthly PCard is null; annual Flex Credits is [11]; a
    # per-term obligation is simply a three-element list.
    months: Mapped[list[int] | None] = mapped_column(JSON, default=None)

    # Exactly one of these two sets the day within the month.
    day_of_month: Mapped[int | None] = mapped_column(Integer, default=None)
    business_days_before_month_end: Mapped[int | None] = mapped_column(Integer, default=None)

    # The effective date rule: business days to back the recorded date off
    # the stated one, where a second party must act afterwards.
    lead_business_days: Mapped[int] = mapped_column(Integer, default=0)
    date_reason: Mapped[str | None] = mapped_column(Text, default=None)

    # How far before the effective date the request appears on the queue.
    generate_days_ahead: Mapped[int] = mapped_column(Integer, default=14)

    reference_url: Mapped[str | None] = mapped_column(String(2000), default=None)
    reference_label: Mapped[str | None] = mapped_column(String(200), default=None)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # The stated date of the most recently generated occurrence. Advancing
    # this is what stops a second run of the generator producing a
    # duplicate for the same occurrence.
    last_generated_stated_date: Mapped[date | None] = mapped_column(Date, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    generated: Mapped[list["Request"]] = relationship(
        back_populates="recurrence", order_by="Request.desired_completion_date"
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
    # Secret path segment for the read-only .ics feed. Null until the Owner
    # generates one from /settings; regenerating it revokes every existing
    # subscription, which is the only revocation mechanism a subscribed
    # calendar feed can have.
    calendar_token: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)


class RequestCounter(Base):
    """Singleton row (id=1) tracking the next sequential public request number."""

    __tablename__ = "request_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_value: Mapped[int] = mapped_column(Integer, default=1)
