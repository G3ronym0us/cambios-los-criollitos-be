"""drop whatsapp_incoming_payments.fund_group_id (redundante)

Era el único enlace directo pago→fondo del modelo (el saliente nunca lo tuvo): el fondo de un
pago debe venir por su OPERACIÓN. La columna se deriva ahora en `WhatsAppIncomingPayment.
fund_group` (op → fondo; si el pago aún no tiene op, el grupo del JID en `client_phone`).

Antes de borrarla se sube el dato a la operación: las ops de esos entrantes que hayan quedado
sin fondo heredan el del pago. En prod (2026-07-23) hay 33 filas con fondo; 2 no tienen
operación (#124 y #86) — la #86 se sigue resolviendo por su `client_phone` @g.us y la #124
queda sin fondo hasta que se le vincule una op.

Revision ID: a5b6c7d8e9f0
Revises: f3a4b5c6d7e8
Create Date: 2026-07-23 18:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        UPDATE whatsapp_operations o
           SET fund_group_id = i.fund_group_id
          FROM whatsapp_incoming_payments i
         WHERE i.whatsapp_operation_id = o.id
           AND i.fund_group_id IS NOT NULL
           AND o.fund_group_id IS NULL
        """
    )
    op.drop_index('ix_whatsapp_incoming_payments_fund_group_id', table_name='whatsapp_incoming_payments')
    op.drop_column('whatsapp_incoming_payments', 'fund_group_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'whatsapp_incoming_payments',
        sa.Column('fund_group_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_whatsapp_incoming_payments_fund_group',
        'whatsapp_incoming_payments', 'fund_groups',
        ['fund_group_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(
        'ix_whatsapp_incoming_payments_fund_group_id',
        'whatsapp_incoming_payments', ['fund_group_id'],
    )
    # Reconstrucción best-effort: el fondo vuelve desde la operación del pago.
    op.execute(
        """
        UPDATE whatsapp_incoming_payments i
           SET fund_group_id = o.fund_group_id
          FROM whatsapp_operations o
         WHERE i.whatsapp_operation_id = o.id
           AND o.fund_group_id IS NOT NULL
        """
    )
