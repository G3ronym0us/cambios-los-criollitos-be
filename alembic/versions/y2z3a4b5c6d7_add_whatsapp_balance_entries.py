"""add whatsapp balance entries (saldo a favor del cliente)

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = 'y2z3a4b5c6d7'
down_revision = 'x1y2z3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'whatsapp_balance_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('entry_type', sa.Enum('CREDIT', 'DEBIT', name='whatsappbalanceentrytype'), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False, server_default='USD'),
        sa.Column('incoming_payment_id', sa.Integer(), nullable=True),
        sa.Column('whatsapp_operation_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['client_id'], ['whatsapp_clients.id']),
        sa.ForeignKeyConstraint(['incoming_payment_id'], ['whatsapp_incoming_payments.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['whatsapp_operation_id'], ['whatsapp_operations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index('ix_whatsapp_balance_entries_uuid', 'whatsapp_balance_entries', ['uuid'])
    op.create_index('ix_whatsapp_balance_entries_client', 'whatsapp_balance_entries', ['client_id'])
    op.create_index('ix_whatsapp_balance_entries_incoming', 'whatsapp_balance_entries', ['incoming_payment_id'])
    op.create_index('ix_whatsapp_balance_entries_operation', 'whatsapp_balance_entries', ['whatsapp_operation_id'])


def downgrade():
    op.drop_index('ix_whatsapp_balance_entries_operation', table_name='whatsapp_balance_entries')
    op.drop_index('ix_whatsapp_balance_entries_incoming', table_name='whatsapp_balance_entries')
    op.drop_index('ix_whatsapp_balance_entries_client', table_name='whatsapp_balance_entries')
    op.drop_index('ix_whatsapp_balance_entries_uuid', table_name='whatsapp_balance_entries')
    op.drop_table('whatsapp_balance_entries')
    sa.Enum(name='whatsappbalanceentrytype').drop(op.get_bind(), checkfirst=True)
