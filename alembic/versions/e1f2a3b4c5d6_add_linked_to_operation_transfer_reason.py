"""add LINKED_TO_OPERATION to the payment transfer reasons

Linking an incoming receipt to an operation used to overwrite `client_phone` with the
operation client's phone, silently. `client_phone` is a FACT (which chat the money arrived
in) and overwriting it made receipt #582 disappear from the chat where the operator had seen
it. Linking now moves `owner_client_id` instead -- the same mechanism `transfer_client`
uses -- and records the move in `whatsapp_payment_transfers`, which needs a reason of its own.

`ALTER TYPE ... ADD VALUE` cannot run inside a transaction on Postgres < 12, so it goes in an
autocommit block; `IF NOT EXISTS` keeps a re-run harmless.

Revision ID: e1f2a3b4c5d6
Revises: 1627d8f71cd3
Create Date: 2026-09-11
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "e1f2a3b4c5d6"
down_revision = "1627d8f71cd3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE paymenttransferreason ADD VALUE IF NOT EXISTS 'LINKED_TO_OPERATION'"
        )


def downgrade() -> None:
    # Postgres no sabe quitar un valor de un enum: haría falta recrear el tipo entero y
    # reescribir las filas que ya lo usan, perdiendo el motivo real de esas mudanzas. Un
    # valor de más en el enum no molesta a nadie, así que la vuelta atrás lo deja estar.
    pass
