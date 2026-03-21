"""add fund groups and movements

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-03-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d3e4f5a6b7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    # ===== fund_groups =====
    op.create_table(
        'fund_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(36), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
        sa.UniqueConstraint('name'),
    )

    # ===== fund_group_members =====
    op.create_table(
        'fund_group_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(36), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('is_fund_manager', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['group_id'], ['fund_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
        sa.UniqueConstraint('group_id', 'user_id', name='uq_fund_group_members_group_user'),
    )

    # ===== fund_movements =====
    op.create_table(
        'fund_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(36), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('movement_type', sa.String(20), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False),
        sa.Column('amount_usdt', sa.Float(), nullable=True),
        sa.Column('usdt_rate', sa.Float(), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('reference', sa.String(200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('recorded_by_user_id', sa.Integer(), nullable=True),
        sa.Column('movement_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['fund_groups.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )

    # Indices
    op.create_index('ix_fund_movements_group_id', 'fund_movements', ['group_id'])
    op.create_index('ix_fund_movements_user_id', 'fund_movements', ['user_id'])
    op.create_index('ix_fund_movements_movement_date', 'fund_movements', ['movement_date'])
    op.create_index('ix_fund_movements_movement_type', 'fund_movements', ['movement_type'])

    # ===== users.is_fund_manager =====
    op.add_column('users', sa.Column('is_fund_manager', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade():
    op.drop_index('ix_fund_movements_movement_type', table_name='fund_movements')
    op.drop_index('ix_fund_movements_movement_date', table_name='fund_movements')
    op.drop_index('ix_fund_movements_user_id', table_name='fund_movements')
    op.drop_index('ix_fund_movements_group_id', table_name='fund_movements')
    op.drop_table('fund_movements')
    op.drop_table('fund_group_members')
    op.drop_table('fund_groups')
    op.drop_column('users', 'is_fund_manager')
