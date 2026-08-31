"""add currency pair negotiation step

Nothing applies this step automatically — unlike `rounding_step`, which the bot
applies to every quote. It only feeds the round-amount suggestions when an
operator creates a quote by hand, so the two are independent and usually differ.

Revision ID: f7a8b9c0d1e2
Revises: e2135891b4b8
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e2"
down_revision = "e2135891b4b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("currency_pairs", sa.Column("negotiation_step", sa.Numeric(15, 4), nullable=True))
    op.add_column("currency_pairs", sa.Column("negotiation_step_side", sa.String(length=4), nullable=True))


def downgrade():
    op.drop_column("currency_pairs", "negotiation_step_side")
    op.drop_column("currency_pairs", "negotiation_step")
