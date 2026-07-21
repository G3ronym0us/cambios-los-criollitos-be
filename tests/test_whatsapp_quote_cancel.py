"""Pruebas de transiciones explícitas del ciclo de operaciones."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.whatsapp_operation import WhatsAppOperationStatus
from app.schemas.whatsapp import WhatsAppOperationCreate
from app.services.whatsapp_quote_service import WhatsAppQuoteService


class _CreateDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def refresh(self, _value):
        pass


@pytest.mark.parametrize(
    ("notes", "expected_status"),
    [
        (None, WhatsAppOperationStatus.QUOTED),
        ("0102\nV18132409\n04241387346", WhatsAppOperationStatus.PENDING),
    ],
)
def test_quote_initial_status_depends_on_payment_data(notes, expected_status):
    db = _CreateDB()
    client = SimpleNamespace(id=2, is_blocked=False, is_usdt_authorized=True)
    pair = SimpleNamespace(id=3)
    entry = SimpleNamespace(
        rate=750.0,
        inverse_percentage=False,
        base_rate=800.0,
        base_percentage=6.25,
    )
    service = SimpleNamespace(
        db=db,
        upsert_client=lambda *_args: client,
        pair_repo=SimpleNamespace(get_by_symbol=lambda _symbol: pair),
        resolver=SimpleNamespace(
            get_rate_entry_for_pair=lambda *_args: entry,
            apply_rate=lambda amount, rate, inverse: amount / rate if inverse else amount * rate,
        ),
        # El par de este test no configura redondeo: passthrough. El redondeo en sí
        # se cubre en test_whatsapp_rounding.py.
        _apply_pair_rounding=lambda _pair, _qfrom, _qto, _side, from_amount, to_amount, rate, inverse: (
            from_amount,
            to_amount,
            rate,
            inverse,
        ),
    )

    op = WhatsAppQuoteService.create_quote(
        service,
        WhatsAppOperationCreate(
            client_phone="584121234567",
            from_currency="ZELLE",
            to_currency="VES",
            amount=100,
            notes=notes,
        ),
    )

    assert op.status == expected_status
    assert (op.approved_at is not None) == (expected_status == WhatsAppOperationStatus.PENDING)
    assert db.commits == 1


# ---------- restore_quote (reversión de corrección) ----------

class _FakeDBSingle:
    """DB stub para restore_quote: _get_op_or_404 devuelve un único op fijo."""

    def __init__(self, op):
        self._op = op
        self.committed = False

    def commit(self):
        self.committed = True

    def refresh(self, _op):
        pass


def _restore(op):
    service = SimpleNamespace(
        db=_FakeDBSingle(op),
        _get_op_or_404=lambda _uuid: op,
        _sync_linked_transaction=lambda _op: None,
    )
    return WhatsAppQuoteService.restore_quote(service, op_uuid=None)


def test_restore_cancelled_to_quoted():
    op = SimpleNamespace(
        status=WhatsAppOperationStatus.CANCELLED,
        cancelled_at=datetime.now(timezone.utc),
        expires_at=None,
    )
    _restore(op)
    assert op.status == WhatsAppOperationStatus.QUOTED
    assert op.cancelled_at is None
    assert op.expires_at is not None  # se refresca el TTL


def test_restore_is_idempotent_when_already_quoted():
    op = SimpleNamespace(
        status=WhatsAppOperationStatus.QUOTED,
        cancelled_at=None,
        expires_at=None,
    )
    _restore(op)
    assert op.status == WhatsAppOperationStatus.QUOTED


def test_restore_rejects_completed():
    import pytest
    from app.services.whatsapp_quote_service import QuoteServiceError

    op = SimpleNamespace(
        status=WhatsAppOperationStatus.COMPLETED,
        cancelled_at=None,
        expires_at=None,
    )
    with pytest.raises(QuoteServiceError):
        _restore(op)
