"""add deposit_method and incoming_payment_id to fund_movements

Revision ID: t7u8v9w0x1y2
Revises: s6t7u8v9w0x1
Create Date: 2026-06-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 't7u8v9w0x1y2'
down_revision: Union[str, None] = 's6t7u8v9w0x1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('fund_movements', sa.Column('deposit_method', sa.String(length=20), nullable=True))
    op.add_column('fund_movements', sa.Column('incoming_payment_id', sa.Integer(), nullable=True))
    op.create_index(
        op.f('ix_fund_movements_incoming_payment_id'),
        'fund_movements',
        ['incoming_payment_id'],
        unique=False,
    )
    op.create_foreign_key(
        'fk_fund_movements_incoming_payment',
        'fund_movements',
        'whatsapp_incoming_payments',
        ['incoming_payment_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_fund_movements_incoming_payment', 'fund_movements', type_='foreignkey')
    op.drop_index(op.f('ix_fund_movements_incoming_payment_id'), table_name='fund_movements')
    op.drop_column('fund_movements', 'incoming_payment_id')
    op.drop_column('fund_movements', 'deposit_method')
