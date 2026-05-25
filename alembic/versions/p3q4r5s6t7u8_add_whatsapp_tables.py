"""add whatsapp clients, operations and bcv_rates

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-05-25

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = 'p3q4r5s6t7u8'
down_revision = 'o2p3q4r5s6t7'
branch_labels = None
depends_on = None


def upgrade():
    # ===== whatsapp_clients =====
    op.create_table(
        'whatsapp_clients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', UUID(as_uuid=True), nullable=False),
        sa.Column('phone', sa.String(32), nullable=False),
        sa.Column('display_name', sa.String(120), nullable=True),
        sa.Column('preferred_pair_id', sa.Integer(), nullable=True),
        sa.Column('is_tracked', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_blocked', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_usdt_authorized', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['preferred_pair_id'], ['currency_pairs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
        sa.UniqueConstraint('phone', name='uq_whatsapp_clients_phone'),
    )
    op.create_index('ix_whatsapp_clients_uuid', 'whatsapp_clients', ['uuid'])
    op.create_index('ix_whatsapp_clients_phone', 'whatsapp_clients', ['phone'])

    # ===== whatsapp_operations =====
    # Enums se guardan como String para evitar la fricción de tipos PG enum
    # (consistente con fund_movements.movement_type).
    op.create_table(
        'whatsapp_operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('currency_pair_id', sa.Integer(), nullable=False),
        sa.Column('from_amount', sa.Float(), nullable=False),
        sa.Column('to_amount', sa.Float(), nullable=False),
        sa.Column('rate_used', sa.Float(), nullable=False),
        sa.Column('inverse_percentage', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('applied_percentage', sa.Float(), nullable=True),
        sa.Column('default_percentage', sa.Float(), nullable=True),
        sa.Column('amount_side', sa.String(10), nullable=False),
        sa.Column('bcv_usd', sa.Float(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='QUOTED'),
        sa.Column('delivery_status', sa.String(20), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('legacy_sqlite_id', sa.String(36), nullable=True),
        sa.Column('quoted_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['whatsapp_clients.id']),
        sa.ForeignKeyConstraint(['currency_pair_id'], ['currency_pairs.id']),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index('ix_whatsapp_operations_uuid', 'whatsapp_operations', ['uuid'])
    op.create_index('ix_whatsapp_operations_client_id', 'whatsapp_operations', ['client_id'])
    op.create_index('ix_whatsapp_operations_currency_pair_id', 'whatsapp_operations', ['currency_pair_id'])
    op.create_index('ix_whatsapp_operations_status', 'whatsapp_operations', ['status'])
    op.create_index('ix_whatsapp_operations_quoted_at', 'whatsapp_operations', ['quoted_at'])
    op.create_index('ix_whatsapp_operations_transaction_id', 'whatsapp_operations', ['transaction_id'])
    op.create_index('ix_whatsapp_operations_legacy_sqlite_id', 'whatsapp_operations', ['legacy_sqlite_id'])

    # ===== bcv_rates =====
    op.create_table(
        'bcv_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', UUID(as_uuid=True), nullable=False),
        sa.Column('rate', sa.Float(), nullable=False),
        sa.Column('source', sa.String(60), nullable=False, server_default='ve.dolarapi.com'),
        sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index('ix_bcv_rates_uuid', 'bcv_rates', ['uuid'])
    op.create_index('ix_bcv_rates_fetched_at', 'bcv_rates', ['fetched_at'])


def downgrade():
    op.drop_index('ix_bcv_rates_fetched_at', table_name='bcv_rates')
    op.drop_index('ix_bcv_rates_uuid', table_name='bcv_rates')
    op.drop_table('bcv_rates')

    op.drop_index('ix_whatsapp_operations_legacy_sqlite_id', table_name='whatsapp_operations')
    op.drop_index('ix_whatsapp_operations_transaction_id', table_name='whatsapp_operations')
    op.drop_index('ix_whatsapp_operations_quoted_at', table_name='whatsapp_operations')
    op.drop_index('ix_whatsapp_operations_status', table_name='whatsapp_operations')
    op.drop_index('ix_whatsapp_operations_currency_pair_id', table_name='whatsapp_operations')
    op.drop_index('ix_whatsapp_operations_client_id', table_name='whatsapp_operations')
    op.drop_index('ix_whatsapp_operations_uuid', table_name='whatsapp_operations')
    op.drop_table('whatsapp_operations')

    op.drop_index('ix_whatsapp_clients_phone', table_name='whatsapp_clients')
    op.drop_index('ix_whatsapp_clients_uuid', table_name='whatsapp_clients')
    op.drop_table('whatsapp_clients')
