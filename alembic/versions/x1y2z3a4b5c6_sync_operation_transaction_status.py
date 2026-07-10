"""sync operation and transaction status

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
Create Date: 2026-07-10 00:30:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, None] = "w0x1y2z3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL no permite usar un valor nuevo del enum dentro de la misma
    # transacción en la que fue agregado.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE transactionstatus ADD VALUE IF NOT EXISTS 'QUOTED'")

    op.execute(
        """
        UPDATE transactions AS tx
        SET status = operation.status::text::transactionstatus,
            completed_at = CASE
                WHEN operation.status::text = 'COMPLETED'
                THEN COALESCE(operation.completed_at, tx.completed_at)
                ELSE NULL
            END
        FROM whatsapp_operations AS operation
        WHERE operation.transaction_id = tx.id
        """
    )


def downgrade() -> None:
    # PostgreSQL no soporta eliminar de forma segura un valor de un enum en uso.
    pass
