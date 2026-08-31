"""transfer a payment to another client, keeping the trail

Adds `owner_client_id` to both payment tables (an override of who the receipt belongs to;
`client_phone` is never touched, so the origin stays searchable) plus the
`whatsapp_payment_transfers` log that records every move.

Revision ID: 8b7d53613e53
Revises: f7a8b9c0d1e2
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "8b7d53613e53"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("whatsapp_incoming_payments", "whatsapp_outgoing_payments"):
        op.add_column(table, sa.Column("owner_client_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_owner_client",
            table,
            "whatsapp_clients",
            ["owner_client_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_owner_client", table, ["owner_client_id"])

    reason_enum = sa.Enum(
        "THIRD_PARTY", "BOT_MISMATCH", "DUPLICATE_CLIENT", name="paymenttransferreason"
    )

    op.create_table(
        "whatsapp_payment_transfers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("incoming_payment_id", sa.Integer(), nullable=True),
        sa.Column("outgoing_payment_id", sa.Integer(), nullable=True),
        sa.Column("from_client_id", sa.Integer(), nullable=True),
        sa.Column("from_client_phone", sa.String(64), nullable=True),
        sa.Column("from_client_name", sa.String(120), nullable=True),
        sa.Column("to_client_id", sa.Integer(), nullable=False),
        sa.Column("reason", reason_enum, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("unlinked_operation_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["incoming_payment_id"], ["whatsapp_incoming_payments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["outgoing_payment_id"], ["whatsapp_outgoing_payments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["from_client_id"], ["whatsapp_clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["to_client_id"], ["whatsapp_clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["unlinked_operation_id"], ["whatsapp_operations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        # Exactly one side: a transfer belongs either to an incoming or to an outgoing receipt.
        sa.CheckConstraint(
            "(incoming_payment_id IS NULL) <> (outgoing_payment_id IS NULL)",
            name="ck_payment_transfer_one_side",
        ),
    )
    op.create_index("ix_payment_transfers_uuid", "whatsapp_payment_transfers", ["uuid"])
    op.create_index(
        "ix_payment_transfers_incoming", "whatsapp_payment_transfers", ["incoming_payment_id"]
    )
    op.create_index(
        "ix_payment_transfers_outgoing", "whatsapp_payment_transfers", ["outgoing_payment_id"]
    )
    op.create_index("ix_payment_transfers_from", "whatsapp_payment_transfers", ["from_client_id"])
    op.create_index("ix_payment_transfers_to", "whatsapp_payment_transfers", ["to_client_id"])


def downgrade():
    op.drop_table("whatsapp_payment_transfers")
    sa.Enum(name="paymenttransferreason").drop(op.get_bind(), checkfirst=True)

    for table in ("whatsapp_incoming_payments", "whatsapp_outgoing_payments"):
        op.drop_index(f"ix_{table}_owner_client", table_name=table)
        op.drop_constraint(f"fk_{table}_owner_client", table, type_="foreignkey")
        op.drop_column(table, "owner_client_id")
