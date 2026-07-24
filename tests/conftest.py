"""
Harness de integración contra un Postgres real (BD `tasas_test`, aislada y recreada por
sesión). Cada test corre dentro de una transacción que se revierte al final, así que los
`commit()` de los servicios quedan aislados y los tests no se pisan.

Se prefiere Postgres real sobre mocks: la contabilidad por valor toca muchas tablas
(operaciones, pagos, reparto, cobertura, transacción, movimiento de fondo) y un mock del
`Session` se rompe con cualquier refactor. Aquí se prueba el comportamiento observable.

Si no hay Postgres local en :5433, los tests de integración se SALTAN (no fallan), para no
tumbar la suite de tests unitarios que sí corre en cualquier lado.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Registra todos los modelos en Base.metadata.
import app.models  # noqa: F401
from app.database.connection import Base
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.models.fund import FundGroup, FundGroupMember
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient

ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://tasas_user:tasas_password@localhost:5433/tasas_db"
)
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tasas_user:tasas_password@localhost:5433/tasas_test"
)


def _postgres_available() -> bool:
    try:
        eng = create_engine(ADMIN_URL, connect_args={"connect_timeout": 2})
        with eng.connect():
            return True
    except Exception:
        return False
    finally:
        try:
            eng.dispose()
        except Exception:
            pass


@pytest.fixture(scope="session")
def engine():
    if not _postgres_available():
        pytest.skip("Postgres local (:5433) no disponible; se saltan los tests de integración")

    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS tasas_test"))
        conn.execute(text("CREATE DATABASE tasas_test"))
    admin.dispose()

    eng = create_engine(TEST_URL)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()

    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='tasas_test'"
        ))
        conn.execute(text("DROP DATABASE IF EXISTS tasas_test"))
    admin.dispose()


@pytest.fixture
def db(engine):
    """Sesión envuelta en una transacción que se revierte; los commit() de los servicios
    caen en savepoints, así que el test queda aislado."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


# --------------------------------------------------------------------------- fixtures de datos

@pytest.fixture
def operator(db) -> User:
    user = User(
        username="operador", email="op@test.local", hashed_password="x",
        is_active=True, is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def bot_user(db) -> User:
    """Usuario de servicio del bot (algunos flujos lo exigen por email)."""
    from app.core.config import settings
    user = User(
        username="whatsapp_bot", email=settings.BOT_SERVICE_USER_EMAIL,
        hashed_password="x", is_active=True, is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


def _pair(db, frm: str, to: str, rate: float) -> CurrencyPair:
    pair = CurrencyPair(
        from_currency_id=_currency(db, frm).id,
        to_currency_id=_currency(db, to).id,
        pair_symbol=f"{frm}-{to}",
        is_active=True,
    )
    db.add(pair)
    db.flush()
    db.add(ExchangeRate(
        currency_pair_id=pair.id, from_currency=frm, to_currency=to, rate=rate, is_active=True,
    ))
    db.flush()
    return pair


@pytest.fixture
def pairs(db) -> dict:
    """Los pares del caso real, con tasa activa. ZELLE liquida como USD → USDT 1:1 (sin tasa)."""
    return {
        "ZELLE-BRL": _pair(db, "ZELLE", "BRL", 4.5702),
        "ZELLE-VES": _pair(db, "ZELLE", "VES", 782.92),
    }


@pytest.fixture
def fund(db, operator) -> FundGroup:
    group = FundGroup(name="Zelle/Paypal", currency="USD", is_active=True)
    db.add(group)
    db.flush()
    db.add(FundGroupMember(group_id=group.id, user_id=operator.id, is_fund_manager=True))
    db.flush()
    return group


@pytest.fixture
def client(db, pairs) -> WhatsAppClient:
    row = WhatsAppClient(
        phone="13174961478", display_name="Naldin", is_tracked=True,
        preferred_pair_id=pairs["ZELLE-BRL"].id,
    )
    db.add(row)
    db.flush()
    return row
