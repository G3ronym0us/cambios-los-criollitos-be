"""add whatsapp incoming/outgoing payments

Revision ID: q4r5s6t7u8v9
Revises: 0269eb2186ac
Create Date: 2026-05-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = 'q4r5s6t7u8v9'
down_revision = '0269eb2186ac'
branch_labels = None
depends_on = None


def upgrade():
    # ===== whatsapp_incoming_payments =====
    op.create_table(
        'whatsapp_incoming_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', UUID(as_uuid=True), nullable=False),
        sa.Column('client_phone', sa.String(64), nullable=False),
        sa.Column('provider', sa.String(60), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('currency', sa.String(10), nullable=True),
        sa.Column('bank_from', sa.String(120), nullable=True),
        sa.Column('bank_to', sa.String(120), nullable=True),
        sa.Column('account_number', sa.String(60), nullable=True),
        sa.Column('identification', sa.String(60), nullable=True),
        sa.Column('phone_to', sa.String(40), nullable=True),
        sa.Column('reference', sa.String(120), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('whatsapp_operation_id', sa.Integer(), nullable=True),
        sa.Column('corrected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('correction_original', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['whatsapp_operation_id'], ['whatsapp_operations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index('ix_whatsapp_incoming_payments_uuid', 'whatsapp_incoming_payments', ['uuid'])
    op.create_index('ix_whatsapp_incoming_payments_client_phone', 'whatsapp_incoming_payments', ['client_phone'])
    op.create_index('ix_whatsapp_incoming_payments_operation', 'whatsapp_incoming_payments', ['whatsapp_operation_id'])

    # ===== whatsapp_outgoing_payments =====
    op.create_table(
        'whatsapp_outgoing_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', UUID(as_uuid=True), nullable=False),
        sa.Column('client_phone', sa.String(64), nullable=False),
        sa.Column('provider', sa.String(60), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('currency', sa.String(10), nullable=True),
        sa.Column('bank_from', sa.String(120), nullable=True),
        sa.Column('bank_to', sa.String(120), nullable=True),
        sa.Column('account_number', sa.String(60), nullable=True),
        sa.Column('identification', sa.String(60), nullable=True),
        sa.Column('phone_to', sa.String(40), nullable=True),
        sa.Column('reference', sa.String(120), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('whatsapp_operation_id', sa.Integer(), nullable=True),
        sa.Column('is_personal_expense', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('personal_description', sa.Text(), nullable=True),
        sa.Column('is_irrelevant', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('source_payment_id', sa.Integer(), nullable=True),
        sa.Column('corrected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('correction_original', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['whatsapp_operation_id'], ['whatsapp_operations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_payment_id'], ['whatsapp_incoming_payments.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index('ix_whatsapp_outgoing_payments_uuid', 'whatsapp_outgoing_payments', ['uuid'])
    op.create_index('ix_whatsapp_outgoing_payments_client_phone', 'whatsapp_outgoing_payments', ['client_phone'])
    op.create_index('ix_whatsapp_outgoing_payments_operation', 'whatsapp_outgoing_payments', ['whatsapp_operation_id'])
    op.create_index('ix_whatsapp_outgoing_payments_source', 'whatsapp_outgoing_payments', ['source_payment_id'])


def downgrade():
    op.drop_index('ix_whatsapp_outgoing_payments_source', table_name='whatsapp_outgoing_payments')
    op.drop_index('ix_whatsapp_outgoing_payments_operation', table_name='whatsapp_outgoing_payments')
    op.drop_index('ix_whatsapp_outgoing_payments_client_phone', table_name='whatsapp_outgoing_payments')
    op.drop_index('ix_whatsapp_outgoing_payments_uuid', table_name='whatsapp_outgoing_payments')
    op.drop_table('whatsapp_outgoing_payments')

    op.drop_index('ix_whatsapp_incoming_payments_operation', table_name='whatsapp_incoming_payments')
    op.drop_index('ix_whatsapp_incoming_payments_client_phone', table_name='whatsapp_incoming_payments')
    op.drop_index('ix_whatsapp_incoming_payments_uuid', table_name='whatsapp_incoming_payments')
    op.drop_table('whatsapp_incoming_payments')
