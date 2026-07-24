"""add whatsapp_payment_allocations

Un pago entrante puede cubrir varias operaciones: el Zelle de 220 del 2026-07-23 pagó 200 de
un cambio a BRL y 20 de otro a VES. Con un solo FK en el pago, la segunda operación se quedaba
sin comprobante. Esta tabla dice qué parte del pago le toca a cada operación.

El FK `whatsapp_incoming_payments.whatsapp_operation_id` se conserva como "operación principal"
(la de la asignación mayor) para no tocar al bot ni al matcher.

Backfill: cada entrante ya vinculado estrena su asignación. El monto es el del pago, salvo que
la operación sea menor y ambas hablen la misma moneda de liquidación — ahí entra lo de la
operación y el resto queda visible como "sin asignar", que es justo el caso que motivó esto.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-07-24 01:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ZELLE y PAYPAL liquidan en USD: un pago ZELLE y una op con lado origen USD son la misma
# moneda a efectos del reparto.
_SETTLES_USD = "('USD', 'ZELLE', 'PAYPAL', 'USDT')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'whatsapp_payment_allocations',
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('incoming_payment_id', sa.Integer(), nullable=False),
        sa.Column('whatsapp_operation_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['incoming_payment_id'], ['whatsapp_incoming_payments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['whatsapp_operation_id'], ['whatsapp_operations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('incoming_payment_id', 'whatsapp_operation_id',
                            name='uq_allocation_payment_operation'),
    )
    op.create_index(op.f('ix_whatsapp_payment_allocations_uuid'),
                    'whatsapp_payment_allocations', ['uuid'], unique=True)
    op.create_index(op.f('ix_whatsapp_payment_allocations_id'),
                    'whatsapp_payment_allocations', ['id'])
    op.create_index(op.f('ix_whatsapp_payment_allocations_incoming_payment_id'),
                    'whatsapp_payment_allocations', ['incoming_payment_id'])
    op.create_index(op.f('ix_whatsapp_payment_allocations_whatsapp_operation_id'),
                    'whatsapp_payment_allocations', ['whatsapp_operation_id'])

    op.execute(
        f"""
        INSERT INTO whatsapp_payment_allocations
            (uuid, incoming_payment_id, whatsapp_operation_id, amount, created_at)
        SELECT gen_random_uuid()::text, i.id, o.id,
               CASE
                 WHEN o.from_amount < i.amount
                      AND (fc.symbol = i.currency
                           OR (fc.symbol IN {_SETTLES_USD} AND i.currency IN {_SETTLES_USD}))
                 THEN o.from_amount
                 ELSE i.amount
               END,
               i.created_at
          FROM whatsapp_incoming_payments i
          JOIN whatsapp_operations o ON o.id = i.whatsapp_operation_id
          JOIN currency_pairs cp ON cp.id = o.currency_pair_id
          LEFT JOIN currencies fc ON fc.id = cp.from_currency_id
         WHERE i.amount IS NOT NULL AND i.amount > 0
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_whatsapp_payment_allocations_whatsapp_operation_id'),
                  table_name='whatsapp_payment_allocations')
    op.drop_index(op.f('ix_whatsapp_payment_allocations_incoming_payment_id'),
                  table_name='whatsapp_payment_allocations')
    op.drop_index(op.f('ix_whatsapp_payment_allocations_id'),
                  table_name='whatsapp_payment_allocations')
    op.drop_index(op.f('ix_whatsapp_payment_allocations_uuid'),
                  table_name='whatsapp_payment_allocations')
    op.drop_table('whatsapp_payment_allocations')
