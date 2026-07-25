"""un movimiento de fondo se reversa, no se borra

Borrar un movimiento era un DELETE físico: la fila desaparecía y con ella quién la había
registrado, cuándo y por qué. Como el saldo se calcula sumando el historial, un borrado
reescribía en silencio el saldo de todos los movimientos posteriores y no quedaba forma de
reconstruir qué había pasado.

Ahora se anula con otro movimiento que lo referencia y lo deja visible:

- `reverses_movement_id`: en la reversa, a qué movimiento anula. La reversa conserva el TIPO
  del original (anular un DEPOSIT crea un DEPOSIT que resta), así el signo se lee de esta
  columna sin mirar la otra fila.
- `reversed_by_movement_id` / `reversed_at`: la cara opuesta del vínculo en el original, para
  marcarlo en las listas y excluirlo de las sumas sin un subquery por fila.

Revision ID: 1c2d3e4f5a6b
Revises: 0b1c2d3e4f5a
Create Date: 2026-07-25 03:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '1c2d3e4f5a6b'
down_revision: Union[str, None] = '0b1c2d3e4f5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('fund_movements', sa.Column('reverses_movement_id', sa.Integer(), nullable=True))
    op.add_column('fund_movements', sa.Column('reversed_by_movement_id', sa.Integer(), nullable=True))
    op.add_column('fund_movements', sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True))

    # Un movimiento se reversa una sola vez.
    op.create_unique_constraint(
        'uq_fund_movements_reverses', 'fund_movements', ['reverses_movement_id']
    )
    # RESTRICT: borrar el original dejaría la reversa apuntando al vacío.
    op.create_foreign_key(
        'fk_fund_movements_reverses', 'fund_movements', 'fund_movements',
        ['reverses_movement_id'], ['id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_fund_movements_reversed_by', 'fund_movements', 'fund_movements',
        ['reversed_by_movement_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(
        'ix_fund_movements_reversed_by', 'fund_movements', ['reversed_by_movement_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_fund_movements_reversed_by', table_name='fund_movements')
    op.drop_constraint('fk_fund_movements_reversed_by', 'fund_movements', type_='foreignkey')
    op.drop_constraint('fk_fund_movements_reverses', 'fund_movements', type_='foreignkey')
    op.drop_constraint('uq_fund_movements_reverses', 'fund_movements', type_='unique')
    op.drop_column('fund_movements', 'reversed_at')
    op.drop_column('fund_movements', 'reversed_by_movement_id')
    op.drop_column('fund_movements', 'reverses_movement_id')
