"""change pair_symbol to currency_pair_id in commission_configurations

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2025-10-14 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h5i6j7k8l9m0'
down_revision: Union[str, None] = 'g4h5i6j7k8l9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Change pair_symbol to currency_pair_id with foreign key."""

    # Eliminar todas las configuraciones existentes (CASCADE eliminará los splits)
    op.execute("DELETE FROM commission_configurations")

    # Add new currency_pair_id column (nullable for now)
    op.add_column(
        'commission_configurations',
        sa.Column('currency_pair_id', sa.Integer(), nullable=True)
    )

    # Make currency_pair_id non-nullable
    op.alter_column('commission_configurations', 'currency_pair_id', nullable=False)

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_commission_configurations_currency_pair_id',
        'commission_configurations',
        'currency_pairs',
        ['currency_pair_id'],
        ['id']
    )

    # Create index on currency_pair_id
    op.create_index(
        'ix_commission_configurations_currency_pair_id',
        'commission_configurations',
        ['currency_pair_id']
    )

    # Drop old pair_symbol column and its index
    op.drop_index('ix_commission_configurations_pair_symbol', table_name='commission_configurations')
    op.drop_column('commission_configurations', 'pair_symbol')


def downgrade() -> None:
    """Downgrade schema - Restore pair_symbol column."""

    # Add back pair_symbol column (nullable for now)
    op.add_column(
        'commission_configurations',
        sa.Column('pair_symbol', sa.String(length=50), nullable=True)
    )

    # Restore data from currency_pairs
    op.execute("""
        UPDATE commission_configurations cc
        SET pair_symbol = cp.pair_symbol
        FROM currency_pairs cp
        WHERE cc.currency_pair_id = cp.id
    """)

    # Make pair_symbol non-nullable
    op.alter_column('commission_configurations', 'pair_symbol', nullable=False)

    # Recreate index on pair_symbol
    op.create_index(
        'ix_commission_configurations_pair_symbol',
        'commission_configurations',
        ['pair_symbol']
    )

    # Drop currency_pair_id and its constraints
    op.drop_index('ix_commission_configurations_currency_pair_id', table_name='commission_configurations')
    op.drop_constraint('fk_commission_configurations_currency_pair_id', 'commission_configurations', type_='foreignkey')
    op.drop_column('commission_configurations', 'currency_pair_id')
