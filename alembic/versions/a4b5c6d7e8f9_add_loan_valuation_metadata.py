"""add client loan valuation metadata

Revision ID: a4b5c6d7e8f9
Revises: z3a4b5c6d7e8
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa


revision = "a4b5c6d7e8f9"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("client_loans", sa.Column("valuation_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "client_loans",
        sa.Column("manual_values", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE client_loans AS loan
        SET valuation_at = COALESCE(payment.created_at, loan.created_at)
        FROM whatsapp_outgoing_payments AS payment
        WHERE payment.id = loan.outgoing_payment_id
        """
    )
    op.execute("UPDATE client_loans SET valuation_at = created_at WHERE valuation_at IS NULL")
    op.alter_column("client_loans", "valuation_at", nullable=False)


def downgrade():
    op.drop_column("client_loans", "manual_values")
    op.drop_column("client_loans", "valuation_at")
