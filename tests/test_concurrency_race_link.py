"""
Pruebas de concurrencia REAL contra Postgres: dos sesiones SQLAlchemy independientes, cada
una con su propio commit, lanzadas en paralelo con hilos.

El fixture `db` de conftest.py envuelve cada test en una única transacción que se revierte
al final (savepoints para los `commit()` de los servicios) -- eso aísla los tests entre sí,
pero significa que DOS "sesiones" dentro del mismo test en realidad comparten una sola
transacción de Postgres y NUNCA compiten de verdad entre ellas (no hay dos transacciones
concurrentes reales, así que ni un lock de fila las hace esperar). Por eso estas pruebas no
usan `db`: abren conexiones nuevas al motor de sesión (`engine`) y hacen su propio
`Session(bind=engine)` con commits reales, agrupadas con `threading.Barrier` para forzar que
las dos peticiones lleguen a la vez.

Al terminar cada test se limpia lo que creó (no hay rollback automático posible: ya hizo
commit).
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.fund import FundGroup, FundGroupMember, FundMovement
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import WhatsAppOutgoingPayment
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests.conftest import _currency, _pair


@pytest.fixture
def seed(engine):
    """Crea en una sesión propia (con commit real) los datos base: operador, fondo, cliente,
    par ZELLE-VES, y DOS operaciones QUOTED del mismo cliente que valen 220 ZELLE cada una,
    más un comprobante saliente sin vincular que alcanza para cubrir una sola.

    Devuelve un dict con ids y hace su propia limpieza al final (DELETE en orden de FK)."""
    session = Session(bind=engine)
    try:
        operator = User(
            username=f"op_race_{id(session)}", email=f"op_race_{id(session)}@test.local",
            hashed_password="x", is_active=True, is_verified=True,
        )
        session.add(operator)
        session.flush()

        pair = _pair(session, "ZELLE", "VES", 800.0)

        fund = FundGroup(name=f"Fondo race {id(session)}", currency="USD", is_active=True)
        session.add(fund)
        session.flush()
        session.add(FundGroupMember(group_id=fund.id, user_id=operator.id, is_fund_manager=True))
        session.flush()

        client = WhatsAppClient(
            phone="1900000race", display_name="Race", is_tracked=True,
            preferred_pair_id=pair.id,
        )
        session.add(client)
        session.flush()

        now = datetime.now(timezone.utc)

        def _op(from_amount, to_amount):
            op = WhatsAppOperation(
                client_id=client.id,
                currency_pair_id=pair.id,
                from_amount=from_amount,
                to_amount=to_amount,
                rate_used=800.0,
                amount_side=WhatsAppAmountSide.SEND,
                status=WhatsAppOperationStatus.QUOTED,
                quoted_at=now,
                expires_at=now + timedelta(hours=1),
                fund_group_id=fund.id,
                fund_group_out_id=fund.id,
            )
            session.add(op)
            session.flush()
            return op

        op_a = _op(220, 220 * 800.0)
        op_b = _op(220, 220 * 800.0)

        payment = WhatsAppOutgoingPayment(
            client_phone=client.phone, amount=220 * 800.0, currency="VES",
            created_at=now,
        )
        session.add(payment)
        session.flush()

        session.commit()

        data = {
            "operator_id": operator.id,
            "fund_id": fund.id,
            "client_id": client.id,
            "op_a_id": op_a.id,
            "op_a_uuid": op_a.uuid,
            "op_b_id": op_b.id,
            "op_b_uuid": op_b.uuid,
            "payment_id": payment.id,
        }
        yield data
    finally:
        session.rollback()
        session.close()

    # limpieza con commit real, orden por FK
    cleanup = Session(bind=engine)
    try:
        from app.models.transaction import Transaction, TransactionProfitSplit
        from app.models.whatsapp_payment import WhatsAppOutgoingSettlement

        tx_ids = [
            row[0] for row in cleanup.query(WhatsAppOperation.transaction_id).filter(
                WhatsAppOperation.id.in_([data["op_a_id"], data["op_b_id"]])
            ).all() if row[0] is not None
        ]
        cleanup.query(FundMovement).filter(
            FundMovement.transaction_id.in_(tx_ids)
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOutgoingSettlement).filter(
            WhatsAppOutgoingSettlement.outgoing_payment_id == data["payment_id"]
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOutgoingPayment).filter(
            WhatsAppOutgoingPayment.id == data["payment_id"]
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOperation).filter(
            WhatsAppOperation.id.in_([data["op_a_id"], data["op_b_id"]])
        ).delete(synchronize_session=False)
        cleanup.query(TransactionProfitSplit).filter(
            TransactionProfitSplit.transaction_id.in_(tx_ids)
        ).delete(synchronize_session=False)
        cleanup.query(Transaction).filter(Transaction.id.in_(tx_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(WhatsAppClient).filter(WhatsAppClient.id == data["client_id"]).delete()
        cleanup.query(FundGroupMember).filter(FundGroupMember.group_id == data["fund_id"]).delete()
        cleanup.query(FundGroup).filter(FundGroup.id == data["fund_id"]).delete()
        cleanup.query(User).filter(User.id == data["operator_id"]).delete()
        cleanup.commit()
    finally:
        cleanup.close()


def test_same_payment_linked_to_two_operations_concurrently_is_not_a_lost_update(engine, seed):
    """
    Dos operadores (o el operador y el bot) vinculan a la vez el MISMO comprobante saliente
    a DOS operaciones distintas que ambas lo aceptarían si sólo miraran el estado que vieron
    al leer (`whatsapp_operation_id` todavía None para los dos).

    H-8.1: sin el `SELECT ... FOR UPDATE` que ahora tiene `set_operation`, esto era una
    pérdida de actualización (lost update) reproducible 3 de 3 veces: las DOS operaciones
    quedaban COMPLETED con su movimiento de fondo creado, pero el comprobante que las
    "completó" sólo podía apuntar a UNA -- contabilidad fantasma, plata que no existe.

    Con el lock: la segunda transacción espera a que la primera comitee su vínculo y
    entonces sí ve `whatsapp_operation_id` ya puesto -> 409 `payment_already_linked`. Sólo
    una operación puede quedar COMPLETED por este comprobante.
    """
    barrier = threading.Barrier(2)
    results = {}

    def worker(name, op_uuid):
        session = Session(bind=engine)
        try:
            service = WhatsAppPaymentService(session)
            operator = session.get(User, seed["operator_id"])
            barrier.wait(timeout=5)
            try:
                out = service.set_operation(
                    "outgoing", seed["payment_id"], op_uuid,
                    completing_user=operator, complete_outgoing=True,
                )
                results[name] = ("ok", out)
            except QuoteServiceError as exc:
                results[name] = ("error", exc.code)
            except Exception as exc:  # noqa: BLE001
                results[name] = ("exception", repr(exc))
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("A", seed["op_a_uuid"]))
    t2 = threading.Thread(target=worker, args=("B", seed["op_b_uuid"]))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert "A" in results and "B" in results, f"un hilo no terminó: {results}"

    verify = Session(bind=engine)
    try:
        payment = verify.get(WhatsAppOutgoingPayment, seed["payment_id"])
        op_a = verify.get(WhatsAppOperation, seed["op_a_id"])
        op_b = verify.get(WhatsAppOperation, seed["op_b_id"])

        linked_to = payment.whatsapp_operation_id
        other_op = op_b if linked_to == op_a.id else op_a
        winner_op = op_a if linked_to == op_a.id else op_b

        movements_other = verify.query(FundMovement).filter(
            FundMovement.transaction_id == other_op.transaction_id
        ).count() if other_op.transaction_id else 0

        report = {
            "results": results,
            "payment.whatsapp_operation_id": linked_to,
            "op_a.status": op_a.status.value,
            "op_b.status": op_b.status.value,
            "op_a.transaction_id": op_a.transaction_id,
            "op_b.transaction_id": op_b.transaction_id,
            "movements_on_loser_op": movements_other,
        }
        print("RACE REPORT:", report)

        # Con el lock, exactamente una de las dos llamadas debe haber sido rechazada.
        outcomes = sorted(r[0] for r in results.values())
        assert outcomes == ["error", "ok"], f"se esperaba un ok y un error 409: {report}"
        rejected = next(v for v in results.values() if v[0] == "error")
        assert rejected[1] == "payment_already_linked", report

        # Y la operación que perdió la carrera NUNCA debe quedar COMPLETED con un movimiento
        # de fondo fantasma (el bug original: contabilidad sin comprobante real detrás).
        loser_completed_with_movement = (
            other_op.status == WhatsAppOperationStatus.COMPLETED and movements_other > 0
        )
        assert not loser_completed_with_movement, (
            "LOST UPDATE: la operación perdedora quedó COMPLETED con movimiento de fondo "
            f"pese al lock. Reporte: {report}"
        )
    finally:
        verify.close()
