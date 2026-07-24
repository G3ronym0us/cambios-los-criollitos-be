"""transaction can stand on its own value (nullable pair + explicit currencies)

Una operación deja de tener un par único, así que su transacción tampoco. Ahora la transacción
representa el VALOR del trato → su equivalente USDT (220 ZELLE → 220 USDT), con la ganancia
calculada sobre ese valor. Para eso necesita nombrar sus monedas sin depender de un par.

- `currency_pair_id` pasa a nullable.
- `from_currency` / `to_currency` se guardan como texto; las operaciones viejas se rellenan
  desde su par y la propiedad del modelo sigue cayendo al par cuando no hay símbolo guardado.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-07-24 04:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('transactions', sa.Column('from_currency', sa.String(length=10), nullable=True))
    op.add_column('transactions', sa.Column('to_currency', sa.String(length=10), nullable=True))
    op.alter_column('transactions', 'currency_pair_id', existing_type=sa.Integer(), nullable=True)

    op.execute(
        """
        UPDATE transactions t
           SET from_currency = fc.symbol,
               to_currency = tc.symbol
          FROM currency_pairs cp
          JOIN currencies fc ON fc.id = cp.from_currency_id
          JOIN currencies tc ON tc.id = cp.to_currency_id
         WHERE cp.id = t.currency_pair_id
           AND t.from_currency IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Solo se puede volver a NOT NULL si no quedaron transacciones sin par.
    op.alter_column('transactions', 'currency_pair_id', existing_type=sa.Integer(), nullable=False)
    op.drop_column('transactions', 'to_currency')
    op.drop_column('transactions', 'from_currency')
