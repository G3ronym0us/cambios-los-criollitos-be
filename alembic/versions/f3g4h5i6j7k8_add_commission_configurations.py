"""add commission configurations

Revision ID: f3g4h5i6j7k8
Revises: e2f3g4h5i6j7
Create Date: 2025-10-13 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3g4h5i6j7k8'
down_revision: Union[str, None] = 'e2f3g4h5i6j7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Add commission configuration tables."""

    # Create commission_configurations table
    op.create_table(
        'commission_configurations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pair_symbol', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('total_percentage', sa.Float(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_commission_configurations_id'), 'commission_configurations', ['id'], unique=False)
    op.create_index(op.f('ix_commission_configurations_pair_symbol'), 'commission_configurations', ['pair_symbol'], unique=False)

    # Create commission_configuration_splits table
    op.create_table(
        'commission_configuration_splits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('configuration_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('percentage', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['configuration_id'], ['commission_configurations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_commission_configuration_splits_configuration_id'), 'commission_configuration_splits', ['configuration_id'], unique=False)
    op.create_index(op.f('ix_commission_configuration_splits_user_id'), 'commission_configuration_splits', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema - Remove commission configuration tables."""

    op.drop_index(op.f('ix_commission_configuration_splits_user_id'), table_name='commission_configuration_splits')
    op.drop_index(op.f('ix_commission_configuration_splits_configuration_id'), table_name='commission_configuration_splits')
    op.drop_table('commission_configuration_splits')

    op.drop_index(op.f('ix_commission_configurations_pair_symbol'), table_name='commission_configurations')
    op.drop_index(op.f('ix_commission_configurations_id'), table_name='commission_configurations')
    op.drop_table('commission_configurations')
