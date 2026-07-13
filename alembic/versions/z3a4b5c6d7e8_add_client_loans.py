"""add client loans with fiat, USDT and BCV values

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "z3a4b5c6d7e8"
down_revision = "y2z3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    preferred_enum = sa.Enum("FIAT", "USDT", "BCV", name="clientloanpreferredvalue")
    status_enum = sa.Enum("OPEN", "PARTIAL", "PAID", "CANCELLED", name="clientloanstatus")

    op.create_table(
        "client_loans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("outgoing_payment_id", sa.Integer(), nullable=False),
        sa.Column("fiat_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("fiat_currency", sa.String(10), nullable=False),
        sa.Column("usdt_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("usdt_rate", sa.Numeric(24, 8), nullable=False),
        sa.Column("bcv_amount", sa.Numeric(24, 8), nullable=True),
        sa.Column("bcv_rate", sa.Numeric(24, 8), nullable=True),
        sa.Column("preferred_value", preferred_enum, nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="OPEN"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["whatsapp_clients.id"]),
        sa.ForeignKeyConstraint(["outgoing_payment_id"], ["whatsapp_outgoing_payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outgoing_payment_id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_client_loans_uuid", "client_loans", ["uuid"])
    op.create_index("ix_client_loans_client", "client_loans", ["client_id"])
    op.create_index("ix_client_loans_outgoing", "client_loans", ["outgoing_payment_id"])

    op.create_table(
        "client_loan_repayments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("preferred_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("fiat_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("fiat_currency", sa.String(10), nullable=False),
        sa.Column("usdt_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("usdt_rate", sa.Numeric(24, 8), nullable=False),
        sa.Column("bcv_amount", sa.Numeric(24, 8), nullable=True),
        sa.Column("bcv_rate", sa.Numeric(24, 8), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["loan_id"], ["client_loans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_client_loan_repayments_uuid", "client_loan_repayments", ["uuid"])
    op.create_index("ix_client_loan_repayments_loan", "client_loan_repayments", ["loan_id"])


def downgrade():
    op.drop_index("ix_client_loan_repayments_loan", table_name="client_loan_repayments")
    op.drop_index("ix_client_loan_repayments_uuid", table_name="client_loan_repayments")
    op.drop_table("client_loan_repayments")
    op.drop_index("ix_client_loans_outgoing", table_name="client_loans")
    op.drop_index("ix_client_loans_client", table_name="client_loans")
    op.drop_index("ix_client_loans_uuid", table_name="client_loans")
    op.drop_table("client_loans")
    sa.Enum(name="clientloanstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="clientloanpreferredvalue").drop(op.get_bind(), checkfirst=True)
