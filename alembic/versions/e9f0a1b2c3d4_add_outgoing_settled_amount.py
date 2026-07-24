"""add whatsapp_outgoing_payments.settled_amount

Cuánto del valor de la operación cubre cada comprobante de salida, en la moneda del valor: el
Pix de 914,04 BRL cubre 200 de un trato de 220 ZELLE, y el pago móvil de 15.658,4 VES cubre los
20 restantes. Con eso la operación sabe cuánto lleva entregado y cuánto le falta, sin depender
de que todo se pague en una sola moneda.

`settled_reference_rate` guarda la tasa contra la que se comparó al vincular (la cotizada, o la
activa del par). Así la diferencia entre lo que se pagó y lo que tocaba sigue siendo auditable
cuando la tasa del par cambie.

Backfill: al comprobante más antiguo de cada operación se le asigna el valor completo; los
demás quedan en NULL —en producción son duplicados y correcciones, no pagos distintos— para
que el operador diga cuánto cubren.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-07-24 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('whatsapp_outgoing_payments', sa.Column('settled_amount', sa.Float(), nullable=True))
    op.add_column(
        'whatsapp_outgoing_payments',
        sa.Column('settled_reference_rate', sa.Float(), nullable=True),
    )

    op.execute(
        """
        WITH primero AS (
            SELECT DISTINCT ON (p.whatsapp_operation_id) p.id, o.amount
              FROM whatsapp_outgoing_payments p
              JOIN whatsapp_operations o ON o.id = p.whatsapp_operation_id
             WHERE p.whatsapp_operation_id IS NOT NULL
               AND o.amount IS NOT NULL
             ORDER BY p.whatsapp_operation_id, p.created_at, p.id
        )
        UPDATE whatsapp_outgoing_payments p
           SET settled_amount = primero.amount
          FROM primero
         WHERE p.id = primero.id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('whatsapp_outgoing_payments', 'settled_reference_rate')
    op.drop_column('whatsapp_outgoing_payments', 'settled_amount')
