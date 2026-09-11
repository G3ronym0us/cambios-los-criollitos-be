"""
Clasificación automática del escenario (`WhatsAppOperationScenario`) al nacer una operación
de un comprobante, y su corrección del histórico (`app/cli/backfill_operation_scenarios.py`).

El negocio (ver `backend/CLAUDE.md`, "Quién manda qué al grupo de WhatsApp"): todos los
comprobantes al grupo los sube Diohandres, el operador — quién los sube no distingue nada.
Lo que distingue es el CONTENIDO:

  - Captura de Zelle ENTRANTE -> el cliente es de Diohandres. ZELLE_DIRECT.
  - Comprobante SALIENTE en bolívares, sin entrante -> el socio del fondo (Jean, Dionis)
    cobró al cliente en su propio WhatsApp; el entrante nunca llega al operador.
    VIA_PARTNER, con `received_by_user_id` = el socio.

`WhatsAppPaymentService._resolve_scenario_for_new_op` es el punto único (mismo patrón que
`_resolve_fund_legs_for_new_op`): corre una vez, al nacer la op desde
`create_operation_from_payment`, y sólo rellena lo que está vacío.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.cli.backfill_operation_scenarios import apply_scenario_backfill, plan_scenario_backfill
from app.models.fund import FundGroupMember
from app.models.user import User
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationScenario,
    WhatsAppOperationStatus,
)
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def _add_partner(db, group_id, phone, username="jean"):
    """Un socio del fondo: miembro con `whatsapp_phone` propio (la columna que el modelo
    documenta como "el socio"), igual que lo usa `resolve_fund_channel`."""
    user = User(
        username=username, email=f"{username}@test.local", hashed_password="x",
        is_active=True, is_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(FundGroupMember(
        group_id=group_id, user_id=user.id, is_fund_manager=True, whatsapp_phone=phone,
    ))
    db.flush()
    return user


# --------------------------------------------------------------------------- clasificación en vivo


def test_outgoing_bs_receipt_defaults_to_via_partner_with_the_fund_partner(
    service, db, fund, pairs, client, operator
):
    """
    El caso real (Jean/Dionis): un comprobante SALIENTE llega solo, sin entrante — porque el
    socio cobró al cliente en su propio chat. La op nace VIA_PARTNER con el socio como
    receptor del entrante que nunca llegó.
    """
    jean = _add_partner(db, fund.id, "16204195618")

    pago = f.outgoing(db, 78292, "VES")
    op = _op(db, service.create_operation_from_payment(
        "outgoing", pago.id, "ZELLE", "VES", 100, 78292,
        exchange_user_uuid=operator.uuid, recorded_by_user_id=operator.id,
    )["uuid"])

    assert op.scenario == WhatsAppOperationScenario.VIA_PARTNER
    assert op.received_by_user_id == jean.id


def test_incoming_zelle_capture_defaults_to_zelle_direct(service, db, fund, pairs, client, operator):
    """Una captura de Zelle ENTRANTE es del cliente de Diohandres: ZELLE_DIRECT, sin receptor."""
    inc = f.incoming(db, 100, "ZELLE")
    op = _op(db, service.create_operation_from_payment(
        "incoming", inc.id, "ZELLE", "VES", 100, 78292,
        exchange_user_uuid=operator.uuid, recorded_by_user_id=operator.id,
    )["uuid"])

    assert op.scenario == WhatsAppOperationScenario.ZELLE_DIRECT
    assert op.received_by_user_id is None


def test_manual_scenario_is_not_overridden(service, db, fund, pairs, client, operator):
    """Nunca una imposición: un escenario ya fijado (a mano, o heredado del payload) se respeta."""
    _add_partner(db, fund.id, "16204195619")

    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-VES"].id,
        from_amount=100, to_amount=78292, rate_used=782.92, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING, amount=100, currency="ZELLE",
        fund_group_id=fund.id,
        scenario=WhatsAppOperationScenario.ZELLE_DIRECT,  # ya fijado a mano
        quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()

    # El saliente por sí solo diría VIA_PARTNER; no debe pisar lo que ya estaba.
    service._resolve_scenario_for_new_op(op, "outgoing")

    assert op.scenario == WhatsAppOperationScenario.ZELLE_DIRECT
    assert op.received_by_user_id is None


def test_manual_receiver_is_not_overridden(service, db, fund, pairs, client, operator, partner):
    """Un receptor ya asignado a mano también se respeta, aunque el escenario siga en NORMAL."""
    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-VES"].id,
        from_amount=100, to_amount=78292, rate_used=782.92, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING, amount=100, currency="ZELLE",
        fund_group_id=fund.id,
        received_by_user_id=partner.id,  # ya fijado a mano
        quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()

    service._resolve_scenario_for_new_op(op, "incoming")

    assert op.scenario == WhatsAppOperationScenario.NORMAL
    assert op.received_by_user_id == partner.id


def test_a_fund_with_no_identifiable_partner_does_not_guess(service, db, fund, pairs, client, operator):
    """
    Sólo Diohandres en el fondo (nadie con `whatsapp_phone` propio): no hay a quién
    atribuirle el entrante. Mejor no clasificar que atribuirle la ganancia a quien no
    gestionó el cambio.
    """
    pago = f.outgoing(db, 78292, "VES")
    op = _op(db, service.create_operation_from_payment(
        "outgoing", pago.id, "ZELLE", "VES", 100, 78292,
        exchange_user_uuid=operator.uuid, recorded_by_user_id=operator.id,
    )["uuid"])

    assert op.scenario == WhatsAppOperationScenario.NORMAL
    assert op.received_by_user_id is None


def test_a_fund_with_more_than_one_candidate_partner_does_not_guess(
    service, db, fund, pairs, client, operator
):
    """Dos socios posibles en el mismo fondo: adivinar movería la ganancia al equivocado."""
    _add_partner(db, fund.id, "16204195618", username="jean")
    _add_partner(db, fund.id, "584267948636", username="diodimar")

    pago = f.outgoing(db, 78292, "VES")
    op = _op(db, service.create_operation_from_payment(
        "outgoing", pago.id, "ZELLE", "VES", 100, 78292,
        exchange_user_uuid=operator.uuid, recorded_by_user_id=operator.id,
    )["uuid"])

    assert op.scenario == WhatsAppOperationScenario.NORMAL
    assert op.received_by_user_id is None


def test_cash_pairs_never_get_a_partner(service, db, fund, pairs, client, operator):
    """
    USD-VES (efectivo) comparte fondo por moneda con Zelle/Paypal, pero el cliente paga en
    mano, directo con Diohandres: nunca hay socio, aunque el fondo tenga uno identificable.
    """
    from app.models.currency import Currency
    from app.models.currency_pair import CurrencyPair

    usd = Currency(symbol="USD", name="USD")
    db.add(usd)
    db.flush()
    ves = db.query(Currency).filter(Currency.symbol == "VES").first()
    usd_ves = CurrencyPair(
        from_currency_id=usd.id, to_currency_id=ves.id, pair_symbol="USD-VES",
        is_active=True, settles_in_cash=True,
    )
    db.add(usd_ves)
    db.flush()

    _add_partner(db, fund.id, "16204195618")

    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=usd_ves.id,
        from_amount=100, to_amount=78292, rate_used=782.92, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING, amount=100, currency="USD",
        fund_group_id=fund.id,
        quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()

    service._resolve_scenario_for_new_op(op, "outgoing")

    assert op.scenario == WhatsAppOperationScenario.NORMAL
    assert op.received_by_user_id is None


# --------------------------------------------------------------------------- CLI de corrección del histórico


def test_cli_dry_run_reports_without_writing_then_apply_writes(db, fund, pairs, client, operator):
    """
    El ensayo (dry-run, sin --apply) sólo informa; recién `apply_scenario_backfill` escribe.
    Simula el histórico real: ops viejas `NORMAL` sin receptor, con sus comprobantes ya
    vinculados a mano (como quedaron antes de que existiera esta clasificación automática).
    """
    jean = _add_partner(db, fund.id, "16204195618")
    now = datetime.now(timezone.utc)

    # Vieja op con un entrante vinculado: debería quedar ZELLE_DIRECT.
    op_in = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-VES"].id,
        from_amount=100, to_amount=78292, rate_used=782.92, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING, amount=100, currency="ZELLE",
        fund_group_id=fund.id,
        quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op_in)
    db.flush()
    inc = f.incoming(db, 100, "ZELLE")
    inc.whatsapp_operation_id = op_in.id

    # Vieja op con sólo un saliente vinculado y socio único: debería quedar VIA_PARTNER.
    op_out = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-VES"].id,
        from_amount=50, to_amount=39146, rate_used=782.92, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING, amount=50, currency="ZELLE",
        fund_group_id=fund.id,
        quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op_out)
    db.flush()
    out = f.outgoing(db, 39146, "VES")
    out.whatsapp_operation_id = op_out.id
    db.flush()

    plan = plan_scenario_backfill(db)
    assert op_in.id in plan["zelle_direct"]
    assert any(item[0] == op_out.id and item[1] == jean.id for item in plan["via_partner"])

    # El ensayo no escribió nada.
    assert op_in.scenario == WhatsAppOperationScenario.NORMAL
    assert op_out.scenario == WhatsAppOperationScenario.NORMAL
    assert op_out.received_by_user_id is None

    apply_scenario_backfill(db, plan)
    db.refresh(op_in)
    db.refresh(op_out)

    assert op_in.scenario == WhatsAppOperationScenario.ZELLE_DIRECT
    assert op_out.scenario == WhatsAppOperationScenario.VIA_PARTNER
    assert op_out.received_by_user_id == jean.id


def test_cli_does_not_touch_an_already_classified_operation(db, fund, pairs, client, operator):
    """El ensayo respeta lo ya clasificado (a mano, o por una corrida anterior): no lo toca."""
    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-VES"].id,
        from_amount=100, to_amount=78292, rate_used=782.92, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.PENDING, amount=100, currency="ZELLE",
        fund_group_id=fund.id,
        scenario=WhatsAppOperationScenario.NORMAL,
        quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    inc = f.incoming(db, 100, "ZELLE")
    inc.whatsapp_operation_id = op.id
    op.received_by_user_id = operator.id  # ya clasificado a mano (aunque scenario siga NORMAL)
    db.flush()

    plan = plan_scenario_backfill(db)

    assert op.id not in plan["zelle_direct"]
    assert all(item[0] != op.id for item in plan["via_partner"])
