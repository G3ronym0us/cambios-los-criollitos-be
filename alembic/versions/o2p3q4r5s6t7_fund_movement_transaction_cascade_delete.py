"""fund_movement_transaction_cascade_delete

Revision ID: o2p3q4r5s6t7
Revises: n1o2p3q4r5s6
Create Date: 2026-03-27

"""
from alembic import op
import sqlalchemy as sa


revision = 'o2p3q4r5s6t7'
down_revision = 'n1o2p3q4r5s6'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the existing SET NULL FK and recreate with CASCADE
    op.drop_constraint('fund_movements_transaction_id_fkey', 'fund_movements', type_='foreignkey')
    op.create_foreign_key(
        'fund_movements_transaction_id_fkey',
        'fund_movements', 'transactions',
        ['transaction_id'], ['id'],
        ondelete='CASCADE'
    )


def downgrade():
    op.drop_constraint('fund_movements_transaction_id_fkey', 'fund_movements', type_='foreignkey')
    op.create_foreign_key(
        'fund_movements_transaction_id_fkey',
        'fund_movements', 'transactions',
        ['transaction_id'], ['id'],
        ondelete='SET NULL'
    )
