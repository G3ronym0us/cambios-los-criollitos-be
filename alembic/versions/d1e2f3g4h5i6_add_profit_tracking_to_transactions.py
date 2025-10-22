"""add profit tracking to transactions

Revision ID: d1e2f3g4h5i6
Revises: a1b2c3d4e5f6
Create Date: 2025-10-13 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3g4h5i6'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Add profit tracking and splits."""

    # Create ENUM type first
    transaction_status_enum = sa.Enum('PENDING', 'COMPLETED', 'CANCELLED', 'FAILED', name='transactionstatus')
    transaction_status_enum.create(op.get_bind(), checkfirst=True)

    # Add new columns to transactions table
    op.add_column('transactions', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('transactions', sa.Column('total_profit_percentage', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('transactions', sa.Column('profit_amount', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('transactions', sa.Column('status', transaction_status_enum, nullable=False, server_default='PENDING'))
    op.add_column('transactions', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('transactions', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))

    # Create transaction_profit_splits table
    op.create_table(
        'transaction_profit_splits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('profit_percentage', sa.Float(), nullable=False),
        sa.Column('profit_amount', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_transaction_profit_splits_transaction_id'), 'transaction_profit_splits', ['transaction_id'], unique=False)
    op.create_index(op.f('ix_transaction_profit_splits_user_id'), 'transaction_profit_splits', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema - Remove profit tracking."""

    # Drop transaction_profit_splits table
    op.drop_index(op.f('ix_transaction_profit_splits_user_id'), table_name='transaction_profit_splits')
    op.drop_index(op.f('ix_transaction_profit_splits_transaction_id'), table_name='transaction_profit_splits')
    op.drop_table('transaction_profit_splits')

    # Drop enum type
    op.execute('DROP TYPE IF EXISTS transactionstatus')

    # Remove columns from transactions
    op.drop_column('transactions', 'completed_at')
    op.drop_column('transactions', 'updated_at')
    op.drop_column('transactions', 'status')
    op.drop_column('transactions', 'profit_amount')
    op.drop_column('transactions', 'total_profit_percentage')
    op.drop_column('transactions', 'description')
