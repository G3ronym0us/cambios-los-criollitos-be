"""add bank email notifications and verifications

Revision ID: 31ac3d9074b1
Revises: 3e4f5a6b7c8d
Create Date: 2026-08-04

"""

from alembic import op
import sqlalchemy as sa


revision = "31ac3d9074b1"
down_revision = "3e4f5a6b7c8d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bank_email_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("mailbox_label", sa.String(length=60), nullable=False),
        sa.Column("mailbox_email", sa.String(length=255), nullable=False),
        sa.Column("bank", sa.String(length=40), nullable=False),
        sa.Column("sender_name", sa.String(length=200), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="USD"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("auth_result", sa.Text(), nullable=True),
        sa.Column("consumed_by_payment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["consumed_by_payment_id"], ["whatsapp_incoming_payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bank_email_notifications_uuid", "bank_email_notifications", ["uuid"], unique=True)
    op.create_index("ix_bank_email_notifications_message_id", "bank_email_notifications", ["message_id"], unique=True)
    op.create_index("ix_bank_email_notifications_amount", "bank_email_notifications", ["amount"])
    op.create_index("ix_bank_email_notifications_received_at", "bank_email_notifications", ["received_at"])
    op.create_index(
        "ix_bank_email_notifications_consumed_by_payment_id",
        "bank_email_notifications", ["consumed_by_payment_id"],
    )

    op.create_table(
        "bank_email_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incoming_payment_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "CONFIRMED", "NOT_FOUND", name="bank_email_verification_status"),
            nullable=False,
        ),
        sa.Column("matched_notification_id", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalation_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_notify_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["incoming_payment_id"], ["whatsapp_incoming_payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_notification_id"], ["bank_email_notifications.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incoming_payment_id", name="uq_bank_email_verification_payment"),
    )
    op.create_index("ix_bank_email_verifications_uuid", "bank_email_verifications", ["uuid"], unique=True)
    op.create_index("ix_bank_email_verifications_status", "bank_email_verifications", ["status"])
    op.create_index("ix_bank_email_verifications_next_notify_at", "bank_email_verifications", ["next_notify_at"])


def downgrade():
    op.drop_table("bank_email_verifications")
    op.execute("DROP TYPE IF EXISTS bank_email_verification_status")
    op.drop_table("bank_email_notifications")
