"""add source to requests

Revision ID: f2e28718f55d
Revises: 042e7d748137
Create Date: 2026-11-23 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2e28718f55d'
down_revision: Union[str, Sequence[str], None] = '042e7d748137'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Added with a server_default so the ALTER TABLE can satisfy NOT NULL for
    # the rows that already exist — every one of which is, by definition, a
    # public form submission. The default is kept rather than dropped
    # afterwards: SQLite can only drop a column default by rebuilding the
    # whole table, and leaving it in place also means a raw INSERT that
    # forgets `source` lands on the safe value instead of failing.
    op.add_column(
        'requests',
        sa.Column(
            'source',
            sa.Enum('form', 'email', 'manual', name='requestsource'),
            nullable=False,
            server_default='form',
        ),
    )
    # Explicit backfill. Redundant with the server_default above for a plain
    # SQLite ALTER, but harmless, and it keeps the intent ("everything that
    # predates this column is a form submission") stated in the migration
    # rather than implied by a default.
    op.execute("UPDATE requests SET source = 'form' WHERE source IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('requests', 'source')
