"""reparto de un comprobante saliente entre varias operaciones

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade():
    # Espejo de `whatsapp_payment_allocations`, que hace lo mismo del lado entrante. El
    # arrastre convierte cada vínculo actual en su fila equivalente, así que después de esto
    # `delivered_amount` puede leer SOLO de aquí sin perder nada de lo ya cargado.
    op.create_table(
        "whatsapp_outgoing_settlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outgoing_payment_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_operation_id", sa.Integer(), nullable=False),
        sa.Column("settled_amount", sa.Float(), nullable=False),
        sa.Column("settled_reference_rate", sa.Float(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["outgoing_payment_id"], ["whatsapp_outgoing_payments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["whatsapp_operation_id"], ["whatsapp_operations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outgoing_payment_id", "whatsapp_operation_id", name="uq_settlement_payment_operation"
        ),
    )
    op.create_index(
        op.f("ix_whatsapp_outgoing_settlements_id"),
        "whatsapp_outgoing_settlements", ["id"],
    )
    op.create_index(
        op.f("ix_whatsapp_outgoing_settlements_outgoing_payment_id"),
        "whatsapp_outgoing_settlements", ["outgoing_payment_id"],
    )
    op.create_index(
        op.f("ix_whatsapp_outgoing_settlements_whatsapp_operation_id"),
        "whatsapp_outgoing_settlements", ["whatsapp_operation_id"],
    )
    op.create_index(
        op.f("ix_whatsapp_outgoing_settlements_uuid"),
        "whatsapp_outgoing_settlements", ["uuid"], unique=True,
    )

    # Arrastre: cada saliente ya vinculado y con cobertura declarada pasa a ser una fila.
    # Los que están vinculados pero sin `settled_amount` (nunca se dijo cuánto cubren) se
    # quedan fuera a propósito: inventarles un monto aquí sería decidir por el operador.
    op.execute(
        """
        INSERT INTO whatsapp_outgoing_settlements (
            uuid, outgoing_payment_id, whatsapp_operation_id,
            settled_amount, settled_reference_rate, created_at
        )
        SELECT gen_random_uuid(), p.id, p.whatsapp_operation_id,
               p.settled_amount, p.settled_reference_rate, p.created_at
        FROM whatsapp_outgoing_payments p
        WHERE p.whatsapp_operation_id IS NOT NULL
          AND p.settled_amount IS NOT NULL
        """
    )


def downgrade():
    op.drop_index(
        op.f("ix_whatsapp_outgoing_settlements_uuid"), table_name="whatsapp_outgoing_settlements"
    )
    op.drop_index(
        op.f("ix_whatsapp_outgoing_settlements_whatsapp_operation_id"),
        table_name="whatsapp_outgoing_settlements",
    )
    op.drop_index(
        op.f("ix_whatsapp_outgoing_settlements_outgoing_payment_id"),
        table_name="whatsapp_outgoing_settlements",
    )
    op.drop_index(
        op.f("ix_whatsapp_outgoing_settlements_id"), table_name="whatsapp_outgoing_settlements"
    )
    op.drop_table("whatsapp_outgoing_settlements")
