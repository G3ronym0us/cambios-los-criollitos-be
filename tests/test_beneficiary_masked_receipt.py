"""
Comprobantes que sólo enseñan el destino tapado (`Destino: 0102****3817`).

De ahí no sale una cuenta nueva —sin cédula ni los 20 dígitos no se puede pagar— pero sí el
reconocimiento de una que el cliente ya tenía guardada. El caso real: el intermediario manda
la captura del BDV una y otra vez y la operación se quedaba siempre sin beneficiario.
"""

from datetime import datetime, timedelta, timezone

from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.services.whatsapp_client_account_service import WhatsAppClientAccountService
from tests.factories import outgoing

# El OCR del "Transferencias a terceros" del BDV: ni cédula, ni cuenta completa, ni teléfono.
BDV_RECEIPT = """Transferencias a terceros
7.337,81 Bs
Fecha: 06/08/2026
Operación: 059134978386
Nombre: Amelida Josefina Bastardo
Origen: 0102****6476
Destino: 0102****3817
Concepto: pago"""


def _op(db, client, pairs, **kw):
    """Operación del caso Dionis: el cliente entrega COP y el beneficiario recibe VES."""
    row = WhatsAppOperation(
        client_id=client.id,
        currency_pair_id=pairs["COP-VES"].id,
        currency=kw.pop("currency", "COP"),
        from_amount=100000, to_amount=24550, rate_used=0.2455,
        amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        **kw,
    )
    db.add(row)
    db.flush()
    return row


def _account(db, client, payment_info, alias="Amelida Bastardo", currency="VES"):
    from app.repositories.whatsapp_client_account_repository import (
        WhatsAppClientAccountRepository,
    )

    return WhatsAppClientAccountRepository(db).create(
        client_id=client.id, alias=alias, payment_info=payment_info,
        currency=currency, source="MESSAGE",
    )


def _pay(db, client, raw_text=BDV_RECEIPT, **kw):
    """Saliente tal como lo deja el bot con este comprobante: sin cédula y sin cuenta."""
    return outgoing(
        db, 7337.81, "VES", phone=client.phone, raw_text=raw_text,
        bank_to="0102", account_number=None, identification=None, phone_to=None, **kw,
    )


def test_recognizes_the_account_the_client_already_had(db, client, pairs):
    account = _account(db, client, "01020113121301033817\nV12345678")
    op = _op(db, client, pairs, beneficiary_alias="Amelida")

    learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client))

    assert learned is not None and learned.id == account.id
    assert op.beneficiary_account_id == account.id
    assert account.last_used_at is not None


def test_does_not_create_a_new_account(db, client, pairs):
    """Un dato tapado nunca se guarda: con `0102****3817` no se puede pagar."""
    from app.repositories.whatsapp_client_account_repository import (
        WhatsAppClientAccountRepository,
    )

    op = _op(db, client, pairs, beneficiary_alias="Amelida")

    assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None
    assert WhatsAppClientAccountRepository(db).list_for_client(client.id) == []
    assert op.beneficiary_account_id is None


def test_resolves_the_ambiguity_the_bot_left(db, client, pairs):
    """Dos cuentas con el mismo nombre: el comprobante dice a cuál de las dos se pagó."""
    paid = _account(db, client, "01020113121301033817\nV12345678", alias="Amelida Bastardo")
    _account(db, client, "01340866100001310098\nV26640340", alias="Amelida Bastardo")
    op = _op(db, client, pairs, beneficiary_alias="Amelida", beneficiary_ambiguous=True)

    learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client))

    assert learned is not None and learned.id == paid.id
    assert op.beneficiary_ambiguous is False


def test_two_accounts_with_the_same_ending_are_not_resolved(db, client, pairs):
    """Mismo banco y mismos últimos 4: el comprobante no da con qué elegir."""
    _account(db, client, "01020113121301033817\nV12345678", alias="Amelida")
    _account(db, client, "01029999999999993817\nV87654321", alias="Josefina")
    op = _op(db, client, pairs)

    assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None
    assert op.beneficiary_account_id is None


def test_a_different_name_wins_over_the_digits(db, client, pairs):
    """La operación nombró a otra persona: los dos datos se contradicen, no se vincula."""
    _account(db, client, "01020113121301033817\nV12345678", alias="Amelida Bastardo")
    op = _op(db, client, pairs, beneficiary_alias="Kelly Zitman")

    assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None


def test_ignores_accounts_of_another_currency(db, client, pairs):
    """El beneficiario recibe VES; una cuenta en COP con esos dígitos no es esta."""
    _account(db, client, "01020113121301033817\nV12345678", currency="COP")
    op = _op(db, client, pairs)

    assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None


def test_does_not_touch_an_operation_that_already_has_an_account(db, client, pairs):
    linked = _account(db, client, "01340866100001310098\nV26640340", alias="Kelly")
    _account(db, client, "01020113121301033817\nV12345678", alias="Amelida")
    op = _op(db, client, pairs, beneficiary_account_id=linked.id)

    assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None
    assert op.beneficiary_account_id == linked.id


def _history(db, client, account_number, identification="V12345678", **kw):
    """Comprobante viejo de otro banco, que sí imprimió la cuenta destino entera."""
    return outgoing(
        db, 5000, "VES", phone=client.phone, raw_text="Cuenta destino 01020113121301033817",
        bank_to="0102", account_number=account_number, identification=identification, **kw,
    )


class TestHistory:
    """
    Lo que un banco tapa, otro lo imprime entero. Si la misma cuenta ya se vio completa en un
    comprobante anterior, del tapado sale una cuenta pagable de verdad.
    """

    def test_completes_the_account_seen_whole_before(self, db, client, pairs):
        _history(db, client, "01020113121301033817", identification="26640340")
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client))

        assert learned is not None
        assert learned.payment_info == "01020113121301033817\nV26640340"
        assert learned.currency == "VES"
        assert op.beneficiary_account_id == learned.id

    def test_history_without_an_id_is_not_payable(self, db, client, pairs):
        _history(db, client, "01020113121301033817", identification=None)
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None

    def test_two_whole_accounts_with_the_same_ending_decide_nothing(self, db, client, pairs):
        _history(db, client, "01020113121301033817")
        _history(db, client, "01029999999999993817")
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None

    def test_two_ids_for_the_same_account_decide_nothing(self, db, client, pairs):
        """El OCR leyó dos cédulas distintas para la misma cuenta: alguna está mal."""
        _history(db, client, "01020113121301033817", identification="26640340")
        _history(db, client, "01020113121301033817", identification="12345678")
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None

    def test_another_clients_history_does_not_count(self, db, client, pairs):
        """La libreta es por cliente; el histórico que la completa, también."""
        outgoing(
            db, 5000, "VES", phone="584140000000", bank_to="0102",
            account_number="01020113121301033817", identification="26640340",
        )
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None

    def test_a_receipt_filed_under_a_group_jid_still_counts(self, db, client, pairs):
        """El saliente se reenvió al grupo de una cuenta alquilada, pero es de este cliente."""
        old_op = _op(db, client, pairs, beneficiary_alias="Amelida")
        outgoing(
            db, 5000, "VES", phone="120363000000000000@g.us", bank_to="0102",
            account_number="01020113121301033817", identification="26640340",
            whatsapp_operation_id=old_op.id,
        )
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client))

        assert learned is not None
        assert learned.payment_info == "01020113121301033817\nV26640340"

    def test_the_saved_account_wins_over_the_history(self, db, client, pairs):
        """Reconocer no es aprender: si ya está guardada, se vincula y no se crea otra."""
        account = _account(db, client, "01020113121301033817\nV12345678")
        _history(db, client, "01020113121301033817", identification="26640340")
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client))

        assert learned is not None and learned.id == account.id

    def test_an_operation_that_named_nobody_learns_nothing(self, db, client, pairs):
        """Guarda de `learn()`: una cuenta sin nombre no entra a la libreta de nombres."""
        _history(db, client, "01020113121301033817", identification="26640340")
        op = _op(db, client, pairs)

        assert WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client)) is None


class TestAmelida:
    """
    El caso que destapó todo esto, con sus datos reales: la captura del BDV tapa
    `0102****3817` y la cuenta es `01020418680105173817`, cédula 11829562.
    """

    ACCOUNT = "01020418680105173817"
    BLOCK = f"{ACCOUNT}\nV11829562"

    def test_the_receipt_recognizes_her_saved_account(self, db, client, pairs):
        saved = _account(db, client, self.BLOCK, alias="Amelida Josefina Bastardo")
        op = _op(db, client, pairs, beneficiary_alias="Amelida")

        learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client))

        assert learned is not None and learned.id == saved.id
        assert op.beneficiary_account_id == saved.id

    def test_the_receipt_learns_her_from_a_payment_made_at_another_bank(self, db, client, pairs):
        _history(db, client, self.ACCOUNT, identification="11829562")
        op = _op(db, client, pairs, beneficiary_alias="Amelida Josefina Bastardo")

        learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, _pay(db, client))

        assert learned is not None
        assert learned.payment_info == self.BLOCK
        assert learned.alias == "Amelida Josefina Bastardo"


def test_a_complete_receipt_still_learns_a_new_account(db, client, pairs):
    """El camino de siempre no cambia: con cédula y cuenta, la cuenta se aprende."""
    op = _op(db, client, pairs, beneficiary_alias="Kelly Zitman")
    payment = _pay(db, client, raw_text="Destino: 0134****0098")
    payment.account_number = "01340866100001310098"
    payment.identification = "26640340"

    learned = WhatsAppClientAccountService(db).learn_from_outgoing(op, payment)

    assert learned is not None
    assert learned.payment_info == "01340866100001310098\nV26640340"
    assert learned.source == "RECEIPT"
