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
    WhatsAppDeliveryStatus,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import (
    WhatsAppIncomingPayment,
    WhatsAppOutgoingPayment,
    WhatsAppOutgoingSettlement,
)
from app.schemas.whatsapp import WhatsAppOperationResponse
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
    collected=None,
    incoming_at=NOW,
    delivery_status=None,
):
    """
    Una operación del cliente. `incoming_at=None` la deja SIN comprobante entrante, que es
    como decir «su dinero no ha llegado»: entonces no es deuda por mucho que le falte pago.
    """
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
        collected_amount=collected,
        delivery_status=delivery_status,
    )
    db.add(op)
    db.flush()
    if incoming_at is not None:
        db.add(WhatsAppIncomingPayment(
            client_phone=client.phone, amount=amount, currency=currency,
            whatsapp_operation_id=op.id, created_at=incoming_at,
        ))
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
        make_op(db, world["client"], world["usd_ves"], created_at=NOW, incoming_at=vieja)
        db.commit()

        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["oldest_at"].replace(tzinfo=timezone.utc) == vieja

    def test_sin_comprobante_entrante_no_hay_deuda(self, db, world):
        """No le debemos nada hasta que su dinero llega: antes es un trato en el papel."""
        make_op(db, world["client"], world["usd_ves"], incoming_at=None)
        db.commit()
        assert ClientPendingService(db).pending_by_client_ids([world["client"].id]) == {}

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


class TestLastOutgoingPaymentAt:
    """La fecha que el listado de Operaciones enseña: cuándo salió la plata."""

    def test_toma_el_comprobante_de_salida_mas_reciente(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"], amount=100)
        for dia, monto in ((10, 20), (2, 30)):
            pago = WhatsAppOutgoingPayment(
                client_phone=world["client"].phone, amount=monto * 280, currency="VES",
                created_at=NOW - timedelta(days=dia),
            )
            db.add(pago)
            db.flush()
            db.add(WhatsAppOutgoingSettlement(
                outgoing_payment_id=pago.id, whatsapp_operation_id=op.id, settled_amount=monto,
            ))
        db.commit()
        db.refresh(op)

        # Un trato pagado en dos veces no está pagado hasta el último comprobante.
        assert op.last_outgoing_payment_at.replace(tzinfo=timezone.utc) == NOW - timedelta(days=2)

    def test_sin_comprobante_de_salida_no_hay_fecha(self, db, world):
        op = make_op(db, world["client"], world["usd_ves"])
        db.commit()
        db.refresh(op)
        assert op.last_outgoing_payment_at is None


@pytest.fixture()
def cash_pair(db, world):
    """
    Un par que se cambia en efectivo, mano a mano.

    Hace de USD-VES de producción: el cliente llega con billetes, así que no hay comprobante
    entrante que adjuntar ni lo habrá. Va sobre COP porque un par es único por combinación de
    monedas y el USD/VES normal ya ocupa la suya — y precisamente hace falta que los dos
    convivan, porque la regla tiene que indultar a uno sin indultar al otro.
    """
    usd = db.query(Currency).filter(Currency.symbol == "USD").one()
    cop = db.query(Currency).filter(Currency.symbol == "COP").one()
    pair = CurrencyPair(
        from_currency_id=usd.id,
        to_currency_id=cop.id,
        pair_symbol="USD-COP efectivo",
        is_active=True,
        settles_in_cash=True,
    )
    db.add(pair)
    db.flush()
    db.commit()
    return pair


class TestParesDeEfectivo:
    """
    La excepción a «sólo debemos lo que ya nos pagaron».

    Exigir el comprobante entrante es correcto en Zelle o PayPal, donde siempre está. En un
    par de efectivo no existe, y exigirlo borraba el par ENTERO de la pantalla: en producción
    USD-VES tenía 134 operaciones sin cuadrar y cero con entrante.
    """

    def test_una_pendiente_sin_comprobante_si_cuenta(self, db, world, cash_pair):
        make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        db.commit()

        entries = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ]
        assert [(e["pair_symbol"], e["amount"], e["operations"]) for e in entries] == [
            ("USD-COP efectivo", 100, 1)
        ]

    def test_una_cotizacion_sin_comprobante_no_cuenta(self, db, world, cash_pair):
        """
        Sin entrante, lo único que separa el trato hecho de la cotización abandonada es el
        estado. Contar las QUOTED metía 106 cotizaciones muertas como deuda.
        """
        make_op(
            db, world["client"], cash_pair, amount=500,
            incoming_at=None, status=WhatsAppOperationStatus.QUOTED,
        )
        db.commit()
        assert ClientPendingService(db).pending_by_client_ids([world["client"].id]) == {}

    def test_una_cotizacion_con_comprobante_sigue_contando(self, db, world, cash_pair):
        """El estado sólo manda cuando falta el entrante: si el entrante está, es deuda."""
        make_op(
            db, world["client"], cash_pair, amount=80,
            status=WhatsAppOperationStatus.QUOTED, incoming_at=NOW,
        )
        db.commit()

        entries = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ]
        assert entries[0]["amount"] == 80

    def test_el_indulto_no_alcanza_a_los_demas_pares(self, db, world, cash_pair):
        """Tener un par de efectivo no relaja la regla en los que sí llevan comprobante."""
        make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        make_op(db, world["client"], world["usd_ves"], amount=70, incoming_at=None)
        db.commit()

        entries = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ]
        assert [e["pair_symbol"] for e in entries] == ["USD-COP efectivo"]

    def test_la_entrada_dice_de_que_lado_esta_la_deuda(self, db, world, cash_pair):
        """
        La misma cifra con el rótulo opuesto: en efectivo los bolívares ya salieron y lo que
        falta es el dinero del cliente. La pantalla necesita el dato para no decirlo al revés.
        """
        make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        make_op(db, world["client"], world["usd_ves"], amount=70)
        db.commit()

        entries = {
            e["pair_symbol"]: e
            for e in ClientPendingService(db).pending_by_client_ids([world["client"].id])[
                world["client"].id
            ]
        }
        assert entries["USD-COP efectivo"]["settles_in_cash"] is True
        assert entries["USD/VES"]["settles_in_cash"] is False

    def test_sin_comprobante_la_antiguedad_sale_de_la_operacion(self, db, world, cash_pair):
        """No hay entrante del que sacar la fecha, y la de la operación es lo mejor que hay."""
        nacio = NOW - timedelta(days=6)
        make_op(db, world["client"], cash_pair, amount=100, created_at=nacio, incoming_at=None)
        db.commit()

        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["oldest_at"].replace(tzinfo=timezone.utc) == nacio

    def test_lo_ya_cobrado_se_descuenta(self, db, world, cash_pair):
        make_op(db, world["client"], cash_pair, amount=100, collected=40, incoming_at=None)
        db.commit()

        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["amount"] == 60

    def test_lo_cobrado_del_todo_desaparece(self, db, world, cash_pair):
        make_op(db, world["client"], cash_pair, amount=100, collected=100, incoming_at=None)
        db.commit()
        assert ClientPendingService(db).pending_by_client_ids([world["client"].id]) == {}

    def test_lo_que_cubrimos_nosotros_no_salda_lo_que_el_debe(self, db, world, cash_pair):
        """
        El corazón del asunto. Vincular el comprobante en bolívares cubre NUESTRA pata: la
        operación queda sin nada por cuadrar y el cliente sigue sin traer un solo dólar.

        Medirlo con lo cubierto sacaba de la lista justo a las que hay que ir a cobrar — y
        eran todas, porque una operación creada desde su propio comprobante de salida nace
        cubierta. El par entero desaparecía el día que se pagaba.
        """
        op = make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        pago = WhatsAppOutgoingPayment(
            client_phone=world["client"].phone, amount=28000, currency="VES"
        )
        db.add(pago)
        db.flush()
        db.add(
            WhatsAppOutgoingSettlement(
                outgoing_payment_id=pago.id, whatsapp_operation_id=op.id, settled_amount=100
            )
        )
        db.commit()

        assert op.dict()["pending_amount"] == 0  # nuestra pata, cubierta
        entry = ClientPendingService(db).pending_by_client_ids([world["client"].id])[
            world["client"].id
        ][0]
        assert entry["amount"] == 100  # la suya, entera

    def test_entregar_y_deshacer_funcionan_sin_comprobante(self, db, world, cash_pair):
        """La entrega en lote es lo que se usa justamente aquí: cobrar efectivo a mano."""
        op = make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
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

    def test_client_ids_with_pending_los_ve(self, db, world, cash_pair):
        make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        db.commit()
        assert ClientPendingService(db).client_ids_with_pending() == [world["client"].id]

    def test_se_entrega_sin_beneficiario(self, db, world, cash_pair):
        """
        El gesto está invertido: los bolívares ya salieron y lo que se marca es que el
        CLIENTE pagó. A quién se le entregó lo dice el comprobante saliente, que ya cuelga
        de la operación, así que el campo sobra. Exigirlo trababa 117 de las 120 filas de
        USD-VES en producción con «Falta dato», y dejaba la cola sin usar.
        """
        op = make_op(
            db, world["client"], cash_pair, amount=100, beneficiary=None, incoming_at=None
        )
        db.commit()

        batch = ClientPendingService(db).deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
        )

        db.refresh(op)
        assert op.collected_amount == 100
        assert batch["operations"] == 1

    def test_en_un_par_normal_el_beneficiario_se_sigue_exigiendo(self, db, world, cash_pair):
        """La exención es del efectivo, no de todos: allí sí vamos a mandarle plata a alguien."""
        op = make_op(db, world["client"], world["usd_ves"], amount=100, beneficiary=None)
        db.commit()

        with pytest.raises(QuoteServiceError) as exc:
            ClientPendingService(db).deliver(
                world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
            )
        assert exc.value.code == "operation_without_beneficiary"


class TestCobroDelEfectivo:
    """
    Marcar que el CLIENTE pagó, que es el gesto de un par de efectivo.

    Es la otra pata del trato y hasta ahora no tenía dónde anotarse: marcar escribía
    `uncovered_amount` —o sea declaraba cubierta la pata NUESTRA— y la operación se quedaba
    en PENDING por mucho que el cliente hubiera traído los billetes.
    """

    def test_cobrar_entero_cierra_la_operacion(self, db, world, cash_pair):
        op = make_op(
            db, world["client"], cash_pair, amount=100, incoming_at=None,
            delivery_status=WhatsAppDeliveryStatus.PENDING,
        )
        db.commit()

        ClientPendingService(db).deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
        )

        db.refresh(op)
        assert op.collected_amount == 100
        # Lo que faltaba: sin esto Pagos la seguía enseñando «Pendiente» para siempre.
        assert op.status == WhatsAppOperationStatus.COMPLETED
        assert op.delivery_status == WhatsAppDeliveryStatus.RECEIVED
        assert op.completed_at is not None

    def test_un_cobro_parcial_deja_la_operacion_abierta(self, db, world, cash_pair):
        """Trae 60 de los 100: se anotan los 60 y sigue debiendo 40, no se cierra nada."""
        op = make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        db.commit()
        service = ClientPendingService(db)

        service.deliver(
            world["client"].uuid,
            [{"operation_uuid": str(op.uuid), "amount": 60}],
            None,
            world["actor"],
        )

        db.refresh(op)
        assert op.collected_amount == 60
        assert op.status == WhatsAppOperationStatus.PENDING
        assert service.pending_by_client_ids([world["client"].id])[world["client"].id][0][
            "amount"
        ] == 40

    def test_dos_cobros_parciales_se_suman_hasta_cerrarla(self, db, world, cash_pair):
        """`collected_amount` es el total, no un incremento: el segundo pago se le suma."""
        op = make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        db.commit()
        service = ClientPendingService(db)

        service.deliver(
            world["client"].uuid,
            [{"operation_uuid": str(op.uuid), "amount": 60}],
            None,
            world["actor"],
        )
        service.deliver(
            world["client"].uuid,
            [{"operation_uuid": str(op.uuid), "amount": 40}],
            None,
            world["actor"],
        )

        db.refresh(op)
        assert op.collected_amount == 100
        assert op.status == WhatsAppOperationStatus.COMPLETED

    def test_no_se_puede_cobrar_mas_de_lo_que_debe(self, db, world, cash_pair):
        op = make_op(db, world["client"], cash_pair, amount=100, collected=80, incoming_at=None)
        db.commit()

        with pytest.raises(QuoteServiceError) as exc:
            ClientPendingService(db).deliver(
                world["client"].uuid,
                [{"operation_uuid": str(op.uuid), "amount": 30}],
                None,
                world["actor"],
            )
        assert exc.value.code == "amount_exceeds_pending"

    def test_deshacer_un_cobro_reabre_la_operacion(self, db, world, cash_pair):
        op = make_op(
            db, world["client"], cash_pair, amount=100, incoming_at=None,
            delivery_status=WhatsAppDeliveryStatus.PENDING,
        )
        db.commit()
        service = ClientPendingService(db)
        batch = service.deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
        )

        service.undo(world["client"].uuid, batch["uuid"], world["actor"])

        db.refresh(op)
        assert op.collected_amount is None
        assert op.status == WhatsAppOperationStatus.PENDING
        assert op.delivery_status == WhatsAppDeliveryStatus.PENDING
        assert op.completed_at is None

    def test_el_cobro_no_toca_lo_que_cubrimos_nosotros(self, db, world, cash_pair):
        """Las dos patas son columnas distintas: cobrar no declara cubierto nada nuestro."""
        op = make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        db.commit()

        ClientPendingService(db).deliver(
            world["client"].uuid, [{"operation_uuid": str(op.uuid)}], None, world["actor"]
        )

        db.refresh(op)
        assert op.uncovered_amount is None

    def test_un_lote_puede_llevar_un_cobro_y_una_entrega(self, db, world, cash_pair):
        """
        Un cliente tiene las dos cosas a la vez y salda de una vez. El gesto lo decide el par
        de cada operación, así que cada fila escribe en su columna.
        """
        efectivo = make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        normal = make_op(db, world["client"], world["usd_ves"], amount=70)
        db.commit()

        batch = ClientPendingService(db).deliver(
            world["client"].uuid,
            [
                {"operation_uuid": str(efectivo.uuid)},
                {"operation_uuid": str(normal.uuid)},
            ],
            None,
            world["actor"],
        )

        db.refresh(efectivo)
        db.refresh(normal)
        assert (efectivo.collected_amount, efectivo.uncovered_amount) == (100, None)
        assert (normal.uncovered_amount, normal.collected_amount) == (70, None)
        assert {item["kind"] for item in batch["items"]} == {"COLLECTION", "DELIVERY"}


class TestLoQueLlegaAlFront:
    """Lo que la operación serializa: sin esto la pantalla no puede aplicar la regla."""

    def test_la_operacion_dice_si_su_par_es_de_efectivo(self, db, world, cash_pair):
        efectivo = make_op(db, world["client"], cash_pair, amount=100, incoming_at=None)
        normal = make_op(db, world["client"], world["usd_ves"], amount=100)
        db.commit()

        assert efectivo.dict()["settles_in_cash"] is True
        assert normal.dict()["settles_in_cash"] is False

    def test_la_respuesta_no_se_come_lo_declarado_en_efectivo(self, db, world, cash_pair):
        """
        `uncovered_amount` no está en `delivered_amount` —que sólo cuenta comprobantes— pero
        sí está descontado de `pending_amount`. Si el esquema no lo deja pasar, una operación
        de 75 con 40 entregados llega al front como si valiera 35 desde el principio y los 40
        no aparecen en ninguna parte.
        """
        op = make_op(db, world["client"], cash_pair, amount=75, uncovered=40, incoming_at=None)
        db.commit()

        response = WhatsAppOperationResponse.model_validate(op.dict())
        assert response.amount == 75
        assert response.uncovered_amount == 40
        assert response.uncovered_reason is None
        assert response.pending_amount == 35
        assert response.settles_in_cash is True
        assert response.first_incoming_payment_at is None
