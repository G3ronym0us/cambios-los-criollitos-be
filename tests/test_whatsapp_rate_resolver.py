"""
Tests del WhatsAppRateResolver — verifican paridad con whatsapp-bot/src/rates.ts.

Funciones puras (apply_rate, rate_with_margin) no necesitan DB.
Para los lookups (direct/inverse/USDT bridge) se monta una SQLite en memoria
con un schema mínimo de exchange_rates (un subset del Postgres real).
"""

import pytest
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

from app.services.whatsapp_rate_resolver import WhatsAppRateResolver, RateEntry


# ---------- Schema mínimo en SQLite (espejo del Postgres) ----------

TestBase = declarative_base()


class FakeExchangeRate(TestBase):
    """Réplica simplificada de ExchangeRate para tests, sin FKs."""
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True)
    currency_pair_id = Column(Integer, nullable=True)
    from_currency = Column(String(10), nullable=False)
    to_currency = Column(String(10), nullable=False)
    rate = Column(Float, nullable=False)
    is_active = Column(Boolean, default=True)
    inverse_percentage = Column(Boolean, default=False)
    percentage = Column(Float, nullable=True)
    manual_rate = Column(Float, nullable=True)
    is_manual = Column(Boolean, default=False)
    automatic_rate = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)

    @property
    def base_rate(self) -> float:
        """Espejo de ExchangeRate.base_rate del modelo real."""
        adjusted = self.automatic_rate if self.is_manual and self.automatic_rate else self.rate
        if self.percentage is None:
            return adjusted
        pct = self.percentage / 100
        if self.inverse_percentage:
            return adjusted * (1 - pct)
        return adjusted / (1 - pct)


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    TestBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Hack: parchear el modelo real para que el resolver use FakeExchangeRate
    from app.services import whatsapp_rate_resolver as resolver_module
    monkeypatch.setattr(resolver_module, "ExchangeRate", FakeExchangeRate)

    yield session
    session.close()


def _add_rate(session, from_c, to_c, rate, *, inverse=False, percentage=None,
              is_active=True, created_at=None):
    er = FakeExchangeRate(
        from_currency=from_c,
        to_currency=to_c,
        rate=rate,
        inverse_percentage=inverse,
        percentage=percentage,
        is_active=is_active,
        created_at=created_at or datetime.utcnow(),
    )
    session.add(er)
    session.commit()
    return er


# ---------- Funciones puras ----------

class TestApplyRate:
    def test_normal_rate(self):
        # 100 USDT * 36 = 3600 VES
        assert WhatsAppRateResolver.apply_rate(100, 36, False) == 3600

    def test_inverse_rate(self):
        # 100 VES / 36 = 2.777... USDT (inverso)
        assert WhatsAppRateResolver.apply_rate(100, 36, True) == pytest.approx(100 / 36)


class TestRateWithMargin:
    def test_normal_margin(self):
        # baseRate=100, margin=10% → 100 * 0.9 = 90
        assert WhatsAppRateResolver.rate_with_margin(100, 10, False) == pytest.approx(90)

    def test_inverse_margin(self):
        # baseRate=100, margin=10% inverso → 100 / 0.9 ≈ 111.11
        assert WhatsAppRateResolver.rate_with_margin(100, 10, True) == pytest.approx(100 / 0.9)

    def test_invalid_margin(self):
        assert WhatsAppRateResolver.rate_with_margin(100, 100, False) is None
        assert WhatsAppRateResolver.rate_with_margin(100, 150, False) is None


# ---------- Resolver (con DB) ----------

class TestResolverDirect:
    def test_same_currency(self, db_session):
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USDT", "USDT")
        assert entry == RateEntry(rate=1.0, inverse_percentage=False, base_percentage=None, base_rate=1.0)

    def test_direct_lookup(self, db_session):
        _add_rate(db_session, "USDT", "VES", 36.5)
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USDT", "VES")
        assert entry is not None
        assert entry.rate == 36.5
        assert entry.inverse_percentage is False

    def test_inverse_lookup(self, db_session):
        # Solo cargamos VES->USDT; al pedir USDT->VES debe invertir
        _add_rate(db_session, "VES", "USDT", 1 / 36.5)
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USDT", "VES")
        assert entry is not None
        assert entry.rate == pytest.approx(36.5)
        assert entry.inverse_percentage is True  # flag se invierte

    def test_no_rate_available(self, db_session):
        r = WhatsAppRateResolver(db_session)
        assert r.get_rate_entry_for_pair("USDT", "VES") is None


class TestResolverCross:
    def test_cross_via_usdt(self, db_session):
        # USD -> VES no existe, pero USD->USDT y USDT->VES sí
        _add_rate(db_session, "USD", "USDT", 0.95)  # 1 USD = 0.95 USDT (paypal-like)
        _add_rate(db_session, "USDT", "VES", 36.0)
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USD", "VES")
        assert entry is not None
        # 1 USD * 0.95 = 0.95 USDT * 36 = 34.2 VES
        assert entry.rate == pytest.approx(0.95 * 36.0)

    def test_no_cross_when_one_side_is_usdt(self, db_session):
        # No debe activar bridge si uno ya es USDT
        r = WhatsAppRateResolver(db_session)
        assert r.get_rate_entry_for_pair("USDT", "COP") is None


class TestResolverPercentageHandling:
    def test_percentage_preserved_in_direct(self, db_session):
        _add_rate(db_session, "USDT", "VES", 33.0, percentage=8.0)
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USDT", "VES")
        assert entry is not None
        assert entry.base_percentage == 8.0
        # base_rate = rate / (1 - 0.08) ≈ 35.86
        assert entry.base_rate == pytest.approx(33.0 / 0.92)


class TestResolverHistorical:
    def test_at_returns_the_rate_of_that_day(self, db_session):
        """La tasa de entonces ya está inactiva; `at` la encuentra igual."""
        ayer = datetime(2026, 7, 23, 10, 0)
        _add_rate(db_session, "ZELLE", "VES", 700.0, percentage=8.0,
                  is_active=False, created_at=ayer)
        _add_rate(db_session, "ZELLE", "VES", 800.0, percentage=8.0,
                  created_at=datetime(2026, 7, 24, 10, 0))
        r = WhatsAppRateResolver(db_session)

        assert r.get_rate_entry_for_pair("ZELLE", "VES").rate == 800.0
        assert r.get_rate_entry_for_pair("ZELLE", "VES", at=ayer).rate == 700.0

    def test_at_before_any_rate_finds_nothing(self, db_session):
        _add_rate(db_session, "ZELLE", "VES", 800.0, created_at=datetime(2026, 7, 24, 10, 0))
        r = WhatsAppRateResolver(db_session)
        assert r.get_rate_entry_for_pair("ZELLE", "VES", at=datetime(2026, 7, 1)) is None


class TestImpliedMargin:
    """Leer el margen de una tasa ya usada: la contracara de rate_with_margin."""

    def test_reads_back_the_pair_margin(self, db_session):
        _add_rate(db_session, "ZELLE", "VES", 791.2, percentage=8.0)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("ZELLE", "VES")
        # Cotizado a la tasa del día → el margen del par, sin ruido de float.
        assert WhatsAppRateResolver.implied_margin(entry, 791.2) == 8.0

    def test_reads_back_on_an_inverse_pair(self, db_session):
        _add_rate(db_session, "VES", "USDT", 800.0, inverse=True, percentage=8.0)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("VES", "USDT")
        # En VES→USDT se divide: la tasa efectiva es 1/800 USDT por VES.
        assert WhatsAppRateResolver.implied_margin(entry, 1 / 800.0) == 8.0

    def test_a_custom_rate_gives_its_own_margin(self, db_session):
        _add_rate(db_session, "ZELLE", "VES", 791.2, percentage=8.0)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("ZELLE", "VES")
        base = entry.base_rate  # 860
        assert WhatsAppRateResolver.implied_margin(entry, base * 0.95) == pytest.approx(5.0)

    def test_none_when_the_pair_has_no_margin(self, db_session):
        _add_rate(db_session, "ZELLE", "VES", 791.2)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("ZELLE", "VES")
        assert WhatsAppRateResolver.implied_margin(entry, 791.2) is None

    def test_none_when_out_of_a_commercial_range(self, db_session):
        _add_rate(db_session, "ZELLE", "VES", 791.2, percentage=8.0)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("ZELLE", "VES")
        # Mejor que la base (margen negativo) y una tasa que no es de este par.
        assert WhatsAppRateResolver.implied_margin(entry, 900.0) is None
        assert WhatsAppRateResolver.implied_margin(entry, 4.57) is None

    def test_none_without_entry_or_rate(self, db_session):
        _add_rate(db_session, "ZELLE", "VES", 791.2, percentage=8.0)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("ZELLE", "VES")
        assert WhatsAppRateResolver.implied_margin(None, 791.2) is None
        assert WhatsAppRateResolver.implied_margin(entry, 0) is None
