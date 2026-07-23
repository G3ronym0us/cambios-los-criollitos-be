"""deposits only via fund_pending_deposits

Un FundMovement DEPOSIT solo puede nacer de un `fund_pending_deposit` confirmado (comprobante
del grupo detectado por el bot, u alta manual del operador). Esta migración prepara la tabla
para ese rol único:

- `origin`: GROUP (bot) | MANUAL (operador desde /admin/funds).
- `created_by_user_id`: quién lo cargó a mano.
- `source_incoming_payment_id`: pago entrante que este comprobante estaría duplicando. El
  gestor a veces reenvía al grupo el Zelle de un cliente: ese dinero ya entró al fondo como
  pata USD del cambio, y confirmarlo como depósito lo contaría dos veces.

Además el arco exclusivo de `fund_movements` que marca el diagrama ER: un movimiento cuelga
de una transacción (EXCHANGE) o de un pago entrante (DEPOSIT), nunca de ambos. Se aplica como
"a lo sumo uno" y no como XOR estricto porque 744 filas legacy (547 DEPOSIT + 180 PERSONAL +
17 EXCHANGE migrados) no tienen ninguno de los dos y un XOR las rechazaría.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-23 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK_NAME = 'ck_fund_movements_source_arc'


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'fund_pending_deposits',
        sa.Column('origin', sa.String(length=20), nullable=False, server_default='GROUP'),
    )
    op.add_column(
        'fund_pending_deposits',
        sa.Column('source_incoming_payment_id', sa.Integer(), nullable=True),
    )
    op.add_column(
        'fund_pending_deposits',
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_fund_pending_deposits_source_incoming_payment',
        'fund_pending_deposits', 'whatsapp_incoming_payments',
        ['source_incoming_payment_id'], ['id'], ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_fund_pending_deposits_created_by_user',
        'fund_pending_deposits', 'users',
        ['created_by_user_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(
        op.f('ix_fund_pending_deposits_source_incoming_payment_id'),
        'fund_pending_deposits', ['source_incoming_payment_id'],
    )

    op.create_check_constraint(
        CHECK_NAME,
        'fund_movements',
        'transaction_id IS NULL OR incoming_payment_id IS NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(CHECK_NAME, 'fund_movements', type_='check')

    op.drop_index(
        op.f('ix_fund_pending_deposits_source_incoming_payment_id'),
        table_name='fund_pending_deposits',
    )
    op.drop_constraint(
        'fk_fund_pending_deposits_created_by_user', 'fund_pending_deposits', type_='foreignkey'
    )
    op.drop_constraint(
        'fk_fund_pending_deposits_source_incoming_payment', 'fund_pending_deposits', type_='foreignkey'
    )
    op.drop_column('fund_pending_deposits', 'created_by_user_id')
    op.drop_column('fund_pending_deposits', 'source_incoming_payment_id')
    op.drop_column('fund_pending_deposits', 'origin')
