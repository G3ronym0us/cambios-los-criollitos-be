"""remove_source_column_from_exchange_rates

Revision ID: a6dc585032e0
Revises: ab8e0de01af3
Create Date: 2025-10-19 23:54:15.596793

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6dc585032e0'
down_revision: Union[str, None] = 'ab8e0de01af3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Eliminar la columna source de exchange_rates
    op.drop_column('exchange_rates', 'source')


def downgrade() -> None:
    """Downgrade schema."""
    # Restaurar la columna source (por si necesitamos hacer rollback)
    op.add_column('exchange_rates', sa.Column('source', sa.String(length=50), nullable=True))

    # Actualizar valores existentes con un valor por defecto
    op.execute("UPDATE exchange_rates SET source = 'unknown' WHERE source IS NULL")

    # Hacer la columna NOT NULL después de llenar valores
    op.alter_column('exchange_rates', 'source', nullable=False)
