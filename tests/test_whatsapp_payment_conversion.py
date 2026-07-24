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


class _QuoteSvcSpy:
    """Espía del quote service para probar a quién se le asigna el cliente de una op."""

    def __init__(self):
        self.anonymous_calls = []
        self.upsert_calls = []

    def upsert_anonymous_group_client(self, group):
        self.anonymous_calls.append(group)
        return SimpleNamespace(id=99, phone=f"anon:group:{group.id}")

    def upsert_client(self, phone):
        self.upsert_calls.append(phone)
        return SimpleNamespace(id=1, phone=phone)


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


def test_explicit_null_fund_group_is_respected_over_the_payment_one():
    """Si el operador quitó el fondo a propósito, no se lo devolvemos por la puerta de atrás."""
    group = SimpleNamespace(id=1, name="Zelle/Paypal")
    row = _payment(fund_group_id=1)
    service = _service(_DB({payment_service_module.FundGroup: group}), row)

    resolved = service._resolve_operation_fund_group(row, None, fund_group_provided=True)

    assert resolved is None


