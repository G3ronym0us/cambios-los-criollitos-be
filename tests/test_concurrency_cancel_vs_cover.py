"""
Dos operadores, una operación: uno la CANCELA mientras el otro la CUBRE (vincula el
comprobante que la completa). Concurrencia real -- ver la nota en
`test_concurrency_race_link.py` sobre por qué el fixture `db` no sirve para esto.
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
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import WhatsAppOutgoingPayment
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService
from tests.conftest import _pair


@pytest.fixture
def seed(engine):
    session = Session(bind=engine)
    try:
        operator = User(
            username=f"op_cv_{id(session)}", email=f"op_cv_{id(session)}@test.local",
            hashed_password="x", is_active=True, is_verified=True,
        )
        session.add(operator)
        session.flush()

        pair = _pair(session, "ZELLE", "VES", 800.0)

        fund = FundGroup(name=f"Fondo cv {id(session)}", currency="USD", is_active=True)
        session.add(fund)
        session.flush()
        session.add(FundGroupMember(group_id=fund.id, user_id=operator.id, is_fund_manager=True))
        session.flush()

        client = WhatsAppClient(
            phone="1900000cv", display_name="CancelVsCover", is_tracked=True,
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
            status=WhatsAppOperationStatus.QUOTED,
            quoted_at=now,
            expires_at=now + timedelta(hours=1),
            fund_group_id=fund.id,
            fund_group_out_id=fund.id,
        )
        session.add(op)
        session.flush()

        payment = WhatsAppOutgoingPayment(
            client_phone=client.phone, amount=220 * 800.0, currency="VES", created_at=now,
        )
        session.add(payment)
        session.flush()
        session.commit()

        data = {
            "operator_id": operator.id, "fund_id": fund.id, "client_id": client.id,
            "op_id": op.id, "op_uuid": op.uuid, "payment_id": payment.id,
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
        cleanup.query(FundMovement).filter(FundMovement.transaction_id.in_(tx_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(TransactionProfitSplit).filter(
            TransactionProfitSplit.transaction_id.in_(tx_ids)
        ).delete(synchronize_session=False)
        from app.models.whatsapp_payment import WhatsAppOutgoingSettlement
        cleanup.query(WhatsAppOutgoingSettlement).filter(
            WhatsAppOutgoingSettlement.outgoing_payment_id == data["payment_id"]
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOutgoingPayment).filter(
            WhatsAppOutgoingPayment.id == data["payment_id"]
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOperation).filter(WhatsAppOperation.id == data["op_id"]).delete(
            synchronize_session=False
        )
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


@pytest.mark.xfail(
    reason=(
        "H-8.4, no arreglado: cancelar y cubrir a la vez es una carrera real (8 de 10 en la "
        "campaña) porque `update_status` comitea a mitad del flujo de `set_operation` y "
        "rompe cualquier lock tomado antes. Ver docs/superpowers/... y el informe "
        "08-concurrencia.md del agente 8. Intermitente: no se marca `strict` porque a veces "
        "gana el cancel."
    ),
    strict=False,
)
def test_cancel_and_cover_race(engine, seed):
    """
    H-8.4 (NO ARREGLADO -- documentado en el informe): operador A cancela la operación;
    operador B, a la vez, vincula el comprobante que la cubre entera (lo que la
    completaría). Reproducido 8 de 10 veces (dos tandas de 5): la operación queda
    COMPLETED -- con Transaction y movimientos de fondo reales -- pese a que el operador A
    la canceló en el mismo instante.

    La causa no es sólo la falta de un lock: `WhatsAppPaymentService._sync_status_from_
    delivery` llama a `WhatsAppQuoteService.update_status`, que hace su PROPIO
    `self.db.commit()` a mitad del flujo de `set_operation`. Ese commit interno rompe
    cualquier lock que se tome al principio de la función (se probó con
    `SELECT ... FOR UPDATE` sobre la operación y NO cerró la carrera: se probó, no se afirma
    a ciegas). Arreglarlo de verdad implica que los servicios dejen de comitear a mitad de
    camino -- que el commit lo controle sólo el caller más externo -- y eso es un cambio de
    forma más grande que esta campaña no debe meter de un commit suelto.
    """
    barrier = threading.Barrier(2)
    results = {}

    def cancel_worker():
        session = Session(bind=engine)
        try:
            service = WhatsAppQuoteService(session)
            operator = session.get(User, seed["operator_id"])
            barrier.wait(timeout=5)
            try:
                op = service.update_status(seed["op_uuid"], "CANCELLED", operator)
                results["cancel"] = ("ok", op.status.value)
            except QuoteServiceError as exc:
                results["cancel"] = ("error", exc.code)
            except Exception as exc:  # noqa: BLE001
                results["cancel"] = ("exception", repr(exc))
        finally:
            session.close()

    def cover_worker():
        session = Session(bind=engine)
        try:
            service = WhatsAppPaymentService(session)
            operator = session.get(User, seed["operator_id"])
            barrier.wait(timeout=5)
            try:
                out = service.set_operation(
                    "outgoing", seed["payment_id"], seed["op_uuid"],
                    completing_user=operator, complete_outgoing=True,
                )
                results["cover"] = ("ok", out)
            except QuoteServiceError as exc:
                results["cover"] = ("error", exc.code)
            except Exception as exc:  # noqa: BLE001
                results["cover"] = ("exception", repr(exc))
        finally:
            session.close()

    t1 = threading.Thread(target=cancel_worker)
    t2 = threading.Thread(target=cover_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert "cancel" in results and "cover" in results, f"un hilo no terminó: {results}"

    verify = Session(bind=engine)
    try:
        op = verify.get(WhatsAppOperation, seed["op_id"])
        movements = (
            verify.query(FundMovement).filter(FundMovement.transaction_id == op.transaction_id).count()
            if op.transaction_id else 0
        )
        report = {
            "results": results,
            "op.status": op.status.value,
            "op.transaction_id": op.transaction_id,
            "movements": movements,
        }
        print("CANCEL_VS_COVER REPORT:", report)

        inconsistent = (
            (op.status == WhatsAppOperationStatus.COMPLETED and results["cancel"][0] == "ok")
            or (op.status == WhatsAppOperationStatus.CANCELLED and movements > 0)
        )
        if inconsistent:
            pytest.fail(f"INCONSISTENCIA cancelar-vs-cubrir reproducida: {report}")
    finally:
        verify.close()
