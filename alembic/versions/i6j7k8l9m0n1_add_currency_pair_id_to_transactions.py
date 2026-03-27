"""add currency_pair_id to transactions

Revision ID: i6j7k8l9m0n1
Revises: c2d3e4f5a6b7
Create Date: 2026-03-24 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'i6j7k8l9m0n1'
down_revision: Union[str, None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('currency_pair_id', sa.Integer(), nullable=True))

    # Backfill: corregir to_currency y asignar currency_pair_id en un paso
    op.execute("""
        UPDATE transactions t
        SET
            to_currency = tc.symbol,
            currency_pair_id = cp.id
        FROM currency_pairs cp
        JOIN currencies fc ON fc.id = cp.from_currency_id
        JOIN currencies tc ON tc.id = cp.to_currency_id
        WHERE fc.symbol = t.from_currency
          AND (
            tc.symbol = t.to_currency
            OR (
                t.to_currency = 'USD'
                AND tc.symbol = 'VES'
                AND cp.pair_symbol IN ('ZELLE-VES', 'PAYPAL-VES')
                AND fc.symbol = t.from_currency
            )
          )
    """)

    op.create_foreign_key(
        'fk_transactions_currency_pair_id',
        'transactions', 'currency_pairs',
        ['currency_pair_id'], ['id']
    )
    op.create_index('ix_transactions_currency_pair_id', 'transactions', ['currency_pair_id'])


def downgrade() -> None:
    op.drop_index('ix_transactions_currency_pair_id', table_name='transactions')
    op.drop_constraint('fk_transactions_currency_pair_id', 'transactions', type_='foreignkey')
    op.drop_column('transactions', 'currency_pair_id')
