"""add currency pair settles_in_cash

Hay pares que se cambian en efectivo, mano a mano, y en ellos no existe ni existirá un
comprobante entrante: nadie fotografía un billete. La regla de «por entregar» exigía ese
comprobante para contar una operación como deuda —que es lo correcto en Zelle o PayPal,
donde el comprobante siempre está— y eso dejaba invisibles pares enteros como USD-VES.

Esta bandera dice cuáles son esos pares. Con ella puesta, `ClientPendingService` deja de
exigir el entrante y vuelve a contar lo que falta por cuadrar.

Revision ID: b4c1d9e70a25
Revises: c7d8e9fa0b1c
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = "b4c1d9e70a25"
down_revision = "c7d8e9fa0b1c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "currency_pairs",
        sa.Column(
            "settles_in_cash",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("currency_pairs", "settles_in_cash")
