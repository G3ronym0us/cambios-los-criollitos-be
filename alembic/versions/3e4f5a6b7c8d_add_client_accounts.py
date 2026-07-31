"""add client accounts (beneficiary address book)

Revision ID: 3e4f5a6b7c8d
Revises: 2d3e4f5a6b7c
Create Date: 2026-07-31

Migra la cuenta única de cada cliente (whatsapp_clients.default_payment_info) a una fila
de whatsapp_client_accounts con is_default=true. Las columnas viejas NO se borran acá: el
backend deja de leerlas pero las sigue exponiendo derivadas, para que un bot desplegado
antes que el backend siga funcionando. Se borran en una migración posterior.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3e4f5a6b7c8d"
down_revision: Union[str, None] = "2d3e4f5a6b7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_client_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=120), nullable=True),
        sa.Column("alias_normalized", sa.String(length=120), nullable=True),
        sa.Column("payment_info", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source", sa.String(length=10), server_default="MANUAL", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["whatsapp_clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "payment_info", name="uq_client_account_payment_info"),
    )
    op.create_index("ix_whatsapp_client_accounts_id", "whatsapp_client_accounts", ["id"])
    op.create_index("ix_whatsapp_client_accounts_uuid", "whatsapp_client_accounts", ["uuid"], unique=True)
    op.create_index("ix_whatsapp_client_accounts_client_id", "whatsapp_client_accounts", ["client_id"])
    op.create_index(
        "ix_whatsapp_client_accounts_alias_normalized", "whatsapp_client_accounts", ["alias_normalized"]
    )
    op.create_index("ix_client_account_lookup", "whatsapp_client_accounts", ["client_id", "alias_normalized"])
    # A lo sumo una cuenta predeterminada por cliente.
    op.create_index(
        "uq_client_account_one_default",
        "whatsapp_client_accounts",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    # Movimiento de datos: la cuenta única de cada cliente pasa a ser su predeterminada.
    op.execute(
        """
        INSERT INTO whatsapp_client_accounts
            (uuid, client_id, alias, alias_normalized, payment_info, currency,
             is_default, is_confirmed, source, created_at)
        SELECT gen_random_uuid()::text, id, NULL, NULL,
               default_payment_info, default_payment_currency,
               true, true, 'MANUAL', now()
        FROM whatsapp_clients
        WHERE default_payment_info IS NOT NULL
          AND btrim(default_payment_info) <> ''
          AND default_payment_currency IS NOT NULL
          AND btrim(default_payment_currency) <> ''
        """
    )

    op.add_column("whatsapp_operations", sa.Column("beneficiary_alias", sa.String(length=120), nullable=True))
    op.add_column("whatsapp_operations", sa.Column("beneficiary_account_id", sa.Integer(), nullable=True))
    op.add_column(
        "whatsapp_operations",
        sa.Column("beneficiary_ambiguous", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_foreign_key(
        "fk_operation_beneficiary_account",
        "whatsapp_operations",
        "whatsapp_client_accounts",
        ["beneficiary_account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_operation_beneficiary_account", "whatsapp_operations", type_="foreignkey")
    op.drop_column("whatsapp_operations", "beneficiary_ambiguous")
    op.drop_column("whatsapp_operations", "beneficiary_account_id")
    op.drop_column("whatsapp_operations", "beneficiary_alias")
    op.drop_table("whatsapp_client_accounts")
