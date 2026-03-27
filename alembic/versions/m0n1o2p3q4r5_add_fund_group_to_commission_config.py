"""add_fund_group_to_commission_config

Revision ID: m0n1o2p3q4r5
Revises: l9m0n1o2p3q4
Create Date: 2026-03-27

"""
from alembic import op
import sqlalchemy as sa

revision = 'm0n1o2p3q4r5'
down_revision = 'l9m0n1o2p3q4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'commission_configurations',
        sa.Column('fund_group_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_commission_configurations_fund_group_id',
        'commission_configurations', 'fund_groups',
        ['fund_group_id'], ['id']
    )
    op.create_index(
        'ix_commission_configurations_fund_group_id',
        'commission_configurations', ['fund_group_id']
    )


def downgrade():
    op.drop_index('ix_commission_configurations_fund_group_id', table_name='commission_configurations')
    op.drop_constraint('fk_commission_configurations_fund_group_id', 'commission_configurations', type_='foreignkey')
    op.drop_column('commission_configurations', 'fund_group_id')
