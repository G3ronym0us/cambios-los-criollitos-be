"""
`OperationMatchService.rank_for_payment` como pagina filtrada, puntuada y ordenada -el
diseno que reemplaza a `POST /operations/match` de "puntua un lote global fijo" a "el mismo
contrato que `GET /operations` (phone/search/status/page/limit) mas `order_by`".

Antes esto era responsabilidad del navegador (`sortScored` + `buildOperationQuery` en
`LinkOperationPanel.tsx`, frontend): los tres botones "sugerida"/"monto"/"hora" ordenaban en
memoria y el corte a 60 filas era puro pintado. Ahora el backend filtra, puntua, ordena Y
pagina - estos tests prueban justo eso: que cada orden ordena de verdad, que el filtro por
cliente acota el POOL que se puntua (no solo lo que se pinta), que paginar no pierde ni repite
filas, y que la sugerida sigue encabezando el modo por defecto.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.whatsapp_operation import WhatsAppOperation
from app.services.operation_match_service import OperationMatchService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def svc(db):
    return WhatsAppPaymentService(db)


def _row(db, op_dict):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_dict["uuid"]).first()


def _make_op(svc, db, *, phone, to_amount, created_at, from_amount=100.0, operator):
    inc = f.incoming(db, from_amount, "ZELLE", phone=phone, created_at=created_at)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm="ZELLE", to="VES",
        from_amount=from_amount, to_amount=to_amount, recorded_by=operator.id,
    )
    row = _row(db, created)
    row.created_at = created_at
    db.flush()
    return row


def _outgoing_payment(db, *, phone, amount, created_at, currency="VES"):
    return f.outgoing(db, amount, currency, phone=phone, created_at=created_at)


def test_phone_filter_scopes_the_pool_being_scored(db, fund, pairs, operator):
    """
    Antes `rank_for_payment` cargaba las 500 operaciones mas recientes de TODO el sistema, sin
    mirar de que cliente era el comprobante: una operacion fuera de esa ventana global no se
    puntuaba aunque fuera exacta para el cliente que pago. Con `phone` el filtro va en el
    WHERE de la propia consulta, asi que el tope de seguridad (`MATCH_POOL_LIMIT`) muerde
    sobre el historial YA filtrado del cliente, no sobre el del sistema entero.
    """
    a_phone, b_phone = "584140000001", "584140000002"
    svc = WhatsAppPaymentService(db)
    op_a1 = _make_op(svc, db, phone=a_phone, to_amount=1000.0, created_at=NOW, operator=operator)
    op_a2 = _make_op(svc, db, phone=a_phone, to_amount=2000.0, created_at=NOW, operator=operator)
    # El cliente B tiene una op que calzaria MEJOR que las de A (monto exacto), pero no es de
    # quien mando este comprobante: no puede colarse en el pool filtrado por phone=a_phone.
    _make_op(svc, db, phone=b_phone, to_amount=1000.0, created_at=NOW, operator=operator)

    pago = _outgoing_payment(db, phone=a_phone, amount=1000.0, created_at=NOW)
    db.flush()

    result = OperationMatchService(db).rank_for_payment(pago.id, "outgoing", phone=a_phone)

    assert result.total == 2, "el total tiene que ser el del cliente, no el del sistema"
    uuids = {str(op.uuid) for op, _ in result.items}
    assert uuids == {str(op_a1.uuid), str(op_a2.uuid)}


def test_search_filter_also_scopes_the_pool(db, fund, pairs, operator):
    """search (nombre/telefono) filtra igual que en GET /operations."""
    svc = WhatsAppPaymentService(db)
    phone = "584140000003"
    op_a = _make_op(svc, db, phone=phone, to_amount=1000.0, created_at=NOW, operator=operator)
    _make_op(svc, db, phone="584140000004", to_amount=1000.0, created_at=NOW, operator=operator)

    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    result = OperationMatchService(db).rank_for_payment(pago.id, "outgoing", search=phone)

    assert result.total == 1
    assert str(result.items[0][0].uuid) == str(op_a.uuid)


def test_time_order_sorts_by_recency_desc(db, fund, pairs, operator):
    svc = WhatsAppPaymentService(db)
    phone = "584140000010"
    vieja = _make_op(svc, db, phone=phone, to_amount=5000.0, created_at=NOW - timedelta(hours=48), operator=operator)
    media = _make_op(svc, db, phone=phone, to_amount=5000.0, created_at=NOW - timedelta(hours=5), operator=operator)
    reciente = _make_op(svc, db, phone=phone, to_amount=5000.0, created_at=NOW, operator=operator)

    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    result = OperationMatchService(db).rank_for_payment(pago.id, "outgoing", phone=phone, order_by="time")

    assert [str(op.uuid) for op, _ in result.items] == [
        str(reciente.uuid), str(media.uuid), str(vieja.uuid),
    ]


def test_amount_order_sorts_by_relative_closeness(db, fund, pairs, operator):
    """
    Ordena por cercania al monto del comprobante (score.relative), sin mirar la hora -la mas
    vieja de las tres es la que mas se acerca y tiene que salir primero.
    """
    svc = WhatsAppPaymentService(db)
    phone = "584140000011"
    lejos = _make_op(svc, db, phone=phone, to_amount=1300.0, created_at=NOW, operator=operator)
    media = _make_op(svc, db, phone=phone, to_amount=1100.0, created_at=NOW, operator=operator)
    cerca = _make_op(svc, db, phone=phone, to_amount=1005.0, created_at=NOW - timedelta(hours=200), operator=operator)

    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    result = OperationMatchService(db).rank_for_payment(pago.id, "outgoing", phone=phone, order_by="amount")

    assert [str(op.uuid) for op, _ in result.items] == [
        str(cerca.uuid), str(media.uuid), str(lejos.uuid),
    ]


def test_suggested_order_puts_the_confident_suggestion_first(db, fund, pairs, operator):
    """
    El modo por defecto ("sugerida") sube la candidata sugerida al frente aunque su puntaje
    CRUDO no sea el mas alto del lote. Puede pasar porque pick_suggestion solo compite entre
    las candidatas DENTRO de tolerancia (+-1%), mientras que el orden crudo de rank_candidates
    no distingue eso: una candidata FUERA de tolerancia pero muy reciente puede sacar mas
    puntaje que una DENTRO de tolerancia pero vieja. El front lo resolvia "subiendo" la
    sugerida a mano (sortScored); aqui se prueba que el backend hace lo mismo.
    """
    svc = WhatsAppPaymentService(db)
    phone = "584140000012"
    # Dentro de tolerancia (0,9% de diferencia) pero muy vieja.
    la_sugerida = _make_op(
        svc, db, phone=phone, to_amount=991.0, created_at=NOW - timedelta(hours=100), operator=operator,
    )
    # Fuera de tolerancia (1,5%) pero recien nacida: gana en puntaje crudo, no en elegibilidad.
    la_mas_reciente = _make_op(
        svc, db, phone=phone, to_amount=985.0, created_at=NOW, operator=operator,
    )

    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    result = OperationMatchService(db).rank_for_payment(pago.id, "outgoing", phone=phone)

    assert result.suggestion is not None and result.suggestion.confident
    assert str(result.suggestion.uuid) == str(la_sugerida.uuid)
    # La prueba real: el ORDEN por defecto la pone primero, no solo el campo suggestion.
    assert str(result.items[0][0].uuid) == str(la_sugerida.uuid)
    assert str(result.items[1][0].uuid) == str(la_mas_reciente.uuid)


def test_pagination_does_not_skip_or_repeat_rows(db, fund, pairs, operator):
    svc = WhatsAppPaymentService(db)
    phone = "584140000020"
    ops = [
        _make_op(
            svc, db, phone=phone, to_amount=5000.0 + i,
            created_at=NOW - timedelta(minutes=i), operator=operator,
        )
        for i in range(7)
    ]
    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    service = OperationMatchService(db)
    full = service.rank_for_payment(pago.id, "outgoing", phone=phone, order_by="time", limit=100)
    assert full.total == 7 and len(full.items) == 7

    paged_uuids = []
    for page in (1, 2, 3):
        result = service.rank_for_payment(pago.id, "outgoing", phone=phone, order_by="time", page=page, limit=3)
        assert result.total == 7
        paged_uuids.extend(str(op.uuid) for op, _ in result.items)

    assert paged_uuids == [str(op.uuid) for op, _ in full.items]
    assert len(paged_uuids) == len(set(paged_uuids)) == 7
    assert {str(o.uuid) for o in ops} == set(paged_uuids)


def test_a_page_past_the_end_is_empty_but_total_still_reports(db, fund, pairs, operator):
    svc = WhatsAppPaymentService(db)
    phone = "584140000021"
    _make_op(svc, db, phone=phone, to_amount=5000.0, created_at=NOW, operator=operator)
    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    result = OperationMatchService(db).rank_for_payment(pago.id, "outgoing", phone=phone, page=5, limit=10)
    assert result.items == []
    assert result.total == 1


# ---------------------------------------------------------------------------
# El contrato HTTP: lo que consume el front (POST /operations/match)
# ---------------------------------------------------------------------------


def test_match_endpoint_returns_operations_with_scores_paginated(db, fund, pairs, operator):
    """
    Fija la forma exacta de la respuesta para quien implemente el front: `items` trae la
    operación completa (lo que antes salía de `GET /operations`) junto a `score` (lo que
    antes salía de `candidates`), más `total`/`page`/`limit` para el pie del cajón.
    """
    from app.core.dependencies import get_current_user
    from app.database.connection import get_db
    from app.main import app
    from starlette.testclient import TestClient

    svc = WhatsAppPaymentService(db)
    phone = "584140000030"
    op_a = _make_op(svc, db, phone=phone, to_amount=1000.0, created_at=NOW, operator=operator)
    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: operator
    try:
        with TestClient(app, raise_server_exceptions=False) as api:
            r = api.post(
                "/operations/match",
                json={
                    "payment_id": pago.id,
                    "table": "outgoing",
                    "phone": phone,
                    "order_by": "suggested",
                    "page": 1,
                    "limit": 50,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["page"] == 1 and body["limit"] == 50
    assert body["suggestion"] == {"uuid": str(op_a.uuid), "confident": True}
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["operation"]["uuid"] == str(op_a.uuid)
    assert item["operation"]["from_amount"] == pytest.approx(100.0)
    assert item["score"]["within_tolerance"] is True


def test_el_contrato_viejo_sigue_respondido(db, fund, pairs, operator):
    """
    `candidates` sigue poblado aunque ya nadie deba usarlo.

    El front que está en producción hace `match?.candidates ?? []`. Si el endpoint deja de
    mandarlo mientras ese front sigue vivo —y sigue vivo durante todo el despliegue, porque
    el backend recarga al instante y Vercel tarda minutos— se queda sin el sello «SUGERIDA»
    y sin el orden por monto **en silencio**: una lista vacía no da error.

    Se borran el campo y este test cuando el front nuevo esté desplegado en todas partes.
    """
    from app.core.dependencies import get_current_user
    from app.database.connection import get_db
    from app.main import app
    from starlette.testclient import TestClient

    svc = WhatsAppPaymentService(db)
    phone = "584140000031"
    _make_op(svc, db, phone=phone, to_amount=1000.0, created_at=NOW, operator=operator)
    _make_op(svc, db, phone=phone, to_amount=2000.0, created_at=NOW, operator=operator)
    pago = _outgoing_payment(db, phone=phone, amount=1000.0, created_at=NOW)
    db.flush()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: operator
    try:
        with TestClient(app, raise_server_exceptions=False) as api:
            r = api.post(
                "/operations/match",
                json={"payment_id": pago.id, "table": "outgoing", "phone": phone},
            )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidates"], "el alias en desuso no puede llegar vacío"
    # Y tiene que decir lo mismo que `items`, en el mismo orden: si divergen, el front viejo
    # y el nuevo pintarían cosas distintas sobre la misma respuesta.
    assert [c["uuid"] for c in body["candidates"]] == [i["score"]["uuid"] for i in body["items"]]
