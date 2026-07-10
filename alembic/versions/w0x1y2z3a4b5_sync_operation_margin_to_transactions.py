"""sync operation margin to linked transactions

Revision ID: w0x1y2z3a4b5
Revises: v9w0x1y2z3a4
Create Date: 2026-07-10 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "w0x1y2z3a4b5"
down_revision: Union[str, None] = "v9w0x1y2z3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Repara únicamente transacciones sin margen ni reparto. Si ya hay splits,
    # prevalece la contabilidad existente y no se reescribe automáticamente.
    op.execute(
        """
        UPDATE transactions AS tx
        SET total_profit_percentage = operation.applied_percentage,
            profit_amount = tx.to_amount * operation.applied_percentage / 100.0
        FROM whatsapp_operations AS operation
        WHERE operation.transaction_id = tx.id
          AND operation.applied_percentage IS NOT NULL
          AND tx.total_profit_percentage = 0
          AND NOT EXISTS (
              SELECT 1
              FROM transaction_profit_splits AS split
              WHERE split.transaction_id = tx.id
          )
        """
    )


def downgrade() -> None:
    # Data migration: no es seguro distinguir después cuáles valores eran 0 legítimos.
    pass
