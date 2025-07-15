"""populate_currency_pairs_table

Revision ID: 462ef9732da7
Revises: bcc069818759
Create Date: 2025-07-02 18:27:51.171924

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '462ef9732da7'
down_revision: Union[str, None] = 'bcc069818759'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Populate currency pairs table with existing trading pairs from the system."""
    from datetime import datetime
    
    # Get currency IDs first (we know they exist from previous migration)
    connection = op.get_bind()
    
    # Query currency IDs
    result = connection.execute(sa.text("SELECT id, symbol FROM currencies"))
    currency_map = {row[1]: row[0] for row in result}
    
    # Define all currency pairs used in the system
    # Based on analysis of app/services/scrapers/binance_scraper.py
    pairs_data = []
    now = datetime.utcnow()
    
    # Primary pairs with USDT (main trading pairs)
    if 'VES' in currency_map and 'USDT' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['VES'],
                'to_currency_id': currency_map['USDT'],
                'pair_symbol': 'VES-USDT',
                'description': 'Bolívar Venezolano a Tether - Par principal',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['USDT'],
                'to_currency_id': currency_map['VES'],
                'pair_symbol': 'USDT-VES',
                'description': 'Tether a Bolívar Venezolano - Par principal',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'COP' in currency_map and 'USDT' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['COP'],
                'to_currency_id': currency_map['USDT'],
                'pair_symbol': 'COP-USDT',
                'description': 'Peso Colombiano a Tether - Par principal',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['USDT'],
                'to_currency_id': currency_map['COP'],
                'pair_symbol': 'USDT-COP',
                'description': 'Tether a Peso Colombiano - Par principal',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'BRL' in currency_map and 'USDT' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['BRL'],
                'to_currency_id': currency_map['USDT'],
                'pair_symbol': 'BRL-USDT',
                'description': 'Real Brasileño a Tether - Par principal',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['USDT'],
                'to_currency_id': currency_map['BRL'],
                'pair_symbol': 'USDT-BRL',
                'description': 'Tether a Real Brasileño - Par principal',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    # Pairs with Zelle (derived rates)
    if 'VES' in currency_map and 'ZELLE' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['VES'],
                'to_currency_id': currency_map['ZELLE'],
                'pair_symbol': 'VES-ZELLE',
                'description': 'Bolívar Venezolano a Zelle - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['ZELLE'],
                'to_currency_id': currency_map['VES'],
                'pair_symbol': 'ZELLE-VES',
                'description': 'Zelle a Bolívar Venezolano - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'COP' in currency_map and 'ZELLE' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['COP'],
                'to_currency_id': currency_map['ZELLE'],
                'pair_symbol': 'COP-ZELLE',
                'description': 'Peso Colombiano a Zelle - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['ZELLE'],
                'to_currency_id': currency_map['COP'],
                'pair_symbol': 'ZELLE-COP',
                'description': 'Zelle a Peso Colombiano - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'BRL' in currency_map and 'ZELLE' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['BRL'],
                'to_currency_id': currency_map['ZELLE'],
                'pair_symbol': 'BRL-ZELLE',
                'description': 'Real Brasileño a Zelle - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['ZELLE'],
                'to_currency_id': currency_map['BRL'],
                'pair_symbol': 'ZELLE-BRL',
                'description': 'Zelle a Real Brasileño - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    # Pairs with PayPal (derived rates)
    if 'VES' in currency_map and 'PAYPAL' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['VES'],
                'to_currency_id': currency_map['PAYPAL'],
                'pair_symbol': 'VES-PAYPAL',
                'description': 'Bolívar Venezolano a PayPal - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['PAYPAL'],
                'to_currency_id': currency_map['VES'],
                'pair_symbol': 'PAYPAL-VES',
                'description': 'PayPal a Bolívar Venezolano - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'COP' in currency_map and 'PAYPAL' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['COP'],
                'to_currency_id': currency_map['PAYPAL'],
                'pair_symbol': 'COP-PAYPAL',
                'description': 'Peso Colombiano a PayPal - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['PAYPAL'],
                'to_currency_id': currency_map['COP'],
                'pair_symbol': 'PAYPAL-COP',
                'description': 'PayPal a Peso Colombiano - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'BRL' in currency_map and 'PAYPAL' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['BRL'],
                'to_currency_id': currency_map['PAYPAL'],
                'pair_symbol': 'BRL-PAYPAL',
                'description': 'Real Brasileño a PayPal - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['PAYPAL'],
                'to_currency_id': currency_map['BRL'],
                'pair_symbol': 'PAYPAL-BRL',
                'description': 'PayPal a Real Brasileño - Tasa derivada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    # Cross pairs (direct fiat-to-fiat)
    if 'VES' in currency_map and 'COP' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['VES'],
                'to_currency_id': currency_map['COP'],
                'pair_symbol': 'VES-COP',
                'description': 'Bolívar Venezolano a Peso Colombiano - Tasa cruzada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['COP'],
                'to_currency_id': currency_map['VES'],
                'pair_symbol': 'COP-VES',
                'description': 'Peso Colombiano a Bolívar Venezolano - Tasa cruzada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'VES' in currency_map and 'BRL' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['VES'],
                'to_currency_id': currency_map['BRL'],
                'pair_symbol': 'VES-BRL',
                'description': 'Bolívar Venezolano a Real Brasileño - Tasa cruzada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['BRL'],
                'to_currency_id': currency_map['VES'],
                'pair_symbol': 'BRL-VES',
                'description': 'Real Brasileño a Bolívar Venezolano - Tasa cruzada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    if 'COP' in currency_map and 'BRL' in currency_map:
        pairs_data.extend([
            {
                'from_currency_id': currency_map['COP'],
                'to_currency_id': currency_map['BRL'],
                'pair_symbol': 'COP-BRL',
                'description': 'Peso Colombiano a Real Brasileño - Tasa cruzada',
                'is_active': True,
                'is_monitored': True
            },
            {
                'from_currency_id': currency_map['BRL'],
                'to_currency_id': currency_map['COP'],
                'pair_symbol': 'BRL-COP',
                'description': 'Real Brasileño a Peso Colombiano - Tasa cruzada',
                'is_active': True,
                'is_monitored': True
            }
        ])
    
    # Insert all pairs
    for pair_data in pairs_data:
        pair_data['created_at'] = now
        pair_data['updated_at'] = now
        
        connection.execute(
            sa.text("""
                INSERT INTO currency_pairs (from_currency_id, to_currency_id, pair_symbol, 
                                          description, is_active, is_monitored, created_at, updated_at)
                VALUES (:from_currency_id, :to_currency_id, :pair_symbol, 
                        :description, :is_active, :is_monitored, :created_at, :updated_at)
            """),
            pair_data
        )
    
    print(f"✅ Inserted {len(pairs_data)} currency pairs")


def downgrade() -> None:
    """Remove all populated currency pairs."""
    # Delete all pairs that were inserted
    pair_symbols = [
        'VES-USDT', 'USDT-VES', 'COP-USDT', 'USDT-COP', 'BRL-USDT', 'USDT-BRL',
        'VES-ZELLE', 'ZELLE-VES', 'COP-ZELLE', 'ZELLE-COP', 'BRL-ZELLE', 'ZELLE-BRL',
        'VES-PAYPAL', 'PAYPAL-VES', 'COP-PAYPAL', 'PAYPAL-COP', 'BRL-PAYPAL', 'PAYPAL-BRL',
        'VES-COP', 'COP-VES', 'VES-BRL', 'BRL-VES', 'COP-BRL', 'BRL-COP'
    ]
    
    symbols_str = "', '".join(pair_symbols)
    op.execute(sa.text(f"DELETE FROM currency_pairs WHERE pair_symbol IN ('{symbols_str}')"))
    
    print(f"✅ Removed {len(pair_symbols)} currency pairs")
