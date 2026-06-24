"""add fund_group_id to whatsapp_incoming_payments

Revision ID: u8v9w0x1y2z3
Revises: t7u8v9w0x1y2
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'u8v9w0x1y2z3'
down_revision: Union[str, None] = 't7u8v9w0x1y2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('whatsapp_incoming_payments', sa.Column('fund_group_id', sa.Integer(), nullable=True))
    op.create_index(
        op.f('ix_whatsapp_incoming_payments_fund_group_id'),
        'whatsapp_incoming_payments',
        ['fund_group_id'],
        unique=False,
    )
    op.create_foreign_key(
        'fk_whatsapp_incoming_payments_fund_group',
        'whatsapp_incoming_payments',
        'fund_groups',
        ['fund_group_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'fk_whatsapp_incoming_payments_fund_group', 'whatsapp_incoming_payments', type_='foreignkey'
    )
    op.drop_index(
        op.f('ix_whatsapp_incoming_payments_fund_group_id'), table_name='whatsapp_incoming_payments'
    )
    op.drop_column('whatsapp_incoming_payments', 'fund_group_id')
