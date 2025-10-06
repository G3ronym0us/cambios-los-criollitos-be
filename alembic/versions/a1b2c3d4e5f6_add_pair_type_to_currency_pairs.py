"""Add pair_type to currency_pairs

Revision ID: a1b2c3d4e5f6
Revises: f37dc49de924
Create Date: 2025-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f37dc49de924'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create enum type for pair_type
    pair_type_enum = sa.Enum('BASE', 'DERIVED', 'CROSS', name='pairtype')
    pair_type_enum.create(op.get_bind(), checkfirst=True)

    # Add pair_type column with default value 'BASE'
    op.add_column('currency_pairs',
                  sa.Column('pair_type', pair_type_enum, nullable=False, server_default='BASE'))


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the column
    op.drop_column('currency_pairs', 'pair_type')

    # Drop the enum type
    pair_type_enum = sa.Enum('BASE', 'DERIVED', 'CROSS', name='pairtype')
    pair_type_enum.drop(op.get_bind(), checkfirst=True)
