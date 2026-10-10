"""undo a single operation of a pending-delivery batch

Un lote de «entregas marcadas» se deshacía entero. El de Neurys del 2026-10-09 marcó 38
operaciones y sólo 10 estaban mal: cada ítem gana su propio `undone_at`.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-10
"""

import sqlalchemy as sa

from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "whatsapp_pending_delivery_items",
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "whatsapp_pending_delivery_items",
        sa.Column(
            "undone_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Los lotes ya deshechos enteros: sus ítems quedan deshechos con ellos.
    op.execute(
        """
        UPDATE whatsapp_pending_delivery_items i
           SET undone_at = d.undone_at, undone_by_user_id = d.undone_by_user_id
          FROM whatsapp_pending_deliveries d
         WHERE d.id = i.delivery_id AND d.undone_at IS NOT NULL
        """
    )


def downgrade():
    op.drop_column("whatsapp_pending_delivery_items", "undone_by_user_id")
    op.drop_column("whatsapp_pending_delivery_items", "undone_at")
