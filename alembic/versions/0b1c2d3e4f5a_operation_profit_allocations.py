"""el margen de una operación se reparte entre destinos (fondos, cliente)

La operación dice lo que se le cobró al cliente; hasta ahora eso era también, entero y sin
decirlo, la ganancia del negocio. Ahora se reparte:

- `operation_profit_allocations`: una fila por destino (fondo o cliente) con su porcentaje.
  El caso corriente es una sola fila —el fondo que atendió la operación— pero admite varias:
  ZELLE→BRL al 10% dejando 8% en el fondo Zelle y 2% en el de Brasil, o 7% al fondo y 2%
  devuelto al cliente que hizo de intermediario.
- `fund_groups.default_profit_percentage`: cuánto se queda el fondo por defecto (7 de un 8
  cobrado). NULL = todo lo cobrado.
- `fund_group_members.profit_share_percentage`: cómo se reparte esa ganancia entre los socios
  (Zelle: 50/50). Reemplaza a las `commission_configurations` por par, que nunca se usaron.

Revision ID: 0b1c2d3e4f5a
Revises: f0a1b2c3d4e5
Create Date: 2026-07-25 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0b1c2d3e4f5a'
down_revision: Union[str, None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('fund_groups', sa.Column('default_profit_percentage', sa.Float(), nullable=True))
    op.add_column('fund_group_members', sa.Column('profit_share_percentage', sa.Float(), nullable=True))

    op.create_table(
        'operation_profit_allocations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('whatsapp_operation_id', sa.Integer(), nullable=False),
        sa.Column('destination_type', sa.String(length=20), nullable=False),
        sa.Column('fund_group_id', sa.Integer(), nullable=True),
        sa.Column('whatsapp_client_id', sa.Integer(), nullable=True),
        sa.Column('percentage', sa.Float(), nullable=False),
        sa.Column('amount_usdt', sa.Float(), nullable=True),
        sa.Column('approved_by_user_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['whatsapp_operation_id'], ['whatsapp_operations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['fund_group_id'], ['fund_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['whatsapp_client_id'], ['whatsapp_clients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "(destination_type = 'FUND' AND fund_group_id IS NOT NULL AND whatsapp_client_id IS NULL)"
            " OR (destination_type = 'CLIENT' AND whatsapp_client_id IS NOT NULL AND fund_group_id IS NULL)",
            name='ck_profit_allocation_destination',
        ),
    )
    op.create_index('ix_operation_profit_allocations_uuid', 'operation_profit_allocations', ['uuid'], unique=True)
    op.create_index('ix_operation_profit_allocations_operation', 'operation_profit_allocations', ['whatsapp_operation_id'])
    op.create_index('ix_operation_profit_allocations_fund_group', 'operation_profit_allocations', ['fund_group_id'])
    op.create_index('ix_operation_profit_allocations_client', 'operation_profit_allocations', ['whatsapp_client_id'])
    op.create_index('ix_operation_profit_allocations_destination', 'operation_profit_allocations', ['destination_type'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_operation_profit_allocations_destination', table_name='operation_profit_allocations')
    op.drop_index('ix_operation_profit_allocations_client', table_name='operation_profit_allocations')
    op.drop_index('ix_operation_profit_allocations_fund_group', table_name='operation_profit_allocations')
    op.drop_index('ix_operation_profit_allocations_operation', table_name='operation_profit_allocations')
    op.drop_index('ix_operation_profit_allocations_uuid', table_name='operation_profit_allocations')
    op.drop_table('operation_profit_allocations')
    op.drop_column('fund_group_members', 'profit_share_percentage')
    op.drop_column('fund_groups', 'default_profit_percentage')
