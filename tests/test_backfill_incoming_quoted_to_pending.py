"""
El arrastre de las operaciones que se quedaron en QUOTED con su comprobante entrante ya
vinculado (`app/cli/backfill_incoming_quoted_to_pending.py`).

El estado colgado se simula vinculando el comprobante a mano -moviendo el FK directamente,
sin pasar por `WhatsAppPaymentService.set_operation`- porque ESE método ya sincroniza el
estado (Task 7) y dejaría la operación en PENDING de entrada, sin nada que arrastrar. Así es
como quedaron las 10 operaciones reales: con código viejo que solo tocaba el FK.
"""

from datetime import datetime, timedelta, timezone

from app.cli.backfill_incoming_quoted_to_pending import run
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from tests import factories as f


def _op(db, client, pairs, *, from_amount=100.0) -> WhatsAppOperation:
    """Una cotización QUOTED tal como la deja el bot: par ZELLE-VES, con su TTL."""
    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id,
        currency_pair_id=pairs["ZELLE-VES"].id,
        amount=from_amount,
        currency="ZELLE",
        from_amount=from_amount,
        to_amount=round(from_amount * 782.92, 2),
        rate_used=782.92,
        inverse_percentage=False,
        amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.QUOTED,
        quoted_at=now,
        expires_at=now + timedelta(minutes=30),
        approved_at=None,
    )
    db.add(op)
    db.flush()
    return op


def test_moves_a_quoted_operation_with_a_linked_incoming_to_pending(db, client, pairs):
    op_con_entrante = _op(db, client, pairs)
    pago = f.incoming(db, 100.0, phone=client.phone)
    # Vínculo a mano: es el estado colgado real, no el que deja `set_operation`.
    pago.whatsapp_operation_id = op_con_entrante.id
    db.flush()

    op_sin_entrante = _op(db, client, pairs)

    moved = run(db, dry_run=False)

    db.refresh(op_con_entrante)
    db.refresh(op_sin_entrante)
    assert moved == 1
    assert op_con_entrante.status == WhatsAppOperationStatus.PENDING
    assert op_con_entrante.approved_at is not None
    # La que no tiene comprobante no es candidata: ni se mira ni se toca.
    assert op_sin_entrante.status == WhatsAppOperationStatus.QUOTED
    assert op_sin_entrante.approved_at is None


def test_dry_run_reports_but_does_not_write(db, client, pairs):
    op_con_entrante = _op(db, client, pairs)
    pago = f.incoming(db, 100.0, phone=client.phone)
    pago.whatsapp_operation_id = op_con_entrante.id
    db.flush()

    moved = run(db, dry_run=True)

    db.refresh(op_con_entrante)
    assert moved == 0
    assert op_con_entrante.status == WhatsAppOperationStatus.QUOTED
