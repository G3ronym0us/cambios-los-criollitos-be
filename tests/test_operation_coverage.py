"""
La cobertura de una operación: varios comprobantes, una tasa que sale de la suma.

El caso que lo motiva es la operación 3898 — 350 pagados con TRES pagos móviles (6.277,
250.000 y 65.723 bolívares, que suman 322.000 = 350 a 920). Se armó desde el primero, quedó
cotizada a 900 y los otros dos no volvían a verla entre las sugeridas.

Ver `docs/superpowers/specs/2026-08-27-cobertura-de-operacion-design.md`.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f

TELEFONO = "584148861273"


@pytest.fixture
def svc(db):
    return WhatsAppPaymentService(db)


def _op(svc, db, pago, *, valor=350, salida=315_000, operator=None):
    """Una operación nacida de un comprobante de salida, como la arma el operador."""
    return f.create_op_from_payment(
        svc, "outgoing", pago, frm="ZELLE", to="VES",
        from_amount=valor, to_amount=salida, recorded_by=operator.id,
    )


def _row(db, op_dict) -> WhatsAppOperation:
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_dict["uuid"]).first()


# ---------------------------------------------------------------------------
# El resto declarado
# ---------------------------------------------------------------------------


def test_a_declared_remainder_closes_the_pending(svc, db, client, pairs, operator):
    """Declarar el resto es lo que deja cerrar un trato que los comprobantes no cubren entero."""
    pago = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, pago, salida=322_000, operator=operator)
    row = _row(db, op)
    # El trato vale 400 pero sus comprobantes sólo dan 350: los otros 50 fueron por fuera.
    row.amount = 400
    db.flush()

    assert row.dict()["pending_amount"] == pytest.approx(50.0, abs=0.01)
    row.uncovered_amount = 50.0
    row.uncovered_reason = "OTHER_CHANNEL"
    db.flush()
    assert row.dict()["pending_amount"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Leer la cobertura desde la operación
# ---------------------------------------------------------------------------


def test_coverage_lists_the_clients_free_receipts(svc, db, client, pairs, operator):
    """Los candidatos son los salientes del mismo cliente que todavía tienen saldo libre."""
    primero = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, primero, operator=operator)
    f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    f.outgoing(db, 999, "VES", phone="573000000000")
    db.flush()

    cov = svc.operation_coverage(op["uuid"])

    assert cov["value"] == pytest.approx(350.0)
    assert {c["amount"] for c in cov["candidates"]} == {250_000, 6_277}
    assert [s["payment_id"] for s in cov["settlements"]] == [primero.id]


# ---------------------------------------------------------------------------
# Un comprobante ya vinculado a OTRA operación no puede volver a ofrecerse entero
# ---------------------------------------------------------------------------
#
# El fallo real: en vivo, un comprobante ya vinculado a la operación B aparecía como
# candidato de la A. Causa: `_free_amount` sumaba `settled_amount * settled_reference_rate`
# de cada liquidación pero SALTABA las que no tenían tasa (`if s.settled_reference_rate`),
# contándolas como CERO consumido en vez de "no lo sé". Eso pasa por dos caminos: una
# liquidación de `whatsapp_outgoing_settlements` sin tasa resoluble (`_reference_rate` puede
# devolver None y aun así `set_operation_coverage`/`set_settlements` la guardan si el monto
# vino explícito), y el rastro de ANTES de esa tabla — el FK directo con
# `settled_amount`/`settled_reference_rate` puestos a mano, que `backfill_outgoing_settlements`
# no siempre migra.


def test_a_fully_consumed_receipt_is_not_a_candidate_for_another_operation(
    svc, db, client, pairs, operator
):
    """Lo que otra operación ya agotó no puede volver a ofrecerse entero libre."""
    pago = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op_a = _op(svc, db, pago, operator=operator)  # rate_used = 900 exactos (315.000 / 350)
    # Múltiplo exacto de 900 para que "cubre 300 de valor" consuma el comprobante entero sin
    # dejar centavos de redondeo — lo que interesa aquí es el criterio, no la aritmética.
    extra = f.outgoing(db, 270_000, "VES", phone=TELEFONO)
    db.flush()

    svc.set_operation_coverage(
        op_a["uuid"],
        payments=[{"payment_id": pago.id}, {"payment_id": extra.id, "settled_amount": 300}],
        partial=True,
    )

    otro_pago = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    op_b = _op(svc, db, otro_pago, valor=10, salida=6_277, operator=operator)
    db.flush()

    cov = svc.operation_coverage(op_b["uuid"])
    assert extra.id not in {c["payment_id"] for c in cov["candidates"]}


def test_a_partially_consumed_receipt_is_offered_by_its_remainder(svc, db, client, pairs, operator):
    """El reparto es parcial por diseño: lo que sobra de un comprobante sigue siendo candidato."""
    pago = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op_a = _op(svc, db, pago, operator=operator)  # rate_used queda en 900 (315.000 / 350)
    extra = f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    db.flush()

    # A medias, con `extra` aportando sólo 100 de valor (90.000 de sus 250.000 Bs).
    svc.set_operation_coverage(
        op_a["uuid"],
        payments=[{"payment_id": pago.id}, {"payment_id": extra.id, "settled_amount": 100}],
        partial=True,
    )

    otro_pago = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    op_b = _op(svc, db, otro_pago, valor=10, salida=6_277, operator=operator)
    db.flush()

    cov = svc.operation_coverage(op_b["uuid"])
    candidato = next(c for c in cov["candidates"] if c["payment_id"] == extra.id)
    assert candidato["free_amount"] == pytest.approx(250_000 - 100 * 900, abs=0.01)


def test_a_legacy_direct_link_without_a_settlement_row_is_not_a_candidate(
    svc, db, client, pairs, operator
):
    """
    Antes de `whatsapp_outgoing_settlements` un saliente se vinculaba con el FK directo y
    `settled_amount`/`settled_reference_rate` puestos a mano, sin fila en la tabla de
    reparto. `backfill_outgoing_settlements` no migra todos los casos (hay uno real en la
    base local, comprobante 56), así que ese rastro sigue vivo y `_free_amount` tiene que
    contarlo igual que si tuviera su propia fila.
    """
    pago = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op_a = _op(svc, db, pago, operator=operator)
    row_a = _row(db, op_a)

    legado = f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    legado.whatsapp_operation_id = row_a.id
    legado.settled_amount = round(250_000 / 900, 2)  # cubre el comprobante entero a la vieja tasa
    legado.settled_reference_rate = 900.0
    db.flush()

    otro_pago = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    op_b = _op(svc, db, otro_pago, valor=10, salida=6_277, operator=operator)
    db.flush()

    cov = svc.operation_coverage(op_b["uuid"])
    assert legado.id not in {c["payment_id"] for c in cov["candidates"]}


def test_a_receipt_split_on_purpose_across_two_operations_still_sums_right(
    svc, db, client, pairs, operator
):
    """
    No es "excluir todo lo que tenga cualquier vínculo": un comprobante puede cubrir a
    propósito varias operaciones (el caso de Nelson, `test_outgoing_settlements.py`), y el
    fix de arriba no puede convertir eso en "agotado" sólo por tener más de una liquidación.
    """
    a_pago = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    op_a = _row(db, _op(svc, db, a_pago, valor=10, salida=6_277, operator=operator))
    b_pago = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    op_b = _row(db, _op(svc, db, b_pago, valor=10, salida=6_277, operator=operator))

    # Un solo comprobante que paga los dos tratos enteros de una vez (el caso de Nelson):
    # cada op se lleva su valor (10) completo, y la suma agota justo los 12.554 Bs.
    compartido = f.outgoing(db, 12_554, "VES", phone=TELEFONO)  # exactamente 2 x 6.277
    db.flush()
    svc._upsert_settlement(compartido, op_a, 10.0, 627.7, operator)
    svc._upsert_settlement(compartido, op_b, 10.0, 627.7, operator)
    svc._sync_settlement_totals(compartido)
    db.flush()

    assert svc._free_amount(compartido) == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Escribir la cobertura — el caso de aceptación
# ---------------------------------------------------------------------------


def test_coverage_derives_the_rate_from_the_sum(svc, db, client, pairs, operator):
    """
    Caso 3898 al centavo: la tasa no se teclea, sale de la suma. Por eso el 920 que se cotizó
    de verdad deja de poder perderse en un número mal escrito.
    """
    a = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, a, operator=operator)
    b = f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    c = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    db.flush()

    svc.set_operation_coverage(
        op["uuid"],
        payments=[{"payment_id": a.id}, {"payment_id": b.id}, {"payment_id": c.id}],
    )

    row = _row(db, op)
    assert row.to_amount == pytest.approx(322_000, abs=0.01)
    assert row.rate_used == pytest.approx(920.0, abs=0.001)
    assert svc.delivered_amount(row) == pytest.approx(350.0, abs=0.01)
    assert row.dict()["pending_amount"] == pytest.approx(0.0, abs=0.01)


def test_partial_coverage_leaves_the_quote_alone(svc, db, client, pairs, operator):
    """A medias no se deriva nada: 65.723 sobre un valor de 350 daría una tasa de 187,78."""
    a = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, a, operator=operator)
    db.flush()

    svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": a.id}], partial=True)

    row = _row(db, op)
    assert row.to_amount == pytest.approx(315_000, abs=0.01)
    assert row.rate_used == pytest.approx(900.0, abs=0.001)
    assert row.dict()["pending_amount"] > 0


def test_a_declared_remainder_needs_a_reason(svc, db, client, pairs, operator):
    a = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, a, valor=400, salida=322_000, operator=operator)
    db.flush()

    with pytest.raises(QuoteServiceError) as exc:
        svc.set_operation_coverage(
            op["uuid"], payments=[{"payment_id": a.id}], uncovered={"amount": 50.0},
        )
    assert exc.value.code == "uncovered_needs_reason"


def test_the_remainder_is_stored_with_its_reason(svc, db, client, pairs, operator):
    a = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, a, valor=400, salida=322_000, operator=operator)
    db.flush()

    cov = svc.set_operation_coverage(
        op["uuid"],
        payments=[{"payment_id": a.id}],
        uncovered={"amount": 50.0, "reason": "OTHER_CHANNEL"},
    )

    assert cov["uncovered"] == pytest.approx(50.0)
    assert cov["uncovered_reason"] == "OTHER_CHANNEL"
    assert cov["pending"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Las dos direcciones escriben lo mismo
# ---------------------------------------------------------------------------


def test_writing_from_the_operation_reads_back_from_the_payment(svc, db, client, pairs, operator):
    """Son las MISMAS filas leídas por la otra columna: no pueden discrepar."""
    a = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, a, salida=322_000, operator=operator)
    db.flush()

    svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": a.id}])

    desde_el_pago = svc.settlement_summary(a.id)
    assert str(desde_el_pago["settlements"][0]["operation_uuid"]) == str(op["uuid"])
    assert desde_el_pago["settlements"][0]["settled_amount"] == pytest.approx(350.0, abs=0.01)


# ---------------------------------------------------------------------------
# Contra qué base se mide el margen
# ---------------------------------------------------------------------------


def test_the_margin_is_measured_against_the_rate_of_the_payment_date(
    svc, db, client, pairs, operator
):
    """
    La fecha que manda es la del PAGO, no la de la operación.

    El caso 3898 se pagó el 24 y la op se armó el 26. Contra la base del 26 el margen sale
    positivo; contra la del 24, que es cuando se pactó, sale negativo. Acá se montan dos tasas
    del par —una vieja y una de hoy, bien distintas— y se comprueba que el margen sale de la
    vieja. Es la misma convención que ya usa `create_operation_from_payment`.
    """
    from datetime import datetime, timedelta, timezone
    from app.models.exchange_rate import ExchangeRate

    hace_dos_dias = datetime.now(timezone.utc) - timedelta(days=2)
    par = pairs["ZELLE-VES"]
    # La tasa que regía cuando se pagó: base 1.000, muy lejos de la de hoy (782,92).
    db.add(
        ExchangeRate(
            currency_pair_id=par.id, from_currency="ZELLE", to_currency="VES",
            rate=1_000.0, is_active=True, created_at=hace_dos_dias,
        )
    )
    db.flush()

    pago = f.outgoing(db, 322_000, "VES", phone=TELEFONO, created_at=hace_dos_dias)
    op = _op(svc, db, pago, salida=322_000, operator=operator)
    db.flush()

    svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": pago.id}])

    row = _row(db, op)
    assert row.rate_used == pytest.approx(920.0, abs=0.001)
    # (1 - 920/1000) x 100 = 8%. Contra la tasa de hoy (782,92) saldría negativo y enorme.
    assert row.applied_percentage == pytest.approx(8.0, abs=0.01)
