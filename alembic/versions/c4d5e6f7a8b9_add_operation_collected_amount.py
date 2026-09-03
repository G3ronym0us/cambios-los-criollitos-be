"""separa el cobro del efectivo de la cobertura de la operación

En un par que se cambia en efectivo (`currency_pairs.settles_in_cash`) el trato tiene dos
patas y sólo una tenía dónde anotarse. `delivered_amount` (los comprobantes de salida) y
`uncovered_amount` (lo declarado sin comprobante) miden lo NUESTRO: cuánto del valor está
cubierto. Lo del cliente —los billetes que tiene que traer— no vivía en ninguna columna.

La consecuencia se veía en la pantalla: una USD-VES creada desde su propio comprobante en
bolívares nace cubierta (`pending_amount = 0`) y en PENDING, así que se caía de la cola de
«por entregar» del perfil sin que nadie hubiera recogido un dólar. Y marcarla desde la cola
escribía `uncovered_amount`, o sea declaraba cubierta otra vez NUESTRA pata, dejando la
operación abierta para siempre.

`collected_amount` es esa pata. Va en la moneda del valor, admite parciales —el cliente
trae 150 de los 180 que debe— y cuando llega al valor entero cierra la operación.

El backfill: los lotes de entrega que ya se marcaron sobre pares de efectivo eran, de
hecho, cobros (así lo documentaba el propio módulo aunque escribieran la otra columna), y
sin traerlos volverían a aparecer como deuda del cliente. Se copia `uncovered_amount` a
`collected_amount` y se deja el original intacto: no se mueve dinero de sitio, se dice
también en la columna correcta lo que esas filas ya decían.

Revision ID: c4d5e6f7a8b9
Revises: a052f8862e15
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "a052f8862e15"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "whatsapp_operations",
        sa.Column("collected_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "whatsapp_pending_delivery_items",
        sa.Column("kind", sa.String(length=12), nullable=False, server_default="DELIVERY"),
    )
    op.add_column(
        "whatsapp_pending_delivery_items",
        sa.Column("previous_collected", sa.Float(), nullable=True),
    )
    op.add_column(
        "whatsapp_pending_delivery_items",
        sa.Column("previous_status", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "whatsapp_pending_delivery_items",
        sa.Column("previous_delivery_status", sa.String(length=12), nullable=True),
    )

    # Lo ya marcado en efectivo pasa a contar también como cobrado. Sólo donde el par se
    # cambia en efectivo y el motivo es CASH: en un par normal `uncovered_amount` significa
    # lo que dice —se lo entregamos nosotros— y tocarlo sería inventarse un cobro.
    op.execute(
        """
        UPDATE whatsapp_operations AS o
           SET collected_amount = o.uncovered_amount
          FROM currency_pairs AS cp
         WHERE cp.id = o.currency_pair_id
           AND cp.settles_in_cash IS TRUE
           AND o.uncovered_reason = 'CASH'
           AND o.uncovered_amount IS NOT NULL
           AND o.uncovered_amount > 0
        """
    )
    # Y las filas que las marcaron dejan de llamarse entregas: fueron cobros. Lo decide el
    # par de SU operación, que es lo mismo que lo decidirá de ahora en adelante.
    #
    # `previous_collected = 0` en la misma pasada: deshacer un cobro repone esa columna y
    # las filas históricas no la traen. Antes de ellas no había nada recogido.
    op.execute(
        """
        UPDATE whatsapp_pending_delivery_items AS i
           SET kind = 'COLLECTION',
               previous_collected = COALESCE(i.previous_collected, 0)
          FROM whatsapp_operations AS o
          JOIN currency_pairs AS cp ON cp.id = o.currency_pair_id
         WHERE o.id = i.whatsapp_operation_id
           AND cp.settles_in_cash IS TRUE
        """
    )


def downgrade():
    op.drop_column("whatsapp_pending_delivery_items", "previous_delivery_status")
    op.drop_column("whatsapp_pending_delivery_items", "previous_status")
    op.drop_column("whatsapp_pending_delivery_items", "previous_collected")
    op.drop_column("whatsapp_pending_delivery_items", "kind")
    op.drop_column("whatsapp_operations", "collected_amount")
