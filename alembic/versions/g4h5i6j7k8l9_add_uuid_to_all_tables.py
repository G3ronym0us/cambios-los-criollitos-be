"""add uuid to all tables

Revision ID: g4h5i6j7k8l9
Revises: f3g4h5i6j7k8
Create Date: 2025-10-14 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid


# revision identifiers, used by Alembic.
revision: str = 'g4h5i6j7k8l9'
down_revision: Union[str, None] = 'f3g4h5i6j7k8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Add UUID columns to all tables."""

    # List of all tables to add UUID column
    tables = [
        'users',
        'transactions',
        'transaction_profit_splits',
        'commission_configurations',
        'commission_configuration_splits',
        'currencies',
        'currency_pairs',
        'exchange_rates'
    ]

    for table in tables:
        # Add uuid column
        op.add_column(
            table,
            sa.Column('uuid', UUID(as_uuid=True), nullable=True, unique=True)
        )

        # Generate UUIDs for existing rows
        op.execute(f"""
            UPDATE {table}
            SET uuid = gen_random_uuid()
            WHERE uuid IS NULL
        """)

        # Make the column non-nullable
        op.alter_column(table, 'uuid', nullable=False)

        # Create index on uuid column
        op.create_index(f'ix_{table}_uuid', table, ['uuid'], unique=True)


def downgrade() -> None:
    """Downgrade schema - Remove UUID columns from all tables."""

    # List of all tables to remove UUID column
    tables = [
        'users',
        'transactions',
        'transaction_profit_splits',
        'commission_configurations',
        'commission_configuration_splits',
        'currencies',
        'currency_pairs',
        'exchange_rates'
    ]

    for table in tables:
        # Drop index first
        op.drop_index(f'ix_{table}_uuid', table_name=table)

        # Drop uuid column
        op.drop_column(table, 'uuid')
