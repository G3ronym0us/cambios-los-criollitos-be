"""add commission fields to users

Revision ID: e2f3g4h5i6j7
Revises: d1e2f3g4h5i6
Create Date: 2025-10-13 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f3g4h5i6j7'
down_revision: Union[str, None] = 'd1e2f3g4h5i6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Add commission field to users."""

    # Add commission field
    op.add_column('users', sa.Column('can_receive_commission', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    """Downgrade schema - Remove commission field."""

    op.drop_column('users', 'can_receive_commission')
