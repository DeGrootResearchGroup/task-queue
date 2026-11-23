"""add cancelled status

Revision ID: 8282938591ea
Revises: 79e5b784e667
Create Date: 2026-11-23 16:20:49.269322

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8282938591ea'
down_revision: Union[str, Sequence[str], None] = '79e5b784e667'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Only the two new columns. The new `cancelled` member of RequestStatus
    and EventType needs no DDL: SQLAlchemy renders an Enum on SQLite as a
    plain VARCHAR with no CHECK constraint, so the set of permitted values
    lives in Python alone and widening it is not a schema change. Both
    columns are nullable with no backfill — nothing that predates this was
    cancelled.
    """
    op.add_column('requests', sa.Column('cancel_reason', sa.Text(), nullable=True))
    op.add_column('requests', sa.Column('cancelled_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    Rows already cancelled would be left in a status the code no longer
    knows, so they are moved to `withdrawn` — the nearest surviving state —
    before the columns that explain them go away.
    """
    op.execute("UPDATE requests SET status = 'withdrawn' WHERE status = 'cancelled'")
    op.execute(
        "UPDATE requests SET previous_status = 'withdrawn' WHERE previous_status = 'cancelled'"
    )
    op.execute("UPDATE request_events SET event_type = 'withdrawn' WHERE event_type = 'cancelled'")
    op.drop_column('requests', 'cancelled_at')
    op.drop_column('requests', 'cancel_reason')
