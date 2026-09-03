"""
Doble clic en "Crear operación" (armar una operación NUEVA desde un comprobante suelto,
`create_operation_from_payment`): dos peticiones concurrentes de verdad (hilos, sesiones y
commits reales -- ver la nota en `test_concurrency_race_link.py` sobre por qué el fixture
`db` no sirve para esto) sobre el MISMO comprobante sin operación todavía.
"""

import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.fund import FundGroup, FundGroupMember, FundMovement
from app.models.transaction import Transaction, TransactionProfitSplit
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperation
from app.models.whatsapp_payment import WhatsAppOutgoingPayment
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests.conftest import _pair


@pytest.fixture
def seed(engine):
    """Un comprobante saliente suelto (sin operación) y el par para armarla.

    El cliente ya EXISTE de antes (caso normal: no es la primera vez que escribe) -- así
    `upsert_client` sólo hace un SELECT que las dos carreras pasan igual, sin que la unique
    constraint del teléfono tape accidentalmente la carrera real (ver el primer intento de
    esta prueba: con cliente nuevo, el choque en `ix_whatsapp_clients_phone` la enmascaraba).
    """
    session = Session(bind=engine)
    try:
        operator = User(
            username=f"op_co_{id(session)}", email=f"op_co_{id(session)}@test.local",
            hashed_password="x", is_active=True, is_verified=True,
        )
        session.add(operator)
        session.flush()

        pair = _pair(session, "ZELLE", "VES", 800.0)

        session.add(WhatsAppClient(phone="1900000cop", display_name="CreateOp", is_tracked=True))
        session.flush()

        now = datetime.now(timezone.utc)
        payment = WhatsAppOutgoingPayment(
            client_phone="1900000cop", amount=176_000.0, currency="VES", created_at=now,
        )
        session.add(payment)
        session.flush()
        session.commit()

        data = {"operator_id": operator.id, "payment_id": payment.id}
        yield data
    finally:
        session.rollback()
        session.close()

    cleanup = Session(bind=engine)
    try:
        op_ids = [
            row[0] for row in cleanup.query(WhatsAppOperation.id, WhatsAppOperation.transaction_id)
            .join(
                WhatsAppOutgoingPayment,
                WhatsAppOutgoingPayment.whatsapp_operation_id == WhatsAppOperation.id,
            )
            .filter(WhatsAppOutgoingPayment.id == data["payment_id"])
            .all()
        ]
        # El bug bajo prueba puede dejar una op "huérfana" que el FK del pago ya no señala:
        # búscalas también por el cliente que crea `_resolve_operation_client` para este teléfono.
        client = cleanup.query(WhatsAppClient).filter(WhatsAppClient.phone == "1900000cop").first()
        all_op_ids = set(op_ids)
        if client is not None:
            all_op_ids |= {
                row[0] for row in cleanup.query(WhatsAppOperation.id).filter(
                    WhatsAppOperation.client_id == client.id
                ).all()
            }
        tx_ids = [
            row[0] for row in cleanup.query(WhatsAppOperation.transaction_id).filter(
                WhatsAppOperation.id.in_(all_op_ids)
            ).all() if row[0] is not None
        ]
        cleanup.query(FundMovement).filter(FundMovement.transaction_id.in_(tx_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(TransactionProfitSplit).filter(
            TransactionProfitSplit.transaction_id.in_(tx_ids)
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOutgoingPayment).filter(
            WhatsAppOutgoingPayment.id == data["payment_id"]
        ).delete(synchronize_session=False)
        cleanup.query(WhatsAppOperation).filter(WhatsAppOperation.id.in_(all_op_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(Transaction).filter(Transaction.id.in_(tx_ids)).delete(
            synchronize_session=False
        )
        if client is not None:
            cleanup.query(WhatsAppClient).filter(WhatsAppClient.id == client.id).delete()
        # Fondos y grupos que pudieran haberse tocado quedan; este flujo no crea fondo (no se
        # pasa fund_group_uuid), así que no hay nada más que limpiar salvo el operador.
        cleanup.query(User).filter(User.id == data["operator_id"]).delete()
        cleanup.commit()
    finally:
        cleanup.close()


def test_double_click_create_operation_from_the_same_receipt(engine, seed):
    """
    Dos peticiones a la vez para "crear operación desde este comprobante" (doble clic real:
    nada en el backend impide mandar la misma petición dos veces mientras la primera todavía
    no respondió). `create_operation_from_payment` NO comprueba
    `row.whatsapp_operation_id is not None` en ningún punto -- a diferencia de `set_operation`,
    que si vincula a una op EXISTENTE sí lo rechaza (409 `payment_already_linked`).

    H-8.3: con cliente YA EXISTENTE (el caso normal), esto era reproducible 3 de 3 veces: las
    DOS llamadas creaban cada una su propia `WhatsAppOperation` completa -- las dos terminaban
    COMPLETED, con transacción y fondo propios -- por un solo pago real. Ahora
    `create_operation_from_payment` bloquea el comprobante y rechaza la segunda con 409
    `payment_already_linked`, igual que ya hacía `set_operation` al vincular a una op
    existente.
    """
    barrier = threading.Barrier(2)
    results = {}

    def worker(name):
        session = Session(bind=engine)
        try:
            service = WhatsAppPaymentService(session)
            barrier.wait(timeout=5)
            try:
                out = service.create_operation_from_payment(
                    "outgoing", seed["payment_id"], "ZELLE", "VES", 220, 176_000.0,
                    recorded_by_user_id=seed["operator_id"],
                )
                results[name] = ("ok", out["uuid"])
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
        payment = verify.get(WhatsAppOutgoingPayment, seed["payment_id"])
        ok_uuids = [v[1] for v in results.values() if v[0] == "ok"]
        ops_created = (
            verify.query(WhatsAppOperation).filter(WhatsAppOperation.uuid.in_(ok_uuids)).all()
            if ok_uuids else []
        )
        report = {
            "results": results,
            "payment.whatsapp_operation_id": payment.whatsapp_operation_id,
            "operations_created": [(o.id, o.uuid, o.status.value) for o in ops_created],
        }
        print("CREATE_OPERATION RACE REPORT:", report)

        outcomes = sorted(r[0] for r in results.values())
        assert outcomes == ["error", "ok"], f"se esperaba un ok y un error 409: {report}"
        rejected = next(v for v in results.values() if v[0] == "error")
        assert rejected[1] == "payment_already_linked", report

        assert len(ops_created) == 1, (
            f"DUPLICADO: el doble clic en 'crear operación' creó {len(ops_created)} "
            f"operaciones desde el mismo comprobante pese al lock. Reporte: {report}"
        )
    finally:
        verify.close()
