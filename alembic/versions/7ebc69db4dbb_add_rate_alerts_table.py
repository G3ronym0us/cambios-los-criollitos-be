"""add rate_alerts table

Revision ID: 7ebc69db4dbb
Revises: o2p3q4r5s6t7
Create Date: 2026-05-21 16:31:45.707259

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ebc69db4dbb'
down_revision: Union[str, None] = 'o2p3q4r5s6t7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rate_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.UUID(), nullable=False),
        sa.Column('currency_pair_id', sa.Integer(), nullable=False),
        sa.Column('from_currency', sa.String(length=10), nullable=False),
        sa.Column('to_currency', sa.String(length=10), nullable=False),
        sa.Column('manual_rate', sa.Float(), nullable=False),
        sa.Column('automatic_rate', sa.Float(), nullable=False),
        sa.Column('diff_percentage', sa.Float(), nullable=False),
        sa.Column('is_acknowledged', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['currency_pair_id'], ['currency_pairs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_rate_alerts_id'), 'rate_alerts', ['id'], unique=False)
    op.create_index(op.f('ix_rate_alerts_uuid'), 'rate_alerts', ['uuid'], unique=True)
    op.create_index(op.f('ix_rate_alerts_currency_pair_id'), 'rate_alerts', ['currency_pair_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_rate_alerts_currency_pair_id'), table_name='rate_alerts')
    op.drop_index(op.f('ix_rate_alerts_uuid'), table_name='rate_alerts')
    op.drop_index(op.f('ix_rate_alerts_id'), table_name='rate_alerts')
    op.drop_table('rate_alerts')
