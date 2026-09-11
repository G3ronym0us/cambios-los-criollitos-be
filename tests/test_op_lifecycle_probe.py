"""
Sondas del agente 2 (ciclo de vida de la operación) para la campaña de ruptura nocturna.

No es la suite oficial: son reproducciones puntuales de hipótesis sobre la máquina de
estados de WhatsAppOperation (cobertura, cierre, reapertura, estado administrativo).
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import WhatsAppQuoteService
from tests import factories as f

TELEFONO = "584148861273"


@pytest.fixture
def svc(db):
    return WhatsAppPaymentService(db)


@pytest.fixture
def quote_svc(db):
    return WhatsAppQuoteService(db)


def _op(svc, db, pago, *, valor=350, salida=315_000, operator=None):
    return f.create_op_from_payment(
        svc, "outgoing", pago, frm="ZELLE", to="VES",
        from_amount=valor, to_amount=salida, recorded_by=operator.id,
    )


def _row(db, op_dict) -> WhatsAppOperation:
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_dict["uuid"]).first()


# ---------------------------------------------------------------------------
# H-2.1: reabrir/cerrar cobertura de una op COMPLETED no resincroniza su Transaction
# ---------------------------------------------------------------------------


def test_reclosing_coverage_on_a_completed_op_leaves_the_transaction_stale(
    svc, db, client, pairs, operator
):
    """
    `create_operation_from_payment` completa la op de una vez (salientes no-USD). Si después
    llegan más comprobantes y se vuelve a cerrar la cobertura, la tasa/margen de la OPERACIÓN
    cambia (es el caso de aceptación de test_operation_coverage.py) pero la Transaction ya
    generada -la que de verdad contabiliza la ganancia y alimenta profit splits/fondo- se
    queda con los números viejos: nadie la resincroniza.
    """
    a = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, a, operator=operator)
    row = _row(db, op)
    assert row.status == WhatsAppOperationStatus.COMPLETED
    tx = row.transaction
    assert tx is not None
    pct_before = tx.total_profit_percentage
    profit_before = tx.profit_amount

    b = f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    c = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    db.flush()

    svc.set_operation_coverage(
        op["uuid"],
        payments=[{"payment_id": a.id}, {"payment_id": b.id}, {"payment_id": c.id}],
    )

    db.refresh(row)
    db.refresh(tx)
    # La op sí cambió: de 900 a 920 (caso 3898).
    assert row.rate_used == pytest.approx(920.0, abs=0.001)
    assert row.status == WhatsAppOperationStatus.COMPLETED

    # FIX: la transacción se resincroniza al cerrar/reabrir la cobertura. Antes del fix
    # (`_sync_linked_transaction`/`_sync_fund_legs` ausentes al final de
    # `set_operation_coverage`) estos valores se quedaban iguales a `pct_before`/
    # `profit_before` -la transacción mentía sobre el margen real de la operación.
    assert tx.total_profit_percentage != pytest.approx(pct_before)
    assert tx.profit_amount != pytest.approx(profit_before)


# ---------------------------------------------------------------------------
# H-2.2: una segunda llamada a set_operation_coverage que no repite los comprobantes
# anteriores deriva la tasa SOLO de los nuevos, mientras el `delivered_amount` real sigue
# contando también los viejos: to_amount/rate_used quedan por debajo de lo entregado.
# ---------------------------------------------------------------------------


def test_a_coverage_call_that_omits_a_previously_settled_receipt_drops_it_instead_of_double_counting(
    svc, db, client, pairs, operator
):
    """
    `OperationCoverageUpdate` documenta el contrato: "es el conjunto COMPLETO, no deltas".
    `a` quedó cubriendo la operación desde que se creó (fuera de `set_operation_coverage`,
    vía `_apply_settlement` al armarla). Si luego se cierra la cobertura con sólo `b` -sin
    repetir `a`- el contrato dice que `a` DEJA de cubrirla: su settlement se suelta (y
    vuelve a estar libre para otra operación), y `to_amount`/`rate_used` sólo responden por
    lo que de verdad quedó cubriendo, sin arrastrar restos que ya no están en la lista.
    """
    a = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, a, operator=operator)  # rate_used = 900 (315.000 / 350), settlement de "a" ya existe
    row = _row(db, op)
    delivered_before = svc.delivered_amount(row)
    # "a" ya deja la op con parte cubierta (65.723 / 900 = 73,03 de valor), sin pasar por
    # `set_operation_coverage` -la liquida `_apply_settlement` al crearla.
    assert delivered_before == pytest.approx(73.03, abs=0.01)

    b = f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    db.flush()

    # El operador cierra la cobertura con el conjunto completo real: sólo "b" (p.ej. "a"
    # resultó ser de otro cliente y se desvinculó a mano en otra pantalla).
    cov = svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": b.id}])

    db.refresh(row)
    # FIX: "a" quedó fuera del conjunto -su settlement se soltó- y ya no se cuenta.
    assert {s["payment_id"] for s in cov["settlements"]} == {b.id}
    assert svc.delivered_amount(row) == pytest.approx(cov["delivered"], abs=0.01)
    # "a" vuelve a estar libre: ya no tiene settlement en esta operación.
    assert svc._free_amount(a) == pytest.approx(65_723.0, abs=0.01)
    # to_amount/rate_used responden por lo que de verdad quedó cubriendo (sólo "b").
    assert row.to_amount == pytest.approx(250_000.0, abs=0.01)


# ---------------------------------------------------------------------------
# H-2.3: un comprobante con monto real 0 (OCR fallido) más un settled_amount explícito
# pasa el guard "cubre <= 0" (que mira el explícito) pero la tasa se deriva de la SUMA de
# los montos reales de los comprobantes, que sí incluye el 0: rate_used/to_amount = 0.
# ---------------------------------------------------------------------------


def test_coverage_with_a_zero_face_amount_receipt_and_an_explicit_override_zeroes_the_rate(
    svc, quote_svc, db, client, pairs, operator, fund
):
    """
    Una op QUOTED/PENDING (sin comprobante aún, `create_quote`) a la que se le cierra la
    cobertura con un comprobante cuyo OCR quedó en 0 -pasa igual, es real: fotos borrosas,
    capturas recortadas- pero con un `settled_amount` puesto A MANO por el operador.
    """
    from app.schemas.whatsapp import WhatsAppOperationCreate

    payload = WhatsAppOperationCreate(
        client_phone=TELEFONO, client_display_name="Naldin",
        from_currency="ZELLE", to_currency="VES", amount=200, amount_side="SEND",
        notes="datos de pago",
    )
    op = quote_svc.create_quote(payload)
    row = _row(db, {"uuid": op.uuid})

    z = f.outgoing(db, 0, "VES", phone=TELEFONO)  # comprobante con el OCR en cero
    db.flush()

    from app.services.whatsapp_quote_service import QuoteServiceError

    # FIX: la derivación rechaza sumar 0 en vez de dejar la op "cerrada" a tasa 0.
    with pytest.raises(QuoteServiceError) as exc:
        svc.set_operation_coverage(
            str(op.uuid),
            payments=[{"payment_id": z.id, "settled_amount": 200}],
        )
    assert exc.value.code == "coverage_sums_to_zero"

    db.refresh(row)
    # Nada se guardó: ni el settlement fantasma ni la tasa en 0.
    assert svc.delivered_amount(row) == pytest.approx(0.0, abs=0.01)
    assert row.rate_used != 0


# ---------------------------------------------------------------------------
# H-2.5: un comprobante con monto REAL 0 (OCR que no leyó nada) se trataba como "cubre
# el pago entero" al armar la operación desde él (`_covers_whole_payout` comparaba con
# `not payment.amount`, y 0 es falsy igual que None).
# ---------------------------------------------------------------------------


def test_a_zero_amount_receipt_does_not_auto_cover_the_whole_operation_at_creation(
    svc, db, client, pairs, operator
):
    from app.services.whatsapp_quote_service import QuoteServiceError

    z = f.outgoing(db, 0, "VES", phone=TELEFONO)  # el OCR no leyó ningún monto
    # FIX: antes, `not payment.amount` trataba 0 igual que "sin monto para comparar" y
    # `_apply_settlement` caía a "cubre todo lo pendiente" (200): la op nacía COMPLETED y
    # "pagada" de un comprobante que en realidad no demuestra haber pagado nada. Ahora se
    # rechaza en vez de inventar una cobertura.
    with pytest.raises(QuoteServiceError) as exc:
        f.create_op_from_payment(
            svc, "outgoing", z, frm="ZELLE", to="VES",
            from_amount=200, to_amount=157_000, recorded_by=operator.id,
        )
    assert exc.value.code == "invalid_settled_amount"


# ---------------------------------------------------------------------------
# H-2.6: reasignar el fondo de una operación COMPLETED por `update_operation`
# (PATCH /operations/{uuid}, el editor combinado de cliente/par/escenario) no resincroniza
# los movimientos de fondo -a diferencia de `set_scenario`, que sí lo hace para el mismo
# campo-. La operación pasa a decir un fondo nuevo mientras el libro sigue con el viejo.
# ---------------------------------------------------------------------------


def test_reassigning_the_fund_group_of_a_completed_op_via_update_operation_keeps_the_ledger_in_sync(
    quote_svc, db, client, pairs, operator, fund
):
    from app.models.fund import FundGroup, FundGroupMember, FundMovement, FundMovementType
    from app.schemas.whatsapp import WhatsAppOperationCreate, WhatsAppOperationUpdate

    payload = WhatsAppOperationCreate(
        client_phone=TELEFONO, client_display_name="Naldin",
        from_currency="ZELLE", to_currency="VES", amount=100, amount_side="SEND",
        notes="datos de pago",
    )
    # "fund" es el ÚNICO fondo USD activo al nacer -con dos, `get_active_group_by_currency`
    # no adivina y la deja sin fondo (ver el propio repositorio); por eso `fund_b` se crea
    # DESPUÉS, cuando ya no puede interferir con la resolución automática.
    op = quote_svc.create_quote(payload)
    db.refresh(op)
    assert op.fund_group_id == fund.id

    quote_svc.update_status(op.uuid, "COMPLETED", operator)
    db.refresh(op)
    assert op.status == WhatsAppOperationStatus.COMPLETED

    fund_b = FundGroup(name="Zelle/Paypal B", currency="USD", is_active=True)
    db.add(fund_b)
    db.flush()
    db.add(FundGroupMember(group_id=fund_b.id, user_id=operator.id, is_fund_manager=True))
    db.flush()

    movement_before = (
        db.query(FundMovement)
        .filter(
            FundMovement.transaction_id == op.transaction_id,
            FundMovement.movement_type == FundMovementType.EXCHANGE_IN,
        )
        .first()
    )
    assert movement_before is not None
    assert movement_before.group_id == fund.id

    # El operador corrige el fondo desde el editor combinado (par/cliente/escenario).
    quote_svc.update_operation(
        op.uuid, WhatsAppOperationUpdate(fund_group_uuid=fund_b.uuid), operator,
    )
    db.refresh(op)
    assert op.fund_group_id == fund_b.id

    db.expire_all()
    movement_after = (
        db.query(FundMovement)
        .filter(
            FundMovement.transaction_id == op.transaction_id,
            FundMovement.movement_type == FundMovementType.EXCHANGE_IN,
        )
        .first()
    )
    # FIX: el movimiento sigue al fondo que la operación dice ahora. Antes del fix
    # (`update_operation` no llamaba a `_sync_fund_legs`) esto seguía apuntando a `fund.id`
    # -el fondo viejo- aunque la operación ya dijera `fund_b`.
    assert movement_after.group_id == fund_b.id


def test_update_status_resurrects_a_cancelled_operation_straight_into_completed(
    quote_svc, db, client, pairs, operator, fund
):
    from app.schemas.whatsapp import WhatsAppOperationCreate, WhatsAppOperationCancel

    payload = WhatsAppOperationCreate(
        client_phone=TELEFONO,
        client_display_name="Naldin",
        from_currency="ZELLE",
        to_currency="VES",
        amount=100,
        amount_side="SEND",
        notes="datos de pago",
    )
    op = quote_svc.create_quote(payload)
    assert op.status == WhatsAppOperationStatus.PENDING

    quote_svc.cancel_operation(op.uuid, WhatsAppOperationCancel(reason="cliente se arrepintió"))
    db.refresh(op)
    assert op.status == WhatsAppOperationStatus.CANCELLED

    from app.services.whatsapp_quote_service import QuoteServiceError

    # FIX: `update_status` ya no deja saltar de CANCELLED directo a COMPLETED (ni a
    # PENDING); el único camino de vuelta es `restore_quote` (-> QUOTED).
    with pytest.raises(QuoteServiceError) as exc:
        quote_svc.update_status(op.uuid, "COMPLETED", operator)
    assert exc.value.code == "cancelled_must_be_restored_first"
    db.refresh(op)
    assert op.status == WhatsAppOperationStatus.CANCELLED
    assert op.transaction_id is None

    # El camino sancionado sigue funcionando: CANCELLED -> QUOTED (restore) -> COMPLETED.
    quote_svc.restore_quote(op.uuid)
    db.refresh(op)
    assert op.status == WhatsAppOperationStatus.QUOTED
    result = quote_svc.update_status(op.uuid, "COMPLETED", operator)
    assert result.status == WhatsAppOperationStatus.COMPLETED
    assert result.transaction_id is not None
