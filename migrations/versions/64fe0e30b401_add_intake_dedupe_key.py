"""add intake dedupe key

Revision ID: 64fe0e30b401
Revises: 8282938591ea
Create Date: 2026-11-23 16:35:09.707154

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64fe0e30b401'
down_revision: Union[str, Sequence[str], None] = '8282938591ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# SQLite can't ALTER TABLE ADD CONSTRAINT, so the unique index goes on
# through batch mode's table rebuild, and it needs an explicit name or it
# can't be dropped again.
UQ_NAME = 'uq_requests_dedupe_key'


def upgrade() -> None:
    """Upgrade schema.

    Nullable with no backfill: everything that predates the HTTP intake
    arrived by a route that has no idempotency key, and SQLite treats NULLs
    in a unique index as distinct, so they all coexist.
    """
    with op.batch_alter_table('requests') as batch_op:
        batch_op.add_column(sa.Column('dedupe_key', sa.String(length=500), nullable=True))
        batch_op.create_unique_constraint(UQ_NAME, ['dedupe_key'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('requests') as batch_op:
        batch_op.drop_constraint(UQ_NAME, type_='unique')
        batch_op.drop_column('dedupe_key')
