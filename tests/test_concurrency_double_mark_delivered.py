"""
Doble clic en "Marcar entregada": dos llamadas concurrentes de verdad (hilos, sesiones y
commits reales, NO el fixture `db` que comparte una sola transacción -- ver la nota en
`test_concurrency_race_link.py`) a `WhatsAppQuoteService.mark_delivered` sobre la MISMA
operación.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.fund import FundGroup, FundGroupMember, FundMovement
from app.models.transaction import Transaction, TransactionProfitSplit
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppDeliveryStatus,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService
from tests.conftest import _pair


@pytest.fixture
def seed_pending_delivery(engine):
    """Una operación USD-VES ya PENDING, con la entrega de USD física pendiente
    (`delivery_status=PENDING`) -- el estado que `mark_delivered` completa."""
    session = Session(bind=engine)
    try:
        operator = User(
            username=f"op_md_{id(session)}", email=f"op_md_{id(session)}@test.local",
            hashed_password="x", is_active=True, is_verified=True,
        )
        session.add(operator)
        session.flush()

        pair = _pair(session, "ZELLE", "VES", 800.0)

        fund = FundGroup(name=f"Fondo md {id(session)}", currency="USD", is_active=True)
        session.add(fund)
        session.flush()
        session.add(FundGroupMember(group_id=fund.id, user_id=operator.id, is_fund_manager=True))
        session.flush()

        client = WhatsAppClient(
            phone="1900000mdel", display_name="MarkDelivered", is_tracked=True,
            preferred_pair_id=pair.id,
        )
        session.add(client)
        session.flush()

        now = datetime.now(timezone.utc)
        op = WhatsAppOperation(
            client_id=client.id,
            currency_pair_id=pair.id,
            from_amount=220,
            to_amount=220 * 800.0,
            rate_used=800.0,
            amount_side=WhatsAppAmountSide.SEND,
            status=WhatsAppOperationStatus.PENDING,
            delivery_status=WhatsAppDeliveryStatus.PENDING,
            quoted_at=now,
            expires_at=now + timedelta(hours=1),
            fund_group_id=fund.id,
            fund_group_out_id=fund.id,
        )
        session.add(op)
        session.flush()
        session.commit()

        data = {
            "operator_id": operator.id,
            "fund_id": fund.id,
            "client_id": client.id,
            "op_id": op.id,
            "op_uuid": op.uuid,
        }
        yield data
    finally:
        session.rollback()
        session.close()

    cleanup = Session(bind=engine)
    try:
        tx_ids = [
            row[0] for row in cleanup.query(WhatsAppOperation.transaction_id).filter(
                WhatsAppOperation.id == data["op_id"]
            ).all() if row[0] is not None
        ]
        # Puede haber transacciones "huérfanas" creadas por la carrera (el bug bajo prueba):
        # cualquier Transaction con esta descripción, no sólo la que quedó enganchada al FK.
        orphan_tx_ids = [
            row[0] for row in cleanup.query(Transaction.id).filter(
                Transaction.description == f"WhatsApp op {data['op_uuid']}"
            ).all()
        ]
        all_tx_ids = sorted(set(tx_ids) | set(orphan_tx_ids))
        cleanup.query(FundMovement).filter(
            FundMovement.transaction_id.in_(all_tx_ids)
        ).delete(synchronize_session=False)
        cleanup.query(TransactionProfitSplit).filter(
            TransactionProfitSplit.transaction_id.in_(all_tx_ids)
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOperation).filter(
            WhatsAppOperation.id == data["op_id"]
        ).delete(synchronize_session=False)
        cleanup.query(Transaction).filter(Transaction.id.in_(all_tx_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(WhatsAppClient).filter(WhatsAppClient.id == data["client_id"]).delete()
        cleanup.query(FundGroupMember).filter(FundGroupMember.group_id == data["fund_id"]).delete()
        cleanup.query(FundGroup).filter(FundGroup.id == data["fund_id"]).delete()
        cleanup.query(User).filter(User.id == data["operator_id"]).delete()
        cleanup.commit()
    finally:
        cleanup.close()


def test_double_click_mark_delivered_does_not_create_two_transactions(
    engine, seed_pending_delivery
):
    """
    Dos peticiones simultáneas de "marcar entregada" (doble clic, o el operador y un reintento
    del front tras un timeout) sobre la MISMA operación.

    H-8.2: sin el `SELECT ... FOR UPDATE` que ahora tiene `mark_delivered`, esto era
    reproducible 1 de 1 (determinístico con el barrier): las DOS llamadas leían
    `delivery_status == PENDING` y creaban cada una su propia `Transaction` -- una quedaba
    enganchada a la operación y la otra huérfana, duplicando la ganancia contabilizada de un
    solo trato. Con el lock se espera que a lo sumo UNA Transaction quede creada y la otra
    llamada reciba 409.
    """
    barrier = threading.Barrier(2)
    results = {}

    def worker(name):
        session = Session(bind=engine)
        try:
            service = WhatsAppQuoteService(session)
            operator = session.get(User, seed_pending_delivery["operator_id"])
            barrier.wait(timeout=5)
            try:
                op = service.mark_delivered(seed_pending_delivery["op_uuid"], operator)
                results[name] = ("ok", op.transaction_id)
            except QuoteServiceError as exc:
                results[name] = ("error", exc.code)
            except Exception as exc:  # noqa: BLE001
                results[name] = ("exception", repr(exc))
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert "A" in results and "B" in results, f"un hilo no terminó: {results}"

    verify = Session(bind=engine)
    try:
        op = verify.get(WhatsAppOperation, seed_pending_delivery["op_id"])
        all_tx = verify.query(Transaction).filter(
            Transaction.description == f"WhatsApp op {seed_pending_delivery['op_uuid']}"
        ).all()
        report = {
            "results": results,
            "op.transaction_id": op.transaction_id,
            "op.status": op.status.value,
            "transactions_for_this_op": [t.id for t in all_tx],
        }
        print("MARK_DELIVERED RACE REPORT:", report)

        outcomes = sorted(r[0] for r in results.values())
        assert outcomes == ["error", "ok"], f"se esperaba un ok y un error 409: {report}"

        assert len(all_tx) == 1, (
            f"DUPLICADO: el doble clic en 'marcar entregada' creó {len(all_tx)} Transaction "
            f"para la misma operación pese al lock. Reporte: {report}"
        )
    finally:
        verify.close()
