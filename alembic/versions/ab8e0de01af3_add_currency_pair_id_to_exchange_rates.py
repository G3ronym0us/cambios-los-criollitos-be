"""add_currency_pair_id_to_exchange_rates

Revision ID: ab8e0de01af3
Revises: h5i6j7k8l9m0
Create Date: 2025-10-19 16:40:26.785166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab8e0de01af3'
down_revision: Union[str, None] = 'h5i6j7k8l9m0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Agregar columna currency_pair_id (nullable por ahora)
    op.add_column('exchange_rates', sa.Column('currency_pair_id', sa.Integer(), nullable=True))

    # 2. Agregar foreign key constraint
    op.create_foreign_key(
        'fk_exchange_rates_currency_pair_id',
        'exchange_rates', 'currency_pairs',
        ['currency_pair_id'], ['id'],
        ondelete='CASCADE'
    )

    # 3. Migrar datos: vincular exchange_rates existentes con currency_pairs
    # Este paso requiere un script de migración de datos
    op.execute("""
        UPDATE exchange_rates er
        SET currency_pair_id = (
            SELECT cp.id
            FROM currency_pairs cp
            JOIN currencies c_from ON cp.from_currency_id = c_from.id
            JOIN currencies c_to ON cp.to_currency_id = c_to.id
            WHERE c_from.symbol = er.from_currency
            AND c_to.symbol = er.to_currency
            LIMIT 1
        )
        WHERE er.currency_pair_id IS NULL
    """)

    # 4. Hacer currency_pair_id NOT NULL después de migrar datos
    op.alter_column('exchange_rates', 'currency_pair_id', nullable=False)

    # 5. Crear índice para currency_pair_id
    op.create_index('idx_exchange_rates_currency_pair_id', 'exchange_rates', ['currency_pair_id'])

    # 6. Crear índice único para asegurar solo un registro activo por par
    # Solo puede haber un registro con is_active=True por currency_pair_id
    op.create_index(
        'idx_exchange_rates_unique_active_pair',
        'exchange_rates',
        ['currency_pair_id'],
        unique=True,
        postgresql_where=sa.text('is_active = true')
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Eliminar índices
    op.drop_index('idx_exchange_rates_unique_active_pair', 'exchange_rates')
    op.drop_index('idx_exchange_rates_currency_pair_id', 'exchange_rates')

    # Eliminar foreign key
    op.drop_constraint('fk_exchange_rates_currency_pair_id', 'exchange_rates', type_='foreignkey')

    # Eliminar columna
    op.drop_column('exchange_rates', 'currency_pair_id')
