"""add event class with start, end and calendar uid

Revision ID: c96d765c9933
Revises: 64fe0e30b401
Create Date: 2026-11-24 13:37:59.673762

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c96d765c9933'
down_revision: Union[str, Sequence[str], None] = '64fe0e30b401'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Four nullable columns and no backfill — nothing that predates this is
    an event. The new `event` member of RequestClass needs no DDL, for the
    same reason the `cancelled` status did not: SQLAlchemy renders an Enum
    on SQLite as a plain VARCHAR with no CHECK constraint, so the set of
    permitted values lives in Python alone.

    `calendar_uid` is not unique. Two requests could legitimately point at
    one calendar event if an occasion were ever captured twice from
    different mail, and a unique index would turn that into a write
    failure in the sweep rather than something visible on the dashboard.
    """
    op.add_column('requests', sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('location', sa.String(length=300), nullable=True))
    op.add_column('requests', sa.Column('calendar_uid', sa.String(length=300), nullable=True))
    op.add_column(
        'requests', sa.Column('calendar_written_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema.

    Events become Long Actions rather than being deleted. The alternative
    — dropping them — would discard accepted commitments, and a Long
    Action carrying the right title and date is a survivable wrong answer
    where a missing one is not.

    Note this leaves any already-written calendar events orphaned on the
    Exchange calendar, because the UID that would let the sweep delete
    them goes away with the column. Clear them by hand before downgrading
    if that matters.
    """
    op.execute("UPDATE requests SET request_class = 'long' WHERE request_class = 'event'")
    op.drop_column('requests', 'calendar_written_at')
    op.drop_column('requests', 'calendar_uid')
    op.drop_column('requests', 'location')
    op.drop_column('requests', 'ends_at')
    op.drop_column('requests', 'starts_at')
