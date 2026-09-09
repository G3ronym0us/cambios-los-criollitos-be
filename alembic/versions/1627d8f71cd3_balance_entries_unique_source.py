"""Índices únicos parciales en whatsapp_balance_entries: un pago entrante no puede
acreditarse dos veces, una operación no puede debitarse dos veces.

`WhatsAppBalanceService` ya comprobaba "no existe todavía" antes de insertar, pero eso es
lectura-luego-escritura sin lock: dos requests concurrentes (un reintento del bot, un doble
click) pueden pasar la comprobación los dos y dejar la misma plata contada dos veces. Estos
índices hacen que la segunda escritura choque en la base, no solo en la aplicación.

No filtran por entry_type (Postgres no deja usar el cast del enum a texto en un índice
parcial — su función de salida no es IMMUTABLE). No hace falta: por construcción del
servicio, `incoming_payment_id` sólo se llena en un CREDIT y `whatsapp_operation_id` sólo en
un DEBIT.

Revision ID: 1627d8f71cd3
Revises: c4d5e6f7a8b9
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1627d8f71cd3'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'uq_balance_credit_per_incoming_payment',
        'whatsapp_balance_entries',
        ['incoming_payment_id'],
        unique=True,
        postgresql_where=sa.text('incoming_payment_id IS NOT NULL'),
    )
    op.create_index(
        'uq_balance_debit_per_operation',
        'whatsapp_balance_entries',
        ['whatsapp_operation_id'],
        unique=True,
        postgresql_where=sa.text('whatsapp_operation_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_balance_debit_per_operation', table_name='whatsapp_balance_entries')
    op.drop_index('uq_balance_credit_per_incoming_payment', table_name='whatsapp_balance_entries')
