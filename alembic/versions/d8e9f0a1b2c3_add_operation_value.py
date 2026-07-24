"""add operation value (amount + equivalentes USDT/BCV)

La operación deja de definirse por un par y pasa a guardar cuánto vale el trato: el monto que
entrega el cliente, su moneda, y los equivalentes con los que se compara y se contabiliza
(USDT siempre; BCV cuando el valor está en bolívares). Mismo patrón que `client_loans`.

`currency_pair_id`, `from_amount`, `to_amount` y `rate_used` se conservan: pasan a significar
la cotización que se le prometió al cliente.

Backfill: `amount` = `from_amount` y `currency` = moneda origen del par de la operación. Los
equivalentes los llena `app/cli/backfill_operation_values.py`, que necesita las tasas
históricas y no se puede hacer en SQL.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-07-24 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('whatsapp_operations', sa.Column('amount', sa.Float(), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('currency', sa.String(length=10), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('amount_usdt', sa.Float(), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('usdt_rate', sa.Float(), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('bcv_amount', sa.Float(), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('bcv_rate', sa.Float(), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('valuation_at', sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        UPDATE whatsapp_operations o
           SET amount = o.from_amount,
               currency = c.symbol
          FROM currency_pairs cp
          JOIN currencies c ON c.id = cp.from_currency_id
         WHERE cp.id = o.currency_pair_id
           AND o.amount IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('whatsapp_operations', 'valuation_at')
    op.drop_column('whatsapp_operations', 'bcv_rate')
    op.drop_column('whatsapp_operations', 'bcv_amount')
    op.drop_column('whatsapp_operations', 'usdt_rate')
    op.drop_column('whatsapp_operations', 'amount_usdt')
    op.drop_column('whatsapp_operations', 'currency')
    op.drop_column('whatsapp_operations', 'amount')
