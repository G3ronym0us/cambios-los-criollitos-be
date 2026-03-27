"""make exchange_rate nullable in transactions

Revision ID: k8l9m0n1o2p3
Revises: j7k8l9m0n1o2
Create Date: 2026-03-24 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'k8l9m0n1o2p3'
down_revision: Union[str, None] = 'j7k8l9m0n1o2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('transactions', 'exchange_rate', nullable=True)

    op.execute("""
        UPDATE transactions
        SET exchange_rate = NULL
        WHERE from_currency IN ('ZELLE', 'PAYPAL')
          AND exchange_rate = 1.0
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE transactions
        SET exchange_rate = 1.0
        WHERE exchange_rate IS NULL
    """)
    op.alter_column('transactions', 'exchange_rate', nullable=False)
