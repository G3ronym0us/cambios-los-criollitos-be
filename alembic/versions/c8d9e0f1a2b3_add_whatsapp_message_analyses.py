"""bitácora del analizador de mensajes (corpus)

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    # Tabla nueva y aislada: nadie la lee en caliente, no toca ninguna fila existente y su
    # escritura es fire-and-forget. Se puede borrar entera sin consecuencias operativas.
    op.create_table(
        "whatsapp_message_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wa_message_id", sa.String(length=255), nullable=True),
        sa.Column("client_phone", sa.String(length=64), nullable=False),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "analyzer", sa.String(length=32), nullable=False, server_default="heuristic-v1"
        ),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("default_pair_symbol", sa.String(length=20), nullable=True),
        sa.Column("label", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("label_source", sa.String(length=16), nullable=True),
        sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_message_analyses_id", "whatsapp_message_analyses", ["id"])
    op.create_index(
        "ix_whatsapp_message_analyses_uuid", "whatsapp_message_analyses", ["uuid"], unique=True
    )
    # wa_message_id NO es único: un mensaje editado se reanaliza y deja otra fila.
    op.create_index(
        "ix_whatsapp_message_analyses_wa_message_id",
        "whatsapp_message_analyses",
        ["wa_message_id"],
    )
    op.create_index(
        "ix_whatsapp_message_analyses_client_phone", "whatsapp_message_analyses", ["client_phone"]
    )
    # El purgado por antigüedad y el export por ventana barren por fecha.
    op.create_index(
        "ix_whatsapp_message_analyses_created_at", "whatsapp_message_analyses", ["created_at"]
    )


def downgrade():
    op.drop_index("ix_whatsapp_message_analyses_created_at", table_name="whatsapp_message_analyses")
    op.drop_index(
        "ix_whatsapp_message_analyses_client_phone", table_name="whatsapp_message_analyses"
    )
    op.drop_index(
        "ix_whatsapp_message_analyses_wa_message_id", table_name="whatsapp_message_analyses"
    )
    op.drop_index("ix_whatsapp_message_analyses_uuid", table_name="whatsapp_message_analyses")
    op.drop_index("ix_whatsapp_message_analyses_id", table_name="whatsapp_message_analyses")
    op.drop_table("whatsapp_message_analyses")
