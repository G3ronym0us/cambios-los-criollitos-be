"""add is_irrelevant to whatsapp incoming payments

Ya existía para los SALIENTES (dinero nuestro que salió por algo que no es un cambio) pero
no para los ENTRANTES: un comprobante que llega al chat y no es en realidad un pago al
negocio (una captura reenviada por error, un duplicado del mismo Zelle, plata que el cliente
mandó por otra cosa) no tenía dónde clasificarse, y se quedaba pegado en la bandeja "por
atender" para siempre — el mismo caso #4691 de los salientes, del lado entrante.

Revision ID: a052f8862e15
Revises: d5e2f8a91c33
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa


revision = "a052f8862e15"
down_revision = "d5e2f8a91c33"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "whatsapp_incoming_payments",
        sa.Column("is_irrelevant", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "whatsapp_incoming_payments",
        sa.Column("irrelevant_description", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("whatsapp_incoming_payments", "irrelevant_description")
    op.drop_column("whatsapp_incoming_payments", "is_irrelevant")
