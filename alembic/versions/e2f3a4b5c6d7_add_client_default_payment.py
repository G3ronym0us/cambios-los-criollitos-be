"""add client default payment

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("whatsapp_clients", sa.Column("default_payment_info", sa.Text(), nullable=True))
    op.add_column("whatsapp_clients", sa.Column("default_payment_currency", sa.String(length=10), nullable=True))


def downgrade():
    op.drop_column("whatsapp_clients", "default_payment_currency")
    op.drop_column("whatsapp_clients", "default_payment_info")
