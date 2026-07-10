"""
Tests de _cancel_previous_quoted — al crear una cotización nueva, solo se
cancelan las QUOTED SIN datos de pago (notes vacío). Las QUOTED que ya tienen
destinatario (notes) son operaciones distintas en curso (multi-operación en la
misma conversación) y deben sobrevivir.

Paridad con el bot local: whatsapp-bot/src/operations.ts::cancelPreviousQuoted
(filtra notes IS NULL).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.whatsapp_operation import WhatsAppOperationStatus
from app.services.whatsapp_quote_service import WhatsAppQuoteService


class _FakeQuery:
    """Stub de self.db.query(...).filter(...).all() → lista fija de ops."""

    def __init__(self, ops):
        self._ops = ops

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._ops


class _FakeDB:
    def __init__(self, ops):
        self._ops = ops

    def query(self, *args, **kwargs):
        return _FakeQuery(self._ops)


def _op(notes):
    return SimpleNamespace(
        notes=notes,
        status=WhatsAppOperationStatus.QUOTED,
        cancelled_at=None,
    )


def _run(ops):
    service = SimpleNamespace(db=_FakeDB(ops))
    # Llamamos al método sin instanciar el servicio (evita dependencias del __init__).
    WhatsAppQuoteService._cancel_previous_quoted(service, client_id=1)
    return ops


def test_cancels_quoted_without_notes():
    op = _op(None)
    _run([op])
    assert op.status == WhatsAppOperationStatus.CANCELLED
    assert op.cancelled_at is not None


def test_cancels_quoted_with_empty_notes():
    op = _op("   ")
    _run([op])
    assert op.status == WhatsAppOperationStatus.CANCELLED


def test_preserves_quoted_with_notes():
    op = _op("0105\nV11725538\n04127706986")
    _run([op])
    assert op.status == WhatsAppOperationStatus.QUOTED
    assert op.cancelled_at is None


def test_multi_op_only_cancels_the_dangling_quote():
    # Caso real: op1 (130, con banco 0105) + una cotización suelta sin datos.
    # Al crear una tercera, solo la suelta debe caer; la de 130 sobrevive.
    with_notes = _op("0105\nV11725538\n04127706986")
    dangling = _op(None)
    _run([with_notes, dangling])
    assert with_notes.status == WhatsAppOperationStatus.QUOTED
    assert dangling.status == WhatsAppOperationStatus.CANCELLED


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
