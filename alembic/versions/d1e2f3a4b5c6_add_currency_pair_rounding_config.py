"""add currency pair rounding config

Revision ID: d1e2f3a4b5c6
Revises: a4b5c6d7e8f9
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("currency_pairs", sa.Column("rounding_mode", sa.String(length=6), nullable=True))
    op.add_column("currency_pairs", sa.Column("rounding_step", sa.Numeric(15, 4), nullable=True))
    op.add_column("currency_pairs", sa.Column("rounding_direction", sa.String(length=4), nullable=True))
    op.add_column("currency_pairs", sa.Column("rounding_amount_side", sa.String(length=4), nullable=True))


def downgrade():
    op.drop_column("currency_pairs", "rounding_amount_side")
    op.drop_column("currency_pairs", "rounding_direction")
    op.drop_column("currency_pairs", "rounding_step")
    op.drop_column("currency_pairs", "rounding_mode")
