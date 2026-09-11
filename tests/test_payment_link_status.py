"""
El estado de la operación cuando se le vincula (o se le quita) un comprobante ENTRANTE.

Una cotización que recibe el respaldo del cliente pasa a `PENDING`; si se queda sin ningún
entrante, vuelve a `QUOTED`. Hasta ahora `set_operation` sólo sincronizaba estado en la rama
de SALIENTES, y por eso quedaron 10 operaciones colgadas en `QUOTED` y vencidas — fuera de
todas las bandejas de acción.

Ver `docs/superpowers/specs/2026-09-10-operacion-sugerida-por-cliente-design.md` (defecto 3).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationScenario,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment_transfer import (
    PaymentTransferReason,
    WhatsAppPaymentTransfer,
)
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f


@pytest.fixture
def svc(db):
    return WhatsAppPaymentService(db)


def _op(
    db,
    client,
    pairs,
    *,
    status=WhatsAppOperationStatus.QUOTED,
    expires_in_minutes=30,
    scenario=WhatsAppOperationScenario.NORMAL,
    from_amount=100.0,
) -> WhatsAppOperation:
    """Una cotización tal como la deja el bot: par ZELLE-VES, con su TTL."""
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
        status=status,
        scenario=scenario,
        quoted_at=now,
        expires_at=now + timedelta(minutes=expires_in_minutes),
        approved_at=now if status != WhatsAppOperationStatus.QUOTED else None,
    )
    db.add(op)
    db.flush()
    return op


# ---------------------------------------------------------------------------
# El entrante mueve la cotización
# ---------------------------------------------------------------------------


def test_linking_an_incoming_moves_a_quote_to_pending(svc, db, client, pairs):
    op = _op(db, client, pairs)
    pago = f.incoming(db, 100.0, phone=client.phone)

    svc.set_operation("incoming", pago.id, op.uuid)

    db.refresh(op)
    assert op.status.value == "PENDING"
    assert op.approved_at is not None


def test_an_expired_quote_still_moves_to_pending(svc, db, client, pairs):
    """El TTL protege «el cliente no acepta una tasa vieja». Un comprobante ya es dinero movido."""
    op = _op(db, client, pairs, expires_in_minutes=-4320)  # venció hace tres días
    pago = f.incoming(db, 100.0, phone=client.phone)

    svc.set_operation("incoming", pago.id, op.uuid)

    db.refresh(op)
    assert op.status.value == "PENDING"


def test_unlinking_the_last_incoming_returns_the_operation_to_quoted(svc, db, client, pairs, operator):
    op = _op(db, client, pairs)
    pago = f.incoming(db, 100.0, phone=client.phone)
    svc.set_operation("incoming", pago.id, op.uuid)
    db.refresh(op)
    assert op.status.value == "PENDING"

    # Es el único comprobante de la op: hay que decir explícitamente que se mantiene.
    svc.set_operation("incoming", pago.id, None, completing_user=operator, orphan_action="KEEP")

    db.refresh(op)
    assert op.status.value == "QUOTED"
    assert op.approved_at is None


def test_a_second_incoming_still_leaves_the_operation_pending(svc, db, client, pairs, operator):
    """Quitar uno de dos entrantes no devuelve nada: la op sigue respaldada."""
    op = _op(db, client, pairs, from_amount=200.0)
    uno = f.incoming(db, 100.0, phone=client.phone)
    dos = f.incoming(db, 100.0, phone=client.phone)
    svc.set_operation("incoming", uno.id, op.uuid)
    svc.set_operation("incoming", dos.id, op.uuid)

    svc.set_operation("incoming", dos.id, None, completing_user=operator)

    db.refresh(op)
    assert op.status.value == "PENDING"


def test_a_completed_operation_is_not_touched(svc, db, client, pairs):
    op = _op(db, client, pairs, status=WhatsAppOperationStatus.COMPLETED)
    pago = f.incoming(db, 100.0, phone=client.phone)

    svc.set_operation("incoming", pago.id, op.uuid)

    db.refresh(op)
    assert op.status.value == "COMPLETED"


# ---------------------------------------------------------------------------
# El camino de vuelta no puede tocar a quien nunca tuvo entrante
# ---------------------------------------------------------------------------


def test_a_via_partner_operation_never_falls_back_to_quoted(svc, db, client, pairs, operator):
    """
    En `VIA_PARTNER` el socio le cobra al cliente en su propio WhatsApp: el comprobante
    entrante NUNCA llega al operador (ver `backend/CLAUDE.md`). Una op así está en `PENDING`
    legítimamente sin haber tenido jamás un entrante, y desvincularle el saliente no puede
    devolverla a `QUOTED`.
    """
    op = _op(
        db, client, pairs,
        status=WhatsAppOperationStatus.PENDING,
        scenario=WhatsAppOperationScenario.VIA_PARTNER,
    )
    saliente = f.outgoing(db, 78292.0, "VES", phone=client.phone)
    svc.set_operation("outgoing", saliente.id, op.uuid)
    db.refresh(op)
    assert op.status.value == "PENDING"

    svc.set_operation("outgoing", saliente.id, None, completing_user=operator, orphan_action="KEEP")

    db.refresh(op)
    assert op.status.value == "PENDING"


# ---------------------------------------------------------------------------
# Re-vincular toca DOS operaciones: la que gana el comprobante y la que lo pierde
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "No lo impide el sync de estado, que ya sincroniza las DOS operaciones, sino el "
        "reparto: re-vincular por `set_operation` muda el FK a B pero NO borra la fila de "
        "`whatsapp_payment_allocations` de A (`_upsert_allocation` solo escribe la de B, "
        "mientras que la ruta de desvincular sí llama a `_drop_allocation`). A conserva "
        "respaldo y sigue en PENDING con razón. Desde el panel esta ruta no se alcanza -- "
        "con `complete_outgoing=True` da 409 'desvincúlalo primero' -- así que solo llega "
        "por el bot. Si el criterio es que un re-vínculo directo muda el pago entero, el "
        "arreglo es soltar el reparto anterior ahí; si un pago puede cubrir A y B a la vez, "
        "la ruta correcta es `PUT /incoming/{id}/allocations` y este test sobra."
    ),
    strict=False,
)
def test_moving_a_receipt_returns_the_operation_it_left_to_quoted(svc, db, client, pairs, operator):
    """
    Mover un comprobante de la op A a la op B deja a A sin ningún entrante. Si solo se
    sincroniza B, A se queda en `PENDING` sin respaldo — la misma operación colgada que este
    módulo viene a eliminar, nada más que en espejo.
    """
    a = _op(db, client, pairs, status=WhatsAppOperationStatus.QUOTED)
    b = _op(db, client, pairs, status=WhatsAppOperationStatus.QUOTED)
    pago = f.incoming(db, 100.0, phone=client.phone)

    svc.set_operation("incoming", pago.id, a.uuid, completing_user=operator)
    db.refresh(a)
    assert a.status.value == "PENDING"

    svc.set_operation("incoming", pago.id, b.uuid, completing_user=operator)

    db.refresh(a)
    db.refresh(b)
    assert b.status.value == "PENDING"
    assert a.status.value == "QUOTED"


# ---------------------------------------------------------------------------
# Vincular afirma de quién es el dinero, pero no borra dónde llegó
# ---------------------------------------------------------------------------


def _client(db, phone: str, name: str) -> WhatsAppClient:
    row = WhatsAppClient(phone=phone, display_name=name, is_tracked=True)
    db.add(row)
    db.flush()
    return row


def test_linking_to_another_clients_operation_leaves_a_trail(svc, db, pairs, operator):
    """
    Caso #582: el Zelle llegó en el chat de José Bogao y se vinculó a la operación de
    Arianna. Vincular puede afirmar que el dinero es de Arianna —eso va en `owner_client_id`,
    que es una opinión— pero no puede pisar el chat en el que llegó, que es un hecho
    observado: el operador lo buscó donde lo había visto y ya no estaba.
    """
    bogao = _client(db, "584267169499", "Jose Bogao")
    arianna = _client(db, "584128580852", "Arianna")
    op = _op(db, arianna, pairs, from_amount=200.0)
    pago = f.incoming(db, 200.0, phone=bogao.phone)

    svc.set_operation("incoming", pago.id, op.uuid, completing_user=operator)

    db.refresh(pago)
    # El chat de origen NO se pisa: sigue siendo un hecho observado.
    assert pago.client_phone == "584267169499"
    assert pago.owner_client_id == arianna.id

    fila = (
        db.query(WhatsAppPaymentTransfer)
        .filter(WhatsAppPaymentTransfer.incoming_payment_id == pago.id)
        .one()
    )
    assert fila.reason == PaymentTransferReason.LINKED_TO_OPERATION
    assert fila.from_client_id == bogao.id
    assert fila.from_client_phone == "584267169499"
    assert fila.from_client_name == "Jose Bogao"
    assert fila.to_client_id == arianna.id
    assert fila.created_by_user_id == operator.id


def test_linking_to_an_operation_of_the_same_client_leaves_no_trail(svc, db, client, pairs, operator):
    """Con el filtro por cliente puesto, este es el caso normal: no hay mudanza que anotar."""
    op = _op(db, client, pairs)
    pago = f.incoming(db, 100.0, phone=client.phone)

    svc.set_operation("incoming", pago.id, op.uuid, completing_user=operator)

    db.refresh(pago)
    assert pago.owner_client_id is None
    assert db.query(WhatsAppPaymentTransfer).count() == 0


def test_the_move_shows_up_in_the_payment_timeline(svc, db, pairs, operator):
    """Sin línea en la bitácora el rastro existe en la base pero el operador no lo ve."""
    bogao = _client(db, "584267169499", "Jose Bogao")
    arianna = _client(db, "584128580852", "Arianna")
    op = _op(db, arianna, pairs, from_amount=200.0)
    pago = f.incoming(db, 200.0, phone=bogao.phone)

    svc.set_operation("incoming", pago.id, op.uuid, completing_user=operator)

    items = svc.payment_timeline("incoming", pago.id)["items"]
    mudanza = next(i for i in items if i["kind"] == "TRANSFER")
    assert "Jose Bogao" in mudanza["detail"]
    assert "Arianna" in mudanza["detail"]
    assert "vinculado a la operación de otro cliente" in mudanza["detail"]
    assert mudanza["actor"] == operator.username


def test_linking_to_an_anonymous_operation_claims_nothing(svc, db, pairs, operator):
    """
    La operación anónima de un socio no tiene dueño conocido: su «cliente» es un marcador
    (`anon:partner:{uid}`). Mudarle el comprobante sería afirmar que el dinero es de un
    placeholder, así que no se toca nada ni se anota ninguna mudanza.
    """
    anonimo = _client(db, f"anon:partner:{operator.id}", "Anónimo")
    bogao = _client(db, "584267169499", "Jose Bogao")
    op = _op(db, anonimo, pairs, from_amount=200.0)
    pago = f.incoming(db, 200.0, phone=bogao.phone)

    svc.set_operation("incoming", pago.id, op.uuid, completing_user=operator)

    db.refresh(pago)
    assert pago.client_phone == "584267169499"
    assert pago.owner_client_id is None
    assert db.query(WhatsAppPaymentTransfer).count() == 0


def test_linking_an_outgoing_still_adopts_the_operation_client(svc, db, pairs, operator):
    """
    El saliente no entra en este cambio. Su comprobante lo sube el operador (ver
    `backend/CLAUDE.md`), así que su `client_phone` no es «el chat del cliente» sino la
    referencia que se adopta al vincular. Queda aquí escrito para que el alcance sea
    explícito: si algún día se unifica, este test es el que hay que cambiar a conciencia.
    """
    arianna = _client(db, "584128580852", "Arianna")
    op = _op(db, arianna, pairs, from_amount=200.0)
    saliente = f.outgoing(db, 156584.0, "VES", phone="584267169499")

    svc.set_operation("outgoing", saliente.id, op.uuid, completing_user=operator)

    db.refresh(saliente)
    assert saliente.client_phone == arianna.phone
    assert db.query(WhatsAppPaymentTransfer).count() == 0
