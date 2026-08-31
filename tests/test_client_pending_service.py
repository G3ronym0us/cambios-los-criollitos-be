"""
Lo que le debemos al cliente: agregación, entrega en lote y deshacer.

Corre contra SQLite en memoria para que sea rápido y sin dependencias. El SQL de la
agregación se verificó además contra Postgres real apuntando la fixture a una base de
pruebas: pasa igual en los dos, que es lo que hace fiable correrlo aquí en SQLite.
"""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 40)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


# Los modelos usan tipos de Postgres que SQLite no conoce. Sólo afecta al DDL de la
# fixture: lo que se prueba es la regla, y el SQL que ejecuta el servicio es portable.
@compiles(PGUUID, "sqlite")
def _uuid_sqlite(type_, compiler, **kw):  # pragma: no cover - sólo DDL de test
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover - sólo DDL de test
    return "TEXT"

import app.models  # noqa: F401  — registra todos los modelos
from app.database.connection import Base
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import (
    WhatsAppIncomingPayment,
    WhatsAppOutgoingPayment,
    WhatsAppOutgoingSettlement,
)
from app.services.client_pending_service import ClientPendingService
from app.services.whatsapp_quote_service import QuoteServiceError

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def world(db):
    """Un cliente con un par USD/VES y otro VES/COP, más el operador que marca."""
    usd = Currency(symbol="USD", name="Dolar")
    ves = Currency(symbol="VES", name="Bolivar")
    cop = Currency(symbol="COP", name="Peso")
    db.add_all([usd, ves, cop])
    db.flush()

    usd_ves = CurrencyPair(
        from_currency_id=usd.id, to_currency_id=ves.id, pair_symbol="USD/VES", is_active=True
    )
    ves_cop = CurrencyPair(
        from_currency_id=ves.id, to_currency_id=cop.id, pair_symbol="VES/COP", is_active=True
    )
    client = WhatsAppClient(phone="+58412@c.us", display_name="Katiuska")
    other = WhatsAppClient(phone="+58414@c.us", display_name="Sin deuda")
    actor = User(username="op", email="op@x.com", hashed_password="x")
    db.add_all([usd_ves, ves_cop, client, other, actor])
    db.flush()
    db.commit()
    return {
        "client": client,
        "other": other,
        "actor": actor,
        "usd_ves": usd_ves,
        "ves_cop": ves_cop,
    }


def make_op(
    db,
    client,
    pair,
    *,
    amount=100.0,
    currency="USD",
    from_amount=100.0,
    to_amount=28000.0,
    created_at=NOW,
    status=WhatsAppOperationStatus.PENDING,
    beneficiary="Yelitza",
    uncovered=None,
):
    op = WhatsAppOperation(
        client_id=client.id,
        currency_pair_id=pair.id,
        amount=amount,
        currency=currency,
        from_amount=from_amount,
        to_amount=to_amount,
        rate_used=to_amount / from_amount if from_amount else 1,
        amount_side=WhatsAppAmountSide.SEND,
        status=status,
        expires_at=created_at + timedelta(hours=1),
        created_at=created_at,
        beneficiary_alias=beneficiary,
        uncovered_amount=uncovered,
    )
    db.add(op)
    db.flush()
    return op


class TestAggregation:
    def test_agrupa_por_par_y_no_suma_monedas_distintas(self, db, world):
        make_op(db, world["client"], world["usd_ves"], amount=100)
        make_op(db, world["client"], world["usd_ves"], amount=50)
        make_op(
            db, world["client"], world["ves_cop"],
            amount=5000, currency="VES", from_amount=5000, to_amount=1000,
        )
        db.commit()

        result = ClientPendingService(db).pending_by_client_ids([world["client"].id])
        entries = {e["pair_symbol"]: e for e in result[world["client"].id]}

        assert entries["USD/VES"]["amount"] == 150
        assert entries["USD/VES"]["currency"] == "USD"
        assert entries["USD/VES"]["operations"] == 2
        assert entries["VES/COP"]["amount"] == 5000
        assert entries["VES/COP"]["currency"] == "VES"

    def test_descuenta_lo_ya_cubierto_y_el_hueco_declarado(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100, uncovered=30)
        pago = WhatsAppOutgoingPayment(
            client_phone=world["client"].phone, amount=5600, currency="VES"
        )
        db.add(pago)
        db.flush()
        db.add(
            WhatsAppOutgoingSettlement(
                outgoing_payment_id=pago.id, whatsapp_operation_id=op.id, settled_amount=20
            )
        )
        db.commit()

        result = ClientPendingService(db).pending_by_client_ids([world["client"].id])
        assert result[world["client"].id][0]["amount"] == 50  # 100 - 20 - 30

    def test_lo_cubierto_del_todo_no_aparece(self, db, world):
        make_op(db, world["client"], world["usd_ves"], amount=100, uncovered=100)
        db.commit()
        assert ClientPendingService(db).pending_by_client_ids([world["client"].id]) == {}

    def test_canceladas_y_completadas_quedan_fuera(self, db, world):
        make_op(db, world["client"], world["usd_ves"], status=WhatsAppOperationStatus.CANCELLED)
        make_op(db, world["client"], world["usd_ves"], status=WhatsAppOperationStatus.COMPLETED)
        db.commit()
        assert ClientPendingService(db).pending_by_client_ids([world["client"].id]) == {}

    def test_el_equivalente_se_deriva_de_la_proporcion_del_trato(self, db, world):
        make_op(db, world["client"], world["usd_ves"], amount=100, from_amount=100, to_amount=28000)
        db.commit()

        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["payout_amount"] == 28000
        assert entry["payout_currency"] == "VES"

    def test_si_una_no_se_puede_convertir_el_grupo_entero_va_a_null(self, db, world):
        make_op(db, world["client"], world["usd_ves"], amount=100, from_amount=100, to_amount=28000)
        make_op(db, world["client"], world["usd_ves"], amount=50, from_amount=0, to_amount=0)
        db.commit()

        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["payout_amount"] is None

    def test_la_antiguedad_sale_del_comprobante_no_de_la_operacion(self, db, world):
        vieja = NOW - timedelta(days=10)
        # Creada a mano hoy, pero el dinero entró hace diez días.
        op = make_op(db, world["client"], world["usd_ves"], created_at=NOW)
        db.add(
            WhatsAppIncomingPayment(
                client_phone=world["client"].phone,
                amount=100,
                currency="USD",
                whatsapp_operation_id=op.id,
                created_at=vieja,
            )
        )
        db.commit()

        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["oldest_at"].replace(tzinfo=timezone.utc) == vieja

    def test_sin_comprobante_se_cae_a_la_fecha_de_la_operacion(self, db, world):
        make_op(db, world["client"], world["usd_ves"], created_at=NOW)
        db.commit()

        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["oldest_at"].replace(tzinfo=timezone.utc) == NOW

    def test_el_par_acota_la_deuda_que_se_devuelve(self, db, world):
        make_op(db, world["client"], world["usd_ves"], amount=100)
        make_op(
            db, world["client"], world["ves_cop"],
            amount=5000, currency="VES", from_amount=5000, to_amount=1000,
        )
        db.commit()

        result = ClientPendingService(db).pending_by_client_ids(
            [world["client"].id], pair="USD/VES"
        )
        entries = result[world["client"].id]
        assert len(entries) == 1
        assert entries[0]["pair_symbol"] == "USD/VES"

    def test_client_ids_with_pending(self, db, world):
        make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()

        service = ClientPendingService(db)
        assert service.client_ids_with_pending() == [world["client"].id]
        assert service.client_ids_with_pending(pair="VES/COP") == []


class TestDeliver:
    def test_marcar_suma_al_hueco_y_deja_rastro(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()

        batch = ClientPendingService(db).deliver(
            world["client"].uuid,
            [{"operation_uuid": str(op.uuid), "amount": None}],
            note="efectivo",
            actor=world["actor"],
        )

        db.refresh(op)
        assert op.uncovered_amount == 100
        assert op.uncovered_reason == "CASH"
        assert batch["amount"] == 100
        assert batch["operations"] == 1
        assert batch["created_by_username"] == "op"
        assert batch["items"][0]["previous_uncovered"] is None

    def test_una_segunda_entrega_parcial_se_suma_a_la_primera(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()
        service = ClientPendingService(db)

        service.deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid), "amount": 40}], None,
            world["actor"],
        )
        db.refresh(op)
        assert op.uncovered_amount == 40

        # El segundo lote no reemplaza al primero: lo acumula.
        service.deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid), "amount": 60}], None,
            world["actor"],
        )
        db.refresh(op)
        assert op.uncovered_amount == 100

    def test_no_se_puede_entregar_mas_de_lo_que_se_debe(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()

        with pytest.raises(QuoteServiceError) as exc:
            ClientPendingService(db).deliver(
                world["client"].uuid, [{"operation_uuid": str(op.uuid), "amount": 150}], None,
                world["actor"],
            )
        assert exc.value.code == "amount_exceeds_pending"

    def test_sin_beneficiario_no_se_entrega(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], beneficiary=None)
        db.commit()

        with pytest.raises(QuoteServiceError) as exc:
            ClientPendingService(db).deliver(
                world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
            )
        assert exc.value.code == "operation_without_beneficiary"

    def test_una_operacion_de_otro_cliente_no_entra(self, db, world):
        ajena = make_op(db, world["other"], world["usd_ves"], amount=100)
        db.commit()

        with pytest.raises(QuoteServiceError) as exc:
            ClientPendingService(db).deliver(
                world["client"].uuid, [{"operation_uuid": str(ajena.uuid)}], None, world["actor"]
            )
        assert exc.value.code == "operation_client_mismatch"

    def test_el_lote_es_todo_o_nada(self, db, world):
        buena = make_op(db, world["client"], world["usd_ves"], amount=100)
        trabada = make_op(db, world["client"], world["usd_ves"], amount=50, beneficiary=None)
        db.commit()

        with pytest.raises(QuoteServiceError):
            ClientPendingService(db).deliver(
                world["client"].uuid,
                [
                    {"operation_uuid": str(buena.uuid)},
                    {"operation_uuid": str(trabada.uuid)},
                ],
                None,
                world["actor"],
            )

        db.rollback()
        db.refresh(buena)
        # La primera NO quedó marcada aunque su turno pasó sin problemas.
        assert buena.uncovered_amount is None


class TestUndo:
    def test_deshacer_repone_lo_que_habia_y_no_borra_el_lote(self, db, world):
        # Ya tenía 30 declarados de antes: deshacer no puede poner cero.
        op = make_op(db, world["client"], world["usd_ves"], amount=100, uncovered=30)
        db.commit()
        service = ClientPendingService(db)

        batch = service.deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid), "amount": 70}], None,
            world["actor"],
        )
        db.refresh(op)
        assert op.uncovered_amount == 100

        undone = service.undo(world["client"].uuid, batch["uuid"], world["actor"])
        db.refresh(op)
        assert op.uncovered_amount == 30
        assert undone["undone_at"] is not None
        assert undone["undone_by_username"] == "op"

    def test_no_se_deshace_dos_veces(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()
        service = ClientPendingService(db)
        batch = service.deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
        )
        service.undo(world["client"].uuid, batch["uuid"], world["actor"])

        with pytest.raises(QuoteServiceError) as exc:
            service.undo(world["client"].uuid, batch["uuid"], world["actor"])
        assert exc.value.code == "already_undone"

    def test_el_lote_de_otro_cliente_no_se_deshace(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()
        service = ClientPendingService(db)
        batch = service.deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
        )

        with pytest.raises(QuoteServiceError) as exc:
            service.undo(world["other"].uuid, batch["uuid"], world["actor"])
        assert exc.value.code == "delivery_not_found"

    def test_la_deuda_vuelve_a_aparecer_tras_deshacer(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()
        service = ClientPendingService(db)
        batch = service.deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
        )
        assert service.pending_by_client_ids([world["client"].id]) == {}

        service.undo(world["client"].uuid, batch["uuid"], world["actor"])
        assert service.pending_by_client_ids([world["client"].id])[world["client"].id][0][
            "amount"
        ] == 100
