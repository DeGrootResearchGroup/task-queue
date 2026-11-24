"""add blocked status

Revision ID: a4f1c20b9e37
Revises: c96d765c9933
Create Date: 2026-11-24 15:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4f1c20b9e37'
down_revision: Union[str, Sequence[str], None] = 'c96d765c9933'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Four new columns only. The new `blocked` member of RequestStatus and the
    `blocked`/`unblocked` members of EventType need no DDL, for the reason
    given in 8282938591ea: SQLAlchemy renders an Enum on SQLite as a plain
    VARCHAR with no CHECK constraint, so the permitted set lives in Python
    and widening it is not a schema change.

    All nullable, no backfill: nothing that predates this was on hold, and
    `blocked_on` is only required at the point `state_machine.block` sets
    it, not of every row that has never been blocked.
    """
    op.add_column('requests', sa.Column('blocked_on', sa.String(length=300), nullable=True))
    op.add_column('requests', sa.Column('blocked_until', sa.Date(), nullable=True))
    op.add_column('requests', sa.Column('blocked_at', sa.DateTime(), nullable=True))
    op.add_column('requests', sa.Column('blocked_note', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    A blocked request is live work, so unlike the cancelled downgrade it
    must not be pushed into a terminal state — that would silently close
    things nobody closed. `previous_status` is where it was before the hold
    and is exactly where `unblock` would have put it back, so the downgrade
    does that for every held row, falling back to `submitted` for any row
    whose previous status was somehow not recorded.

    Queue positions are not restored: `previous_queue_position` may collide
    with positions handed out while the request was away, and a duplicate
    position is worse than a request sitting at the end of its queue. Rows
    landing back in `queued` keep a null position, which the queue treats as
    the bottom.
    """
    op.execute(
        "UPDATE requests SET status = COALESCE(previous_status, 'submitted'), "
        "previous_status = NULL, previous_queue_position = NULL "
        "WHERE status = 'blocked'"
    )
    op.execute(
        "UPDATE request_events SET event_type = 'information_added' "
        "WHERE event_type IN ('blocked', 'unblocked')"
    )
    op.drop_column('requests', 'blocked_note')
    op.drop_column('requests', 'blocked_at')
    op.drop_column('requests', 'blocked_until')
    op.drop_column('requests', 'blocked_on')
