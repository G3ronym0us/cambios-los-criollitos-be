"""add fund_pending_deposits table

Revision ID: v9w0x1y2z3a4
Revises: u8v9w0x1y2z3
Create Date: 2026-06-23 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'v9w0x1y2z3a4'
down_revision: Union[str, None] = 'u8v9w0x1y2z3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Nombre de tipo derivado del nombre de la clase enum en minúsculas (SQLEnum default).
status_enum = postgresql.ENUM(
    'PENDING', 'CONFIRMED', 'REJECTED',
    name='fundpendingdepositstatus',
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'fund_pending_deposits',
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('detected_user_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=True),
        sa.Column('provider', sa.String(length=60), nullable=True),
        sa.Column('reference', sa.String(length=120), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('status', status_enum, nullable=False, server_default='PENDING'),
        sa.Column('confirmed_movement_id', sa.Integer(), nullable=True),
        sa.Column('resolved_by_user_id', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['fund_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['detected_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['confirmed_movement_id'], ['fund_movements.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_fund_pending_deposits_uuid'), 'fund_pending_deposits', ['uuid'], unique=True)
    op.create_index(op.f('ix_fund_pending_deposits_id'), 'fund_pending_deposits', ['id'])
    op.create_index(op.f('ix_fund_pending_deposits_group_id'), 'fund_pending_deposits', ['group_id'])
    op.create_index(
        op.f('ix_fund_pending_deposits_detected_user_id'), 'fund_pending_deposits', ['detected_user_id']
    )
    op.create_index(op.f('ix_fund_pending_deposits_status'), 'fund_pending_deposits', ['status'])
    op.create_index(
        op.f('ix_fund_pending_deposits_confirmed_movement_id'),
        'fund_pending_deposits',
        ['confirmed_movement_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_fund_pending_deposits_confirmed_movement_id'), table_name='fund_pending_deposits')
    op.drop_index(op.f('ix_fund_pending_deposits_status'), table_name='fund_pending_deposits')
    op.drop_index(op.f('ix_fund_pending_deposits_detected_user_id'), table_name='fund_pending_deposits')
    op.drop_index(op.f('ix_fund_pending_deposits_group_id'), table_name='fund_pending_deposits')
    op.drop_index(op.f('ix_fund_pending_deposits_id'), table_name='fund_pending_deposits')
    op.drop_index(op.f('ix_fund_pending_deposits_uuid'), table_name='fund_pending_deposits')
    op.drop_table('fund_pending_deposits')
    status_enum.drop(op.get_bind(), checkfirst=True)
