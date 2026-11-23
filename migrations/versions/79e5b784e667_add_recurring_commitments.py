"""add recurring commitments

Revision ID: 79e5b784e667
Revises: 934bc2d24a75
Create Date: 2026-11-23 15:37:25.190366

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79e5b784e667'
down_revision: Union[str, Sequence[str], None] = '934bc2d24a75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SQLite can't ALTER TABLE ADD CONSTRAINT, so the foreign key on requests
# has to go on through batch mode's table rebuild — and it needs an
# explicit name, since an unnamed constraint can't be dropped again.
FK_NAME = 'fk_requests_recurrence_id_recurring_commitments'


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'recurring_commitments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('additional_context', sa.Text(), nullable=True),
        sa.Column('request_class', sa.Enum('quick', 'long', name='requestclass'), nullable=False),
        sa.Column('source', sa.Enum('form', 'email', 'manual', name='requestsource'), nullable=False),
        sa.Column('months', sa.JSON(), nullable=True),
        sa.Column('day_of_month', sa.Integer(), nullable=True),
        sa.Column('business_days_before_month_end', sa.Integer(), nullable=True),
        sa.Column('lead_business_days', sa.Integer(), nullable=False),
        sa.Column('date_reason', sa.Text(), nullable=True),
        sa.Column('generate_days_ahead', sa.Integer(), nullable=False),
        sa.Column('reference_url', sa.String(length=2000), nullable=True),
        sa.Column('reference_label', sa.String(length=200), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('last_generated_stated_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('requests') as batch_op:
        batch_op.add_column(sa.Column('recurrence_id', sa.Integer(), nullable=True))
        batch_op.create_index(op.f('ix_requests_recurrence_id'), ['recurrence_id'], unique=False)
        batch_op.create_foreign_key(FK_NAME, 'recurring_commitments', ['recurrence_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('requests') as batch_op:
        batch_op.drop_constraint(FK_NAME, type_='foreignkey')
        batch_op.drop_index(op.f('ix_requests_recurrence_id'))
        batch_op.drop_column('recurrence_id')
    op.drop_table('recurring_commitments')
