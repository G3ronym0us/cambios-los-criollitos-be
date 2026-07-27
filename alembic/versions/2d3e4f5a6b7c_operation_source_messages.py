"""de qué mensaje de WhatsApp nació una operación

El mapa mensaje→operación vivía en el SQLite del bot (`quote_message_map`) y de él dependen
tres comportamientos: informar el revoke de un mensaje, decidir si una corrección reemplaza a
una cotización anterior, y etiquetar como VIA_PARTNER la op que creó ESE mensaje y no «la
activa del teléfono». Los dos últimos son decisiones, así que el mapa tiene que estar donde
se decide.

Tabla y no columna en `whatsapp_operations`: el dato no describe el trato sino de dónde vino,
tiene su propia vida (deja de servir cuando el mensaje ya no se puede borrar ni citar) y una
operación puede acabar con más de un mensaje detrás.

Revision ID: 2d3e4f5a6b7c
Revises: 1c2d3e4f5a6b
Create Date: 2026-07-27 07:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2d3e4f5a6b7c'
down_revision: Union[str, None] = '1c2d3e4f5a6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'whatsapp_operation_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('wa_message_id', sa.String(length=255), nullable=False),
        sa.Column('whatsapp_operation_id', sa.Integer(), nullable=False),
        sa.Column('client_phone', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['whatsapp_operation_id'], ['whatsapp_operations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_whatsapp_operation_messages_id'), 'whatsapp_operation_messages', ['id'])
    op.create_index(op.f('ix_whatsapp_operation_messages_uuid'), 'whatsapp_operation_messages', ['uuid'], unique=True)
    # Único: un mensaje origina una sola operación; reenganchar el mismo id reapunta la fila.
    op.create_index(
        op.f('ix_whatsapp_operation_messages_wa_message_id'),
        'whatsapp_operation_messages', ['wa_message_id'], unique=True,
    )
    op.create_index(
        op.f('ix_whatsapp_operation_messages_whatsapp_operation_id'),
        'whatsapp_operation_messages', ['whatsapp_operation_id'],
    )
    op.create_index(
        op.f('ix_whatsapp_operation_messages_client_phone'),
        'whatsapp_operation_messages', ['client_phone'],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_whatsapp_operation_messages_client_phone'), table_name='whatsapp_operation_messages')
    op.drop_index(op.f('ix_whatsapp_operation_messages_whatsapp_operation_id'), table_name='whatsapp_operation_messages')
    op.drop_index(op.f('ix_whatsapp_operation_messages_wa_message_id'), table_name='whatsapp_operation_messages')
    op.drop_index(op.f('ix_whatsapp_operation_messages_uuid'), table_name='whatsapp_operation_messages')
    op.drop_index(op.f('ix_whatsapp_operation_messages_id'), table_name='whatsapp_operation_messages')
    op.drop_table('whatsapp_operation_messages')
