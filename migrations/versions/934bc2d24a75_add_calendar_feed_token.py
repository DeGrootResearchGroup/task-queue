"""add calendar feed token

Revision ID: 934bc2d24a75
Revises: 18ef99861239
Create Date: 2026-11-23 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '934bc2d24a75'
down_revision: Union[str, Sequence[str], None] = '18ef99861239'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Nullable with no backfill: the feed stays switched off until the Owner
    generates a token from /settings, so an upgrade never silently starts
    publishing dates on a guessable URL.
    """
    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.add_column(sa.Column('calendar_token', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_app_settings_calendar_token', ['calendar_token'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.drop_constraint('uq_app_settings_calendar_token', type_='unique')
        batch_op.drop_column('calendar_token')
