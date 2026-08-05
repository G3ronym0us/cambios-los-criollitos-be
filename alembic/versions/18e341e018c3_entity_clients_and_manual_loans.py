"""entity clients and manual loans

Revision ID: 18e341e018c3
Revises: 31ac3d9074b1
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "18e341e018c3"
down_revision = "31ac3d9074b1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("whatsapp_clients", sa.Column("linked_group_jid", sa.String(64), nullable=True))
    op.create_index(
        "ix_whatsapp_clients_linked_group_jid",
        "whatsapp_clients",
        ["linked_group_jid"],
        unique=True,
    )
    # Los préstamos dados de alta a mano no tienen comprobante. El UNIQUE se conserva:
    # en Postgres los NULL no chocan entre sí, así que sigue impidiendo dos préstamos
    # sobre el mismo pago saliente.
    op.alter_column("client_loans", "outgoing_payment_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    op.execute("DELETE FROM client_loans WHERE outgoing_payment_id IS NULL")
    op.alter_column("client_loans", "outgoing_payment_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_whatsapp_clients_linked_group_jid", table_name="whatsapp_clients")
    op.drop_column("whatsapp_clients", "linked_group_jid")
