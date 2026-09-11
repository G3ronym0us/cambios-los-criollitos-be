"""add client pending deliveries

Audit trail for marking a client's uncovered operations as delivered in cash, in one
atomic batch, with the ability to undo it later.

The money itself keeps living in `whatsapp_operations.uncovered_amount`, exactly as when
it is declared from the coverage panel. These tables only record who marked what, when,
and — crucially — what the value was before, which is the only thing that makes a real
undo possible: `uncovered_amount` is the whole gap, not an increment, so undoing cannot be
"subtract what was delivered" nor "set to zero".

Revision ID: c7d8e9fa0b1c
Revises: 8b7d53613e53
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c7d8e9fa0b1c"
down_revision: Union[str, None] = "8b7d53613e53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_pending_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["whatsapp_clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["undone_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_pending_deliveries_id"), "whatsapp_pending_deliveries", ["id"]
    )
    op.create_index(
        op.f("ix_whatsapp_pending_deliveries_uuid"),
        "whatsapp_pending_deliveries",
        ["uuid"],
        unique=True,
    )
    op.create_index(
        op.f("ix_whatsapp_pending_deliveries_client_id"),
        "whatsapp_pending_deliveries",
        ["client_id"],
    )
    op.create_index(
        op.f("ix_whatsapp_pending_deliveries_created_at"),
        "whatsapp_pending_deliveries",
        ["created_at"],
    )

    op.create_table(
        "whatsapp_pending_delivery_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_operation_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("previous_uncovered", sa.Float(), nullable=True),
        sa.Column("previous_uncovered_reason", sa.String(length=24), nullable=True),
        sa.ForeignKeyConstraint(
            ["delivery_id"], ["whatsapp_pending_deliveries.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["whatsapp_operation_id"], ["whatsapp_operations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_pending_delivery_items_id"), "whatsapp_pending_delivery_items", ["id"]
    )
    op.create_index(
        op.f("ix_whatsapp_pending_delivery_items_uuid"),
        "whatsapp_pending_delivery_items",
        ["uuid"],
        unique=True,
    )
    op.create_index(
        op.f("ix_whatsapp_pending_delivery_items_delivery_id"),
        "whatsapp_pending_delivery_items",
        ["delivery_id"],
    )
    op.create_index(
        op.f("ix_whatsapp_pending_delivery_items_operation_id"),
        "whatsapp_pending_delivery_items",
        ["whatsapp_operation_id"],
    )


def downgrade() -> None:
    op.drop_table("whatsapp_pending_delivery_items")
    op.drop_table("whatsapp_pending_deliveries")
