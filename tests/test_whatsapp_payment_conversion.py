from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.fund import FundMovement
from app.models.whatsapp_operation import WhatsAppOperationStatus
from app.models.whatsapp_balance import WhatsAppBalanceEntry
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
import app.services.whatsapp_payment_service as payment_service_module
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError


def _payment(**overrides):
    values = {
        "client_phone": "584121234567",
        "provider": "PAGO_MOVIL",
        "amount": 4000.0,
        "currency": "VES",
        "bank_from": "0102",
        "bank_to": "0105",
        "account_number": None,
        "identification": "V12345678",
        "phone_to": "04121234567",
        "reference": "123456",
        "raw_text": "comprobante",
        "whatsapp_operation_id": 42,
        "corrected_at": None,
        "correction_original": None,
        "created_at": datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Query:
    def __init__(self, result=None):
        self.result = result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.result


class _DB:
    def __init__(self, results=None):
        self.results = results or {}
        self.added = []
        self.deleted = []
        self.commits = 0

    def query(self, model):
        return _Query(self.results.get(model))

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.commits += 1

    def refresh(self, _value):
        pass


def _service(db, payment):
    service = WhatsAppPaymentService.__new__(WhatsAppPaymentService)
    service.db = db
    service._get_or_404 = lambda _table, _payment_id: payment
    service._assert_not_loan = lambda _payment_id: None
    service._with_name = lambda converted: converted
    return service


def test_convert_outgoing_to_incoming_preserves_receipt_date_and_operation():
    outgoing = _payment()
    db = _DB()
    service = _service(db, outgoing)

    converted = service.convert_outgoing_to_incoming(7)

    assert isinstance(converted, WhatsAppIncomingPayment)
    assert converted.amount == outgoing.amount
    assert converted.created_at == outgoing.created_at
    assert converted.whatsapp_operation_id == outgoing.whatsapp_operation_id
    assert converted.fund_group_id is None
    assert db.deleted == [outgoing]
    assert db.commits == 1


def test_convert_incoming_to_outgoing_preserves_receipt_date_and_operation():
    incoming = _payment(fund_group_id=None)
    db = _DB()
    service = _service(db, incoming)

    converted = service.convert_incoming_to_outgoing(8)

    assert isinstance(converted, WhatsAppOutgoingPayment)
    assert converted.amount == incoming.amount
    assert converted.created_at == incoming.created_at
    assert converted.whatsapp_operation_id == incoming.whatsapp_operation_id
    assert db.deleted == [incoming]
    assert db.commits == 1


@pytest.mark.parametrize(
    ("model", "code"),
    [
        (FundMovement.id, "incoming_has_deposit"),
        (WhatsAppBalanceEntry.id, "incoming_has_balance_credit"),
        (WhatsAppOutgoingPayment.id, "incoming_is_payment_source"),
    ],
)
def test_convert_incoming_rejects_already_accounted_payments(model, code):
    incoming = _payment(fund_group_id=None)
    db = _DB({model: SimpleNamespace(id=1)})
    service = _service(db, incoming)

    with pytest.raises(QuoteServiceError) as exc:
        service.convert_incoming_to_outgoing(8)

    assert exc.value.code == code
    assert db.commits == 0


def test_create_operation_from_incoming_payment_stays_pending(monkeypatch):
    incoming = _payment(whatsapp_operation_id=None)
    pair = SimpleNamespace(id=9)

    class _CreateDB(_DB):
        def flush(self):
            self.added[-1].id = 77

    class _QuoteService:
        def __init__(self, _db):
            pass

        def upsert_client(self, _phone):
            return SimpleNamespace(id=13)

        def _create_transaction_for_op(self, *_args, **_kwargs):
            raise AssertionError("Un pago entrante no debe completar ni crear la transacción final")

    db = _CreateDB()
    service = _service(db, incoming)
    service.pair_repo = SimpleNamespace(
        get_by_symbol=lambda symbol: pair if symbol == "VES-USDT" else None
    )
    monkeypatch.setattr(payment_service_module, "WhatsAppQuoteService", _QuoteService)

    result = service.create_operation_from_payment(
        "incoming",
        8,
        "VES",
        "USDT",
        4000,
        4,
        recorded_by_user_id=1,
    )

    operation = db.added[0]
    assert operation.status == WhatsAppOperationStatus.PENDING
    assert operation.completed_at is None
    assert incoming.whatsapp_operation_id == operation.id
    assert result["status"] == WhatsAppOperationStatus.PENDING.value
    assert db.commits == 1


def test_link_outgoing_rejects_operation_with_another_outgoing_payment():
    row = _payment(id=7, whatsapp_operation_id=None)
    operation_uuid = uuid4()
    operation = SimpleNamespace(id=42, uuid=operation_uuid)

    class _LinkDB(_DB):
        def query(self, entity):
            if entity is payment_service_module.WhatsAppOperation:
                return _Query(operation)
            return _Query(SimpleNamespace(id=99))

    db = _LinkDB()
    service = _service(db, row)

    with pytest.raises(QuoteServiceError) as exc:
        service.set_operation(
            "outgoing",
            row.id,
            operation_uuid,
            completing_user=SimpleNamespace(id=1),
            complete_outgoing=True,
        )

    assert exc.value.code == "operation_payment_already_linked"
    assert db.commits == 0


def test_link_outgoing_adopts_real_client_when_operation_client_is_group(monkeypatch):
    real_phone = "584121234567"
    row = _payment(id=7, client_phone=real_phone, whatsapp_operation_id=None)
    operation_uuid = uuid4()
    group_client = SimpleNamespace(id=3, phone="120363000000000@g.us")
    real_client = SimpleNamespace(id=4, phone=real_phone)
    operation = SimpleNamespace(id=42, uuid=operation_uuid, client=group_client, client_id=group_client.id)
    assignments = []

    class _LinkDB(_DB):
        def query(self, entity):
            if entity is payment_service_module.WhatsAppOperation:
                return _Query(operation)
            return _Query(None)

    class _QuoteService:
        def __init__(self, _db):
            pass

        def _assign_client(self, op, phone, display_name, update_display_name):
            assignments.append((phone, display_name, update_display_name))
            op.client = real_client
            op.client_id = real_client.id

    db = _LinkDB()
    service = _service(db, row)
    monkeypatch.setattr(payment_service_module, "WhatsAppQuoteService", _QuoteService)

    result = service.set_operation("outgoing", row.id, operation_uuid)

    assert assignments == [(real_phone, None, False)]
    assert operation.client is real_client
    assert row.client_phone == real_phone
    assert row.whatsapp_operation_id == operation.id
    assert result is row
    assert db.commits == 1


def test_link_outgoing_keeps_explicit_operation_client(monkeypatch):
    operation_phone = "584129999999"
    row = _payment(id=7, client_phone="584121234567", whatsapp_operation_id=None)
    operation_uuid = uuid4()
    operation = SimpleNamespace(
        id=42,
        uuid=operation_uuid,
        client=SimpleNamespace(id=3, phone=operation_phone),
    )

    class _LinkDB(_DB):
        def query(self, entity):
            if entity is payment_service_module.WhatsAppOperation:
                return _Query(operation)
            return _Query(None)

    class _UnexpectedQuoteService:
        def __init__(self, _db):
            raise AssertionError("No debe inferir otro cliente si la operación ya tiene uno real")

    db = _LinkDB()
    service = _service(db, row)
    monkeypatch.setattr(payment_service_module, "WhatsAppQuoteService", _UnexpectedQuoteService)

    service.set_operation("outgoing", row.id, operation_uuid)

    assert row.client_phone == operation_phone
    assert row.whatsapp_operation_id == operation.id
    assert db.commits == 1


# ===== Cliente y fondo de la operación creada desde un comprobante =====


class _QuoteSvcSpy:
    """Doble de WhatsAppQuoteService que registra qué cliente se pidió crear."""

    def __init__(self):
        self.anonymous_calls = []
        self.upsert_calls = []

    def upsert_anonymous_group_client(self, group):
        self.anonymous_calls.append(group)
        return SimpleNamespace(id=99, phone=f"anon:group:{group.id}")

    def upsert_client(self, phone):
        self.upsert_calls.append(phone)
        return SimpleNamespace(id=1, phone=phone)


def test_operation_from_group_receipt_uses_anonymous_client_not_the_group():
    """El grupo contable no es el cliente: la op nace anónima, no a nombre del grupo."""
    group = SimpleNamespace(id=1, name="Zelle/Paypal")
    row = _payment(client_phone="120363405617730310@g.us")
    service = _service(_DB({payment_service_module.FundGroup: group}), row)
    quote_svc = _QuoteSvcSpy()

    client = service._resolve_operation_client(quote_svc, row, None)

    assert quote_svc.anonymous_calls == [group]
    assert quote_svc.upsert_calls == []
    assert client.phone == "anon:group:1"


def test_operation_from_client_receipt_keeps_the_real_client():
    row = _payment(client_phone="584121234567")
    service = _service(_DB(), row)
    quote_svc = _QuoteSvcSpy()

    client = service._resolve_operation_client(quote_svc, row, None)

    assert quote_svc.anonymous_calls == []
    assert quote_svc.upsert_calls == ["584121234567"]
    assert client.phone == "584121234567"


def test_operation_inherits_fund_group_of_the_payment_when_omitted():
    """Sin fondo explícito, la op hereda el del comprobante en vez de nacer huérfana."""
    group = SimpleNamespace(id=1, name="Zelle/Paypal")
    row = _payment(fund_group_id=1)
    service = _service(_DB({payment_service_module.FundGroup: group}), row)

    resolved = service._resolve_operation_fund_group(row, None, fund_group_provided=False)

    assert resolved is group


def test_explicit_null_fund_group_is_respected_over_the_payment_one():
    """Si el operador quitó el fondo a propósito, no se lo devolvemos por la puerta de atrás."""
    group = SimpleNamespace(id=1, name="Zelle/Paypal")
    row = _payment(fund_group_id=1)
    service = _service(_DB({payment_service_module.FundGroup: group}), row)

    resolved = service._resolve_operation_fund_group(row, None, fund_group_provided=True)

    assert resolved is None


def test_link_outgoing_adopts_real_client_when_operation_client_is_anonymous(monkeypatch):
    """
    Una op creada desde un comprobante de grupo ya no queda con el JID sino con un cliente
    anónimo: al vincular el saliente real, ese teléfono debe seguir adoptándose igual.
    """
    real_phone = "584121234567"
    row = _payment(id=7, client_phone=real_phone, whatsapp_operation_id=None)
    operation_uuid = uuid4()
    anon_client = SimpleNamespace(id=3, phone="anon:group:1")
    real_client = SimpleNamespace(id=4, phone=real_phone)
    operation = SimpleNamespace(id=42, uuid=operation_uuid, client=anon_client, client_id=anon_client.id)
    assignments = []

    class _LinkDB(_DB):
        def query(self, entity):
            if entity is payment_service_module.WhatsAppOperation:
                return _Query(operation)
            return _Query(None)

    class _QuoteService:
        def __init__(self, _db):
            pass

        def _assign_client(self, op, phone, display_name, update_display_name):
            assignments.append((phone, display_name, update_display_name))
            op.client = real_client
            op.client_id = real_client.id

    db = _LinkDB()
    service = _service(db, row)
    monkeypatch.setattr(payment_service_module, "WhatsAppQuoteService", _QuoteService)

    service.set_operation("outgoing", row.id, operation_uuid)

    assert assignments == [(real_phone, None, False)]
    assert operation.client is real_client
    assert db.commits == 1
