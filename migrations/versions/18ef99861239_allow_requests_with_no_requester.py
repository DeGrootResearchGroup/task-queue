"""allow requests with no requester

Revision ID: 18ef99861239
Revises: f2e28718f55d
Create Date: 2026-11-23 15:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18ef99861239'
down_revision: Union[str, Sequence[str], None] = 'f2e28718f55d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    A request from the `email` or `manual` source is the Owner's own
    obligation: there is no second party to name and nobody to notify, so
    the requester columns become genuinely absent rather than holding a
    sentinel. SQLite can't ALTER a column's nullability in place, so this
    rebuilds the table through Alembic's batch mode.
    """
    with op.batch_alter_table('requests') as batch_op:
        batch_op.alter_column('requester_name', existing_type=sa.String(length=200), nullable=True)
        batch_op.alter_column('requester_email', existing_type=sa.String(length=320), nullable=True)


def downgrade() -> None:
    """Downgrade schema.

    Rows with no requester would violate the restored NOT NULL, so they are
    given an explicit placeholder first; there is no information to recover.
    """
    op.execute("UPDATE requests SET requester_name = '(none)' WHERE requester_name IS NULL")
    op.execute("UPDATE requests SET requester_email = '' WHERE requester_email IS NULL")
    with op.batch_alter_table('requests') as batch_op:
        batch_op.alter_column('requester_name', existing_type=sa.String(length=200), nullable=False)
        batch_op.alter_column('requester_email', existing_type=sa.String(length=320), nullable=False)
