"""add operation scenarios, fund group jid and partner whatsapp phone

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-06-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'r5s6t7u8v9w0'
down_revision = 'q4r5s6t7u8v9'
branch_labels = None
depends_on = None


# Debe coincidir con SQLEnum(WhatsAppOperationScenario): nombre de tipo derivado del
# nombre de la clase enum en minúsculas. create_type=False: el tipo se crea/elimina
# explícitamente en upgrade()/downgrade() (no por el add_column).
scenario_enum = postgresql.ENUM(
    'NORMAL', 'ZELLE_DIRECT', 'VIA_PARTNER',
    name='whatsappoperationscenario',
    create_type=False,
)


def upgrade():
    # ===== enum de escenario =====
    scenario_enum.create(op.get_bind(), checkfirst=True)

    # ===== whatsapp_operations: scenario / fund_group_id / received_by_user_id =====
    op.add_column(
        'whatsapp_operations',
        sa.Column('scenario', scenario_enum, nullable=False, server_default='NORMAL'),
    )
    op.create_index('ix_whatsapp_operations_scenario', 'whatsapp_operations', ['scenario'])

    op.add_column(
        'whatsapp_operations',
        sa.Column('fund_group_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_whatsapp_operations_fund_group_id', 'whatsapp_operations', ['fund_group_id'])
    op.create_foreign_key(
        'fk_whatsapp_operations_fund_group_id', 'whatsapp_operations', 'fund_groups',
        ['fund_group_id'], ['id'], ondelete='SET NULL',
    )

    op.add_column(
        'whatsapp_operations',
        sa.Column('received_by_user_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_whatsapp_operations_received_by_user_id', 'whatsapp_operations', ['received_by_user_id'])
    op.create_foreign_key(
        'fk_whatsapp_operations_received_by_user_id', 'whatsapp_operations', 'users',
        ['received_by_user_id'], ['id'], ondelete='SET NULL',
    )

    # ===== fund_groups.whatsapp_group_jid =====
    op.add_column(
        'fund_groups',
        sa.Column('whatsapp_group_jid', sa.String(64), nullable=True),
    )
    op.create_index('ix_fund_groups_whatsapp_group_jid', 'fund_groups', ['whatsapp_group_jid'])

    # ===== fund_group_members.whatsapp_phone =====
    op.add_column(
        'fund_group_members',
        sa.Column('whatsapp_phone', sa.String(32), nullable=True),
    )
    op.create_index('ix_fund_group_members_whatsapp_phone', 'fund_group_members', ['whatsapp_phone'])


def downgrade():
    op.drop_index('ix_fund_group_members_whatsapp_phone', table_name='fund_group_members')
    op.drop_column('fund_group_members', 'whatsapp_phone')

    op.drop_index('ix_fund_groups_whatsapp_group_jid', table_name='fund_groups')
    op.drop_column('fund_groups', 'whatsapp_group_jid')

    op.drop_constraint('fk_whatsapp_operations_received_by_user_id', 'whatsapp_operations', type_='foreignkey')
    op.drop_index('ix_whatsapp_operations_received_by_user_id', table_name='whatsapp_operations')
    op.drop_column('whatsapp_operations', 'received_by_user_id')

    op.drop_constraint('fk_whatsapp_operations_fund_group_id', 'whatsapp_operations', type_='foreignkey')
    op.drop_index('ix_whatsapp_operations_fund_group_id', table_name='whatsapp_operations')
    op.drop_column('whatsapp_operations', 'fund_group_id')

    op.drop_index('ix_whatsapp_operations_scenario', table_name='whatsapp_operations')
    op.drop_column('whatsapp_operations', 'scenario')

    scenario_enum.drop(op.get_bind(), checkfirst=True)
