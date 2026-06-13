"""add irrelevant_description to whatsapp_outgoing_payments

Revision ID: s6t7u8v9w0x1
Revises: b8991e8f8ffe
Create Date: 2026-06-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 's6t7u8v9w0x1'
down_revision: Union[str, None] = 'b8991e8f8ffe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'whatsapp_outgoing_payments',
        sa.Column('irrelevant_description', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('whatsapp_outgoing_payments', 'irrelevant_description')
