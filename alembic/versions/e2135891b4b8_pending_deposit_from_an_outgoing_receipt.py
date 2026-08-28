"""a pending deposit can point at the outgoing receipt that proves it

Someone who owes you money sends their receipt and the money is not withdrawn: it stays in
the fund, in your name. The receipt reaches the operator's own chat and is filed as an
outgoing, so the existing `source_incoming_payment_id` cannot hold it. Without this column
the deposit is typed by hand in /admin/funds with no link to the receipt, and the two records
of the same money never meet (payment 4928, 24 Aug).

Revision ID: e2135891b4b8
Revises: 92c939842296
Create Date: 2026-08-28 04:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e2135891b4b8'
down_revision = '92c939842296'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'fund_pending_deposits',
        sa.Column('source_outgoing_payment_id', sa.Integer(), nullable=True),
    )
    op.create_index(
        'ix_fund_pending_deposits_source_outgoing',
        'fund_pending_deposits', ['source_outgoing_payment_id'],
    )
    op.create_foreign_key(
        'fk_fund_pending_deposits_source_outgoing',
        'fund_pending_deposits', 'whatsapp_outgoing_payments',
        ['source_outgoing_payment_id'], ['id'], ondelete='SET NULL',
    )


def downgrade():
    op.drop_constraint('fk_fund_pending_deposits_source_outgoing', 'fund_pending_deposits', type_='foreignkey')
    op.drop_index('ix_fund_pending_deposits_source_outgoing', table_name='fund_pending_deposits')
    op.drop_column('fund_pending_deposits', 'source_outgoing_payment_id')
