"""make to_amount nullable in transactions

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-03-24 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'j7k8l9m0n1o2'
down_revision: Union[str, None] = 'i6j7k8l9m0n1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('transactions', 'to_amount', nullable=True)

    # Limpiar to_amount de registros migrados donde era igual al from_amount
    op.execute("""
        UPDATE transactions
        SET to_amount = NULL
        WHERE from_currency IN ('ZELLE', 'PAYPAL')
          AND to_amount = from_amount
    """)


def downgrade() -> None:
    # Restaurar to_amount con from_amount donde sea NULL
    op.execute("""
        UPDATE transactions
        SET to_amount = from_amount
        WHERE to_amount IS NULL
    """)
    op.alter_column('transactions', 'to_amount', nullable=False)
