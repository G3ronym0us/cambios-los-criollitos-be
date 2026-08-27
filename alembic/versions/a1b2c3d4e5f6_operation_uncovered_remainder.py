"""the part of an operation's value that has no receipt

Sometimes the payout is larger than what its receipts add up to, because part of it cannot be
represented here: cash handed over, a channel the bot does not read, a credit balance, or an
adjustment in the client's favour. That gap is not an error to fix — it is declared, with a
reason, and declaring it is what lets the deal close.

`pending_amount` becomes `value - delivered - uncovered`, so an operation whose remainder is
declared reads as settled even though its receipts do not add up to the whole value.

Revision ID: a1b2c3d4e5f6
Revises: d9e0f1a2b3c4
Create Date: 2026-08-27 06:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'd9e0f1a2b3c4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('whatsapp_operations', sa.Column('uncovered_amount', sa.Float(), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('uncovered_reason', sa.String(length=24), nullable=True))


def downgrade():
    op.drop_column('whatsapp_operations', 'uncovered_reason')
    op.drop_column('whatsapp_operations', 'uncovered_amount')
