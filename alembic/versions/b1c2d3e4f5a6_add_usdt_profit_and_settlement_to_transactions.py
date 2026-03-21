"""add_usdt_profit_and_settlement_to_transactions

Revision ID: b1c2d3e4f5a6
Revises: a6dc585032e0
Create Date: 2026-03-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a6dc585032e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # transaction_profit_splits: campos de referencia USDT y liquidación
    op.add_column('transaction_profit_splits', sa.Column('profit_amount_usdt', sa.Float(), nullable=True))
    op.add_column('transaction_profit_splits', sa.Column('settlement_currency', sa.String(10), nullable=True))
    op.add_column('transaction_profit_splits', sa.Column('settlement_amount', sa.Float(), nullable=True))

    # transactions: ganancia total en USDT
    op.add_column('transactions', sa.Column('profit_amount_usdt', sa.Float(), nullable=True))

    # users: moneda preferida de liquidación
    op.add_column('users', sa.Column('preferred_settlement_currency', sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'preferred_settlement_currency')
    op.drop_column('transactions', 'profit_amount_usdt')
    op.drop_column('transaction_profit_splits', 'settlement_amount')
    op.drop_column('transaction_profit_splits', 'settlement_currency')
    op.drop_column('transaction_profit_splits', 'profit_amount_usdt')
