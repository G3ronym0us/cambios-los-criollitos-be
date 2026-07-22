from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.whatsapp import WhatsAppOperationUpdate
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService


def _operator():
    """Stub del operador (User) que update_operation exige para sincronizar transacciones."""
    return SimpleNamespace(id=1, username="operator")


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("584121234567", "584121234567"),
        ("+58 (412) 123-4567", "584121234567"),
        ("58.412.123.4567", "584121234567"),
    ],
)
def test_operation_update_normalizes_phone(raw, normalized):
    payload = WhatsAppOperationUpdate(client_phone=raw)

    assert payload.client_phone == normalized


@pytest.mark.parametrize("phone", ["abcd", "58412@g.us", "58/412/123", "１２３４"])
def test_operation_update_rejects_non_numeric_phone(phone):
    with pytest.raises(ValidationError):
        WhatsAppOperationUpdate(client_phone=phone)


def test_operation_update_preserves_explicit_null_for_clearing_name():
    payload = WhatsAppOperationUpdate(
        client_phone="584121234567",
        client_display_name=None,
    )

    assert "client_display_name" in payload.model_fields_set
    assert payload.client_display_name is None


class _PaymentUpdateQuery:
    def __init__(self, updates):
        self.updates = updates

    def filter(self, *_args, **_kwargs):
        return self

    def update(self, values, **_kwargs):
        self.updates.append(values)


class _TrackingDB:
    def __init__(self):
        self.commits = 0
        self.refreshed = []
        self.payment_updates = []

    def query(self, _model):
        return _PaymentUpdateQuery(self.payment_updates)

    def commit(self):
        self.commits += 1

    def refresh(self, op):
        self.refreshed.append(op)


def test_assign_client_clears_name_and_syncs_both_payment_types():
    db = _TrackingDB()
    client = SimpleNamespace(id=22, phone="584121234567", display_name="Nombre anterior")
    op = SimpleNamespace(id=7, client_id=1)
    service = SimpleNamespace(db=db, upsert_client=lambda _phone: client)

    WhatsAppQuoteService._assign_client(
        service,
        op,
        client.phone,
        display_name=None,
        update_display_name=True,
    )

    assert client.display_name is None
    assert op.client_id == client.id
    assert op.client is client
    assert db.payment_updates == [
        {"client_phone": client.phone},
        {"client_phone": client.phone},
    ]


def test_operation_update_commits_once_after_all_changes_succeed():
    db = _TrackingDB()
    op = SimpleNamespace(fund_group_id=None, transaction_id=None)
    calls = []
    service = SimpleNamespace(
        db=db,
        _get_op_or_404=lambda _uuid: op,
        _assign_client=lambda *args: calls.append(("client", args)),
        _apply_scenario=lambda *args: calls.append(("scenario", args)),
        _sync_linked_transaction=lambda _op: None,
    )
    payload = WhatsAppOperationUpdate(
        client_phone="584121234567",
        client_display_name="Ana",
        scenario="NORMAL",
    )

    result = WhatsAppQuoteService.update_operation(service, None, payload, _operator())

    assert result is op
    assert [call[0] for call in calls] == ["client", "scenario"]
    assert db.commits == 1
    assert db.refreshed == [op]


def test_operation_update_does_not_commit_when_scenario_fails():
    db = _TrackingDB()
    op = SimpleNamespace()

    def fail_scenario(*_args):
        raise QuoteServiceError("fund_group_not_found", "FundGroup no encontrado", 404)

    service = SimpleNamespace(
        db=db,
        _get_op_or_404=lambda _uuid: op,
        _assign_client=lambda *_args: None,
        _apply_scenario=fail_scenario,
    )
    payload = WhatsAppOperationUpdate(
        client_phone="584121234567",
        scenario="NORMAL",
    )

    with pytest.raises(QuoteServiceError):
        WhatsAppQuoteService.update_operation(service, None, payload, _operator())

    assert db.commits == 0
    assert db.refreshed == []


class _PairQuery:
    def __init__(self, pair):
        self.pair = pair

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.pair


class _PairTrackingDB(_TrackingDB):
    def __init__(self, pair):
        super().__init__()
        self.pair = pair

    def query(self, _model):
        return _PairQuery(self.pair)


def test_operation_update_reassigns_currency_pair_without_changing_amounts():
    pair_uuid = uuid4()
    pair = SimpleNamespace(id=9, uuid=pair_uuid)
    op = SimpleNamespace(
        currency_pair_id=3,
        currency_pair=SimpleNamespace(id=3),
        from_amount=200,
        to_amount=157440,
        rate_used=787.2,
        fund_group_id=None,
        transaction_id=None,
    )
    db = _PairTrackingDB(pair)
    service = SimpleNamespace(
        db=db,
        _get_op_or_404=lambda _uuid: op,
        _apply_scenario=lambda *_args: None,
        _sync_linked_transaction=lambda _op: None,
    )

    result = WhatsAppQuoteService.update_operation(
        service,
        None,
        WhatsAppOperationUpdate(currency_pair_uuid=pair_uuid),
        _operator(),
    )

    assert result.currency_pair_id == pair.id
    assert result.currency_pair is pair
    assert (result.from_amount, result.to_amount, result.rate_used) == (200, 157440, 787.2)
    assert db.commits == 1


def test_operation_update_rejects_unknown_currency_pair():
    db = _PairTrackingDB(None)
    service = SimpleNamespace(
        db=db,
        _get_op_or_404=lambda _uuid: SimpleNamespace(),
    )

    with pytest.raises(QuoteServiceError) as error:
        WhatsAppQuoteService.update_operation(
            service,
            None,
            WhatsAppOperationUpdate(currency_pair_uuid=uuid4()),
            _operator(),
        )

    assert error.value.code == "currency_pair_not_found"
    assert db.commits == 0
