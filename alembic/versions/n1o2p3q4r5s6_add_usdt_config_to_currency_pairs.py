"""add_usdt_config_to_currency_pairs

Revision ID: n1o2p3q4r5s6
Revises: m0n1o2p3q4r5
Create Date: 2026-03-27

"""
from alembic import op
import sqlalchemy as sa


revision = 'n1o2p3q4r5s6'
down_revision = 'm0n1o2p3q4r5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('currency_pairs', sa.Column('usdt_reference_side', sa.String(4), nullable=True))
    op.add_column('currency_pairs', sa.Column('usdt_manual_rate', sa.Float(), nullable=True))
    op.add_column('currency_pairs', sa.Column('usdt_pair_id', sa.Integer(), nullable=True))
    op.add_column('currency_pairs', sa.Column('usdt_pair_inverse', sa.Boolean(), nullable=False, server_default='false'))
    op.create_foreign_key(
        'fk_currency_pairs_usdt_pair_id',
        'currency_pairs', 'currency_pairs',
        ['usdt_pair_id'], ['id']
    )


def downgrade():
    op.drop_constraint('fk_currency_pairs_usdt_pair_id', 'currency_pairs', type_='foreignkey')
    op.drop_column('currency_pairs', 'usdt_pair_inverse')
    op.drop_column('currency_pairs', 'usdt_pair_id')
    op.drop_column('currency_pairs', 'usdt_manual_rate')
    op.drop_column('currency_pairs', 'usdt_reference_side')
