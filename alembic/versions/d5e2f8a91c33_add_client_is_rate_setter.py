"""add whatsapp client is_rate_setter

Hay clientes intermediarios que fijan ellos la tasa a la que se les compran los dólares. Lo
dicen en un mensaje suelto —«935», «A 940»— y hasta ahora eso se leía como un MONTO: en
producción acuñó cotizaciones de 935 y 940 dólares que nadie pidió.

No es ruido que filtrar: es una instrucción, y sólo la pueden dar algunos. Esta bandera dice
quiénes, igual que `is_usdt_authorized` dice quién puede pedir USDT.

Revision ID: d5e2f8a91c33
Revises: b4c1d9e70a25
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e2f8a91c33"
down_revision = "b4c1d9e70a25"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "whatsapp_clients",
        sa.Column("is_rate_setter", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("whatsapp_clients", "is_rate_setter")
