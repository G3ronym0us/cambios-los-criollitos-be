"""add whatsapp_operations.origin

De dónde nació cada cotización: texto del cliente, texto con un comprobante al lado, o
creada desde un comprobante entrante/saliente. Al vincular, el operador necesita saberlo.

El relleno reconstruye lo viejo con el rastro que dejó cada camino:
  * TEXT: el bot registró el mensaje que la creó (`whatsapp_operation_messages`).
  * TEXT_RECEIPT: además el cliente mandó un comprobante entrante entre 10 min antes y
    2 min después (la captura + datos en Bs de la que salió el monto).
  * INCOMING_RECEIPT / OUTGOING_RECEIPT: sin mensaje, y un comprobante suyo de ese lado ya
    existía cuando se creó (se armó mirándolo).
Lo que no deja ninguno de esos rastros queda NULL: mejor «no se sabe» que adivinar.

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-10-09
"""

import sqlalchemy as sa

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("whatsapp_operations", sa.Column("origin", sa.String(24), nullable=True))
    op.create_index("ix_whatsapp_operations_origin", "whatsapp_operations", ["origin"])

    op.execute(
        """
        UPDATE whatsapp_operations o SET origin = 'TEXT'
         WHERE EXISTS (SELECT 1 FROM whatsapp_operation_messages m
                        WHERE m.whatsapp_operation_id = o.id)
        """
    )
    op.execute(
        """
        UPDATE whatsapp_operations o SET origin = 'TEXT_RECEIPT'
          FROM whatsapp_clients c
         WHERE o.origin = 'TEXT' AND c.id = o.client_id
           AND EXISTS (SELECT 1 FROM whatsapp_incoming_payments p
                        WHERE p.client_phone = c.phone
                          AND p.created_at BETWEEN o.created_at - interval '10 minutes'
                                               AND o.created_at + interval '2 minutes')
        """
    )
    op.execute(
        """
        UPDATE whatsapp_operations o SET origin = 'INCOMING_RECEIPT'
         WHERE o.origin IS NULL
           AND EXISTS (SELECT 1 FROM whatsapp_incoming_payments p
                        WHERE p.created_at <= o.created_at
                          AND (p.whatsapp_operation_id = o.id
                               OR EXISTS (SELECT 1 FROM whatsapp_payment_allocations a
                                           WHERE a.incoming_payment_id = p.id
                                             AND a.whatsapp_operation_id = o.id)))
        """
    )
    op.execute(
        """
        UPDATE whatsapp_operations o SET origin = 'OUTGOING_RECEIPT'
         WHERE o.origin IS NULL
           AND EXISTS (SELECT 1 FROM whatsapp_outgoing_payments p
                        WHERE p.created_at <= o.created_at
                          AND (p.whatsapp_operation_id = o.id
                               OR EXISTS (SELECT 1 FROM whatsapp_outgoing_settlements s
                                           WHERE s.outgoing_payment_id = p.id
                                             AND s.whatsapp_operation_id = o.id)))
        """
    )


def downgrade():
    op.drop_index("ix_whatsapp_operations_origin", table_name="whatsapp_operations")
    op.drop_column("whatsapp_operations", "origin")
