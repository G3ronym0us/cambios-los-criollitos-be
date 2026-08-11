"""operation records the fund that paid

Revision ID: b7c8d9e0f1a2
Revises: 18e341e018c3
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "18e341e018c3"
branch_labels = None
depends_on = None


def upgrade():
    # Aditiva: `fund_group_id` sigue siendo la pata que entra y no se toca ninguna fila.
    op.add_column(
        "whatsapp_operations",
        sa.Column("fund_group_out_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_whatsapp_operations_fund_group_out_id",
        "whatsapp_operations",
        ["fund_group_out_id"],
    )
    op.create_foreign_key(
        "fk_whatsapp_operations_fund_group_out_id",
        "whatsapp_operations",
        "fund_groups",
        ["fund_group_out_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "fk_whatsapp_operations_fund_group_out_id", "whatsapp_operations", type_="foreignkey"
    )
    op.drop_index("ix_whatsapp_operations_fund_group_out_id", table_name="whatsapp_operations")
    op.drop_column("whatsapp_operations", "fund_group_out_id")
