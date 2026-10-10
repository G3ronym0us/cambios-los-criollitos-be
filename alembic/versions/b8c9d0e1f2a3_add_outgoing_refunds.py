"""add whatsapp_outgoing_refunds

Lo que el cliente devolvió de un pago saliente hecho de más (saliente 5917: 28.900 Bs por una
operación de 8.324,88; devolvió 20.500 con el entrante 636). Con una fila aquí el pago cuenta
por su neto y el entrante de la devolución queda con destino.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whatsapp_outgoing_refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "outgoing_payment_id",
            sa.Integer(),
            sa.ForeignKey("whatsapp_outgoing_payments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "incoming_payment_id",
            sa.Integer(),
            sa.ForeignKey("whatsapp_incoming_payments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_outgoing_refunds_id", "whatsapp_outgoing_refunds", ["id"])
    op.create_index(
        "ix_whatsapp_outgoing_refunds_uuid", "whatsapp_outgoing_refunds", ["uuid"], unique=True
    )
    op.create_index(
        "ix_whatsapp_outgoing_refunds_outgoing_payment_id",
        "whatsapp_outgoing_refunds",
        ["outgoing_payment_id"],
    )
    op.create_index(
        "ix_whatsapp_outgoing_refunds_incoming_payment_id",
        "whatsapp_outgoing_refunds",
        ["incoming_payment_id"],
        unique=True,
    )


def downgrade():
    op.drop_table("whatsapp_outgoing_refunds")
