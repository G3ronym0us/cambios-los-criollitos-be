"""
Registrar un abono contra el saldo a favor del cliente (`WhatsAppBalanceService.
debit_for_operation`): dos abonos DISTINTOS del mismo cliente, concurrentes de verdad (ver
la nota en `test_concurrency_race_link.py` sobre por qué el fixture `db` no sirve para esto),
cada uno individualmente dentro del saldo pero juntos por encima.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.whatsapp_balance import WhatsAppBalanceEntry, WhatsAppBalanceEntryType
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests.conftest import _pair


@pytest.fixture
def seed(engine):
    """Cliente con 200 USD de saldo a favor y DOS operaciones de abono de 150 c/u -- juntas
    superan el saldo, cada una sola no."""
    session = Session(bind=engine)
    try:
        operator = User(
            username=f"op_ab_{id(session)}", email=f"op_ab_{id(session)}@test.local",
            hashed_password="x", is_active=True, is_verified=True,
        )
        session.add(operator)
        session.flush()

        pair = _pair(session, "ZELLE", "VES", 800.0)

        client = WhatsAppClient(phone="1900000ab", display_name="Abono", is_tracked=True)
        session.add(client)
        session.flush()

        session.add(WhatsAppBalanceEntry(
            client_id=client.id, entry_type=WhatsAppBalanceEntryType.CREDIT,
            amount=200.0, currency="USD",
        ))
        session.flush()

        now = datetime.now(timezone.utc)

        def _op(amount):
            op = WhatsAppOperation(
                client_id=client.id, currency_pair_id=pair.id,
                from_amount=amount, to_amount=amount * 800.0, rate_used=800.0,
                amount_side=WhatsAppAmountSide.SEND,
                status=WhatsAppOperationStatus.QUOTED, quoted_at=now,
                expires_at=now + timedelta(hours=1),
            )
            session.add(op)
            session.flush()
            return op

        op_a = _op(150.0)
        op_b = _op(150.0)
        session.commit()

        data = {
            "operator_id": operator.id, "client_id": client.id,
            "op_a_uuid": op_a.uuid, "op_b_uuid": op_b.uuid,
            "op_a_id": op_a.id, "op_b_id": op_b.id,
        }
        yield data
    finally:
        session.rollback()
        session.close()

    cleanup = Session(bind=engine)
    try:
        cleanup.query(WhatsAppBalanceEntry).filter(
            WhatsAppBalanceEntry.client_id == data["client_id"]
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOperation).filter(
            WhatsAppOperation.id.in_([data["op_a_id"], data["op_b_id"]])
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppClient).filter(WhatsAppClient.id == data["client_id"]).delete()
        cleanup.query(User).filter(User.id == data["operator_id"]).delete()
        cleanup.commit()
    finally:
        cleanup.close()


def test_two_concurrent_abonos_can_overdraw_the_balance(engine, seed):
    """
    H-8.5: sin el lock sobre el cliente, esto era reproducible 2 de 3 veces (dos tandas
    reales de campaña): 200 USD de saldo, dos abonos de 150 a la vez sobre DOS operaciones
    distintas del mismo cliente -- las dos lecturas veían 200 disponibles, las dos pasaban
    "saldo suficiente" y el saldo terminaba en -100. Con el `FOR UPDATE` sobre la fila del
    cliente, la segunda espera a que la primera comitee su DEBIT y entonces sí ve el saldo
    ya consumido -> 409 `insufficient_balance`.
    """
    barrier = threading.Barrier(2)
    results = {}

    def worker(name, op_uuid):
        session = Session(bind=engine)
        try:
            service = WhatsAppBalanceService(session)
            barrier.wait(timeout=5)
            try:
                out = service.debit_for_operation(op_uuid, 150.0, created_by_user_id=seed["operator_id"])
                results[name] = ("ok", out["balance_after"])
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
        balance = WhatsAppBalanceService(verify).get_balance(seed["client_id"])
        report = {"results": results, "final_balance": balance}
        print("ABONO RACE REPORT:", report)

        both_ok = all(v[0] == "ok" for v in results.values())
        if both_ok and balance < -0.01:
            pytest.fail(
                f"SOBREGIRO reproducido: los dos abonos de 150 pasaron con 200 de saldo y "
                f"lo dejaron en {balance:.2f} -- debería haber rechazado el segundo con "
                f"409 insufficient_balance. Reporte: {report}"
            )
        assert balance >= -0.01, f"saldo negativo pese al lock: {report}"
    finally:
        verify.close()
