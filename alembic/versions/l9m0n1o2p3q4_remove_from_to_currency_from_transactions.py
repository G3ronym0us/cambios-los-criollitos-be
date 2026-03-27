"""remove from_to_currency from transactions

Revision ID: l9m0n1o2p3q4
Revises: k8l9m0n1o2p3
Create Date: 2026-03-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'l9m0n1o2p3q4'
down_revision = 'k8l9m0n1o2p3'
branch_labels = None
depends_on = None


def upgrade():
    # Make currency_pair_id NOT NULL (all rows already have it set)
    op.alter_column('transactions', 'currency_pair_id', nullable=False)
    # Drop redundant denormalized columns
    op.drop_column('transactions', 'from_currency')
    op.drop_column('transactions', 'to_currency')


def downgrade():
    op.add_column('transactions', sa.Column('from_currency', sa.String(10), nullable=True))
    op.add_column('transactions', sa.Column('to_currency', sa.String(10), nullable=True))
    op.alter_column('transactions', 'currency_pair_id', nullable=True)
