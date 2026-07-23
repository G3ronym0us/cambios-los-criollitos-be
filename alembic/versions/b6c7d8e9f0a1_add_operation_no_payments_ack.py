"""add whatsapp_operations no-payments acknowledgement

Desvincular el último comprobante de una operación la deja sin nada que la respalde — y si
estaba COMPLETED ya no se puede mover de estado. A partir de ahora esa salida es una decisión
explícita del operador: o se borra la operación con su transacción, o se conserva y queda
firmada aquí (quién la aceptó, cuándo y por qué).

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-07-23 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'whatsapp_operations',
        sa.Column('no_payments_ack_by_user_id', sa.Integer(), nullable=True),
    )
    op.add_column(
        'whatsapp_operations',
        sa.Column('no_payments_ack_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'whatsapp_operations',
        sa.Column('no_payments_ack_note', sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        'fk_whatsapp_operations_no_payments_ack_by',
        'whatsapp_operations', 'users',
        ['no_payments_ack_by_user_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'fk_whatsapp_operations_no_payments_ack_by', 'whatsapp_operations', type_='foreignkey'
    )
    op.drop_column('whatsapp_operations', 'no_payments_ack_note')
    op.drop_column('whatsapp_operations', 'no_payments_ack_at')
    op.drop_column('whatsapp_operations', 'no_payments_ack_by_user_id')
