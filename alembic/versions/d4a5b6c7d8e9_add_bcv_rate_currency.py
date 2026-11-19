"""add currency to bcv rates

Cada moneda del BCV es su propia serie: el euro oficial no se deriva del
dólar oficial. Sin `currency`, el histórico en euros devolvería la tasa del
dólar (o la última de cualquier moneda) y la calculadora cotizaría mal.

Las filas existentes son todas del dólar oficial, así que el server_default
las marca como USD sin backfill aparte.

Revision ID: d4a5b6c7d8e9
Revises: c9d0e1f2a3b4
Create Date: 2026-11-18
"""

from alembic import op
import sqlalchemy as sa


revision = "d4a5b6c7d8e9"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bcv_rates",
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
    )
    op.create_index(
        "ix_bcv_rates_currency_fetched_at",
        "bcv_rates",
        ["currency", "fetched_at"],
    )


def downgrade():
    op.drop_index("ix_bcv_rates_currency_fetched_at", table_name="bcv_rates")
    op.drop_column("bcv_rates", "currency")
