"""populate_currencies_table

Revision ID: dfa81171770a
Revises: 6ae0be7a8bf3
Create Date: 2025-07-02 15:18:44.751747

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dfa81171770a'
down_revision: Union[str, None] = '6ae0be7a8bf3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Populate currencies table with existing currencies."""
    # Define currency data based on existing enum
    currencies_data = [
        {
            'name': 'Bolívar Venezolano',
            'symbol': 'VES',
            'description': 'Moneda oficial de Venezuela',
            'currency_type': 'FIAT'
        },
        {
            'name': 'Peso Colombiano',
            'symbol': 'COP',
            'description': 'Moneda oficial de Colombia',
            'currency_type': 'FIAT'
        },
        {
            'name': 'Real Brasileño',
            'symbol': 'BRL',
            'description': 'Moneda oficial de Brasil',
            'currency_type': 'FIAT'
        },
        {
            'name': 'Tether',
            'symbol': 'USDT',
            'description': 'Criptomoneda estable vinculada al dólar estadounidense',
            'currency_type': 'CRYPTO'
        },
        {
            'name': 'Zelle',
            'symbol': 'ZELLE',
            'description': 'Sistema de pagos digitales',
            'currency_type': 'FIAT'
        },
        {
            'name': 'PayPal',
            'symbol': 'PAYPAL',
            'description': 'Plataforma de pagos digitales',
            'currency_type': 'FIAT'
        }
    ]
    
    # Insert currencies using execute statements
    from datetime import datetime
    now = datetime.utcnow()
    
    # Insert each currency individually to avoid enum casting issues
    for currency in currencies_data:
        op.execute(
            f"""
            INSERT INTO currencies (name, symbol, description, currency_type, created_at, updated_at)
            VALUES ('{currency['name']}', '{currency['symbol']}', '{currency['description']}', 
                    '{currency['currency_type']}'::currencytype, '{now}', '{now}')
            """
        )


def downgrade() -> None:
    """Remove populated currencies."""
    # Delete all currencies that were inserted
    symbols_to_delete = ['VES', 'COP', 'BRL', 'USDT', 'ZELLE', 'PAYPAL']
    
    currencies_table = sa.table(
        'currencies',
        sa.column('symbol', sa.String)
    )
    
    op.execute(
        currencies_table.delete().where(
            currencies_table.c.symbol.in_(symbols_to_delete)
        )
    )
