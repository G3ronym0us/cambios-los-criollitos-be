"""
Un comprobante que ES un depósito al fondo.

El caso real: alguien que le debe dinero al operador le manda su comprobante, y ese dinero no
se retira — se queda en el fondo, a su nombre. Hoy el comprobante se queda huérfano en la
bandeja y el depósito se teclea aparte en /admin/funds, sin relación entre los dos (pago 4928
de 1.000.000 COP, 24-ago).

Las dos propuestas del formulario salen de datos que ya existen, y ninguna es un candado:
  * el FONDO, del canal donde llegó el comprobante
  * el GESTOR, de quién lo mandó
"""

from datetime import datetime, timezone

import pytest

from app.models.fund import (
    FundGroup,
    FundGroupMember,
    FundMovement,
    FundMovementType,
    FundPendingDepositOrigin,
)
from app.services.fund_pending_deposit_service import FundPendingDepositService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f

DIONIS = "573123146340"
DIOHANDRES = "584249428608"


@pytest.fixture
def deposits(db):
    return FundPendingDepositService(db)


@pytest.fixture
def dionis_user(db):
    """El segundo gestor del fondo: hacen falta DOS para probar la propuesta."""
    from app.models.user import User
    u = User(username="dionis", email="dionis@test.local", hashed_password="x",
             is_active=True, is_verified=True)
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def colombia(db, operator, dionis_user) -> FundGroup:
    """Cambios Colombia: sin grupo de WhatsApp y con DOS gestores, como en producción."""
    group = FundGroup(name="Cambios Colombia", currency="COP", is_active=True)
    db.add(group)
    db.flush()
    db.add(FundGroupMember(group_id=group.id, user_id=operator.id, is_fund_manager=True,
                           whatsapp_phone=DIOHANDRES))
    db.add(FundGroupMember(group_id=group.id, user_id=dionis_user.id, is_fund_manager=True,
                           whatsapp_phone=DIONIS))
    db.flush()
    return group


# ---------------------------------------------------------------------------
# Las propuestas
# ---------------------------------------------------------------------------


def test_the_fund_is_proposed_from_the_channel(deposits, db, colombia):
    """El comprobante llegó por el chat de Dionis, que ES el canal del fondo colombiano."""
    pago = f.outgoing(db, 1_000_000, "COP", phone=DIONIS, reference="0000021500")
    db.flush()

    sug = deposits.suggest_for_receipt("outgoing", pago.id)

    assert sug["fund_group_name"] == "Cambios Colombia"
    assert sug["amount"] == 1_000_000
    assert sug["currency"] == "COP"
    assert sug["reference"] == "0000021500"


def test_an_outgoing_proposes_the_operator_not_the_chat_owner(deposits, db, colombia, operator):
    """
    Un saliente lo mandó el operador, no el dueño del chat. Es justo el caso 4928: llegó por
    el chat de Dionis y el depósito es de Diohandres.
    """
    pago = f.outgoing(db, 1_000_000, "COP", phone=DIONIS)
    db.flush()

    sug = deposits.suggest_for_receipt("outgoing", pago.id)

    assert sug["user_uuid"] == operator.uuid
    assert sug["username"] == operator.username


def test_an_incoming_proposes_the_owner_of_the_chat(deposits, db, colombia, dionis_user):
    """Un entrante en el chat de Dionis lo mandó Dionis."""
    pago = f.incoming(db, 500_000, currency="COP", phone=DIONIS)
    db.flush()

    sug = deposits.suggest_for_receipt("incoming", pago.id)

    assert sug["user_uuid"] == dionis_user.uuid


def test_a_clients_chat_proposes_no_fund(deposits, db, colombia):
    """Un chat de cliente no es canal de ningún fondo: no se propone nada, no se adivina."""
    pago = f.outgoing(db, 50_000, "COP", phone="573999888777")
    db.flush()

    sug = deposits.suggest_for_receipt("outgoing", pago.id)

    assert sug["fund_group_uuid"] is None
    assert sug["user_uuid"] is None


# ---------------------------------------------------------------------------
# El registro
# ---------------------------------------------------------------------------


def test_the_receipt_becomes_the_deposit_and_stays_as_evidence(
    deposits, db, colombia, operator
):
    pago = f.outgoing(db, 1_000_000, "COP", phone=DIONIS, reference="0000021500",
                      provider="transferencia")
    db.flush()

    dep = deposits.create_from_receipt(
        "outgoing", pago.id, colombia.uuid, operator.uuid, created_by_user_id=operator.id,
    )

    assert dep["amount"] == 1_000_000
    assert dep["currency"] == "COP"
    assert dep["reference"] == "0000021500"
    assert dep["origin"] == FundPendingDepositOrigin.RECEIPT.value
    assert dep["source_outgoing_payment_id"] == pago.id

    # Señalarlo desde el pago ES la confirmación: no hay un segundo «sí» que dar en Fondos, y
    # el dinero entra al fondo en el mismo acto.
    assert dep["status"] == "CONFIRMED"
    movement = db.query(FundMovement).filter(
        FundMovement.movement_type == FundMovementType.DEPOSIT
    ).one()
    assert movement.amount == 1_000_000
    assert movement.currency == "COP"
    assert movement.group_id == colombia.id
    # El método sale del comprobante, no de una pregunta al operador.
    assert movement.deposit_method == "TRANSFER"


def test_a_receipt_without_a_readable_amount_is_refused(deposits, db, colombia, operator):
    """Sin monto no hay depósito que registrar: se corrige el comprobante primero."""
    pago = f.outgoing(db, None, "COP", phone=DIONIS)
    db.flush()

    with pytest.raises(QuoteServiceError) as exc:
        deposits.create_from_receipt(
            "outgoing", pago.id, colombia.uuid, operator.uuid, created_by_user_id=operator.id,
        )
    assert exc.value.code == "missing_fields"


# ---------------------------------------------------------------------------
# El guardarraíl del duplicado: coincidir no es estar contado
# ---------------------------------------------------------------------------


def test_a_loose_matching_incoming_does_not_block_the_deposit(
    deposits, db, colombia, operator
):
    """
    El entrante 424 coincidía con el saliente 4928 —son el mismo comprobante— pero no tenía
    operación ni movimiento: ese millón no estaba contado en ninguna parte y frenarlo obligaba
    a forzar el depósito para el caso normal.
    """
    gemelo = f.incoming(db, 1_000_000, currency="COP", phone="573183099427",
                        reference="0000021500")
    pago = f.outgoing(db, 1_000_000, "COP", phone=DIONIS, reference="0000021500")
    db.flush()

    dep = deposits.create_from_receipt(
        "outgoing", pago.id, colombia.uuid, operator.uuid, created_by_user_id=operator.id,
    )
    assert dep["source_incoming_payment_id"] == gemelo.id, "el vínculo se registra igual"
    assert dep["status"] == "CONFIRMED"


def test_an_incoming_already_counted_still_blocks(deposits, db, colombia, operator, pairs):
    """Lo que de verdad duplicaría sigue frenando: ese dinero ya movió el fondo."""
    gemelo = f.incoming(db, 1_000_000, currency="COP", phone="573183099427",
                        reference="0000021500")
    db.flush()
    db.add(FundMovement(
        group_id=colombia.id, user_id=operator.id, movement_type=FundMovementType.EXCHANGE_IN,
        amount=1_000_000, currency="COP", incoming_payment_id=gemelo.id,
        movement_date=datetime.now(timezone.utc),
    ))
    pago = f.outgoing(db, 1_000_000, "COP", phone=DIONIS, reference="0000021500")
    db.flush()

    with pytest.raises(QuoteServiceError) as exc:
        deposits.create_from_receipt(
            "outgoing", pago.id, colombia.uuid, operator.uuid, created_by_user_id=operator.id,
        )
    assert exc.value.code == "duplicate_of_incoming"

    # Y no queda rastro: confirmar en el mismo acto significa que un depósito frenado no deja
    # una fila PENDING que nadie podría confirmar nunca.
    from app.models.fund import FundPendingDeposit
    assert db.query(FundPendingDeposit).count() == 0
