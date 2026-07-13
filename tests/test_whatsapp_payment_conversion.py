from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.fund import FundMovement
from app.models.whatsapp_balance import WhatsAppBalanceEntry
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
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
