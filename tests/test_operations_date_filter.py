"""
Filtrar el listado de operaciones por fecha, contra Postgres real.

El rango va por la MISMA fecha por la que se ordena: con `order_by=paid` (el del listado del
admin) la del comprobante de salida, y si no hay, la de la operación.
"""

from datetime import date, datetime, timedelta, timezone

from app.core.timezones import day_bounds
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import WhatsAppQuoteService
from tests import factories as f


def _op(db, client, pair, created_at):
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pair.id, from_amount=100, to_amount=78292,
        rate_used=782.92, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING, created_at=created_at, quoted_at=created_at,
        expires_at=created_at + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    return op


def _ids(db, order_by, frm, to):
    start, end = day_bounds(frm, to)
    ops, total = WhatsAppQuoteService(db).list_operations(
        order_by=order_by, date_from=start, date_to=end, limit=500
    )
    return {o.id for o in ops}, total


def test_filters_by_the_operation_date(db, pairs, client):
    # 15:00 UTC = 11:00 en Caracas: cae en el día de calendario que dice la fecha.
    sept_12 = _op(db, client, pairs["ZELLE-VES"], datetime(2026, 9, 12, 15, tzinfo=timezone.utc))
    sept_14 = _op(db, client, pairs["ZELLE-VES"], datetime(2026, 9, 14, 15, tzinfo=timezone.utc))

    ids, total = _ids(db, "created", date(2026, 9, 12), date(2026, 9, 12))
    assert sept_12.id in ids and sept_14.id not in ids

    ids, _ = _ids(db, "created", date(2026, 9, 12), date(2026, 9, 14))
    assert {sept_12.id, sept_14.id} <= ids


def test_the_last_day_counts_in_caracas_time(db, pairs, client):
    """23:30 del 12-09 en Caracas son las 03:30 UTC del 13: sigue siendo del 12."""
    late = _op(db, client, pairs["ZELLE-VES"], datetime(2026, 9, 13, 3, 30, tzinfo=timezone.utc))
    ids, _ = _ids(db, "created", date(2026, 9, 12), date(2026, 9, 12))
    assert late.id in ids


def test_with_paid_order_it_filters_by_the_payment_date(db, pairs, client):
    """Registrada hoy, pagada el 12-09: en el listado por pago es del 12-09."""
    op = _op(db, client, pairs["ZELLE-VES"], datetime.now(timezone.utc))
    out = f.outgoing(
        db, 78292, "VES", phone=client.phone,
        created_at=datetime(2026, 9, 12, 15, tzinfo=timezone.utc),
    )
    WhatsAppPaymentService(db).set_operation("outgoing", out.id, op.uuid, completing_user=None)

    ids, _ = _ids(db, "paid", date(2026, 9, 12), date(2026, 9, 12))
    assert op.id in ids
    ids, _ = _ids(db, "created", date(2026, 9, 12), date(2026, 9, 12))
    assert op.id not in ids
