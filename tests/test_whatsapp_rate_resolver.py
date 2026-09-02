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
        # Solo cargamos VES->USDT; al pedir USDT->VES debe voltear el flag, NO la tasa.
        # VES->USDT con rate=1/36.5, inverse=False significa: USDT = VES * (1/36.5)
        # (36.5 VES * 1/36.5 = 1 USDT). Leído al revés (USDT->VES) es la MISMA tasa
        # con el flag volteado a True: USDT / (1/36.5) = USDT * 36.5 = VES. Antes del
        # fix esta rama invertía también la tasa (rate=1/(1/36.5)=36.5), lo que hacía
        # que USDT->VES aplicara `amount / 36.5` en vez de `amount * 36.5` — este mismo
        # test daba por buena esa conducta rota afirmando entry.rate == 36.5.
        _add_rate(db_session, "VES", "USDT", 1 / 36.5)
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USDT", "VES")
        assert entry is not None
        assert entry.rate == pytest.approx(1 / 36.5)
        assert entry.inverse_percentage is True  # flag se invierte
        # La propiedad que hace obvio el bug: aplicar la entry inversa a 1 USDT
        # debe dar lo mismo que el par directo real (1 USDT = 36.5 VES).
        assert WhatsAppRateResolver.apply_rate(1.0, entry.rate, entry.inverse_percentage) == pytest.approx(36.5)

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

    def test_cross_via_usdt_with_one_leg_only_inverse(self, db_session):
        """
        Puente USD -> USDT -> VES donde la pata USD->USDT NO está cargada directa,
        sólo su inverso (USDT->USD) — exactamente el escenario que dispara el bug de
        `_get_direct_entry` dentro de una de las patas del cruce.
        """
        _add_rate(db_session, "USDT", "USD", 0.98)  # sólo USDT->USD; USD->USDT cae al fallback
        _add_rate(db_session, "USDT", "VES", 36.0)
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USD", "VES")
        assert entry is not None
        # leg1 (USD->USDT, vía fallback del inverso): 1 USD / 0.98 = 1.020408... USDT
        # leg2 (USDT->VES, directo): * 36.0
        # Con el bug (rate invertido dos veces en leg1) esto daba 0.98 * 36 = 35.28
        # en vez de (1/0.98) * 36 ≈ 36.7347 — casi un 4% de diferencia, silenciosa.
        assert entry.rate == pytest.approx((1.0 / 0.98) * 36.0)
        assert entry.rate != pytest.approx(0.98 * 36.0)  # eso sería el resultado del bug


class TestInverseFallbackCorrectness:
    """
    Casos centrados en el bug de reciprocidad doble en `_get_direct_entry`: cuando un
    par sólo está cargado en el sentido inverso al pedido, la entrada resuelta debe
    voltear únicamente el flag `inverse_percentage`, conservando `rate`/`base_rate`
    tal cual — no aplicar la recíproca otra vez.
    """

    def test_inverse_only_pair_resolves_to_the_correct_numeric_rate(self, db_session):
        # Sólo VES->USDT cargado (1 USDT = 36.5 VES → VES->USDT rate = 1/36.5).
        _add_rate(db_session, "VES", "USDT", 1 / 36.5, percentage=8.0)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("USDT", "VES")
        assert entry is not None
        # `rate` NO se invierte: sigue siendo 1/36.5, sólo cambia el flag.
        assert entry.rate == pytest.approx(1 / 36.5)
        assert entry.inverse_percentage is True
        assert entry.base_percentage == 8.0
        # 1 USDT debe rendir 36.5 VES (aplicando la entry resuelta), no 36.5² ni 1/36.5².
        assert WhatsAppRateResolver.apply_rate(1.0, entry.rate, entry.inverse_percentage) == pytest.approx(36.5)

    def test_inverse_fallback_matches_the_direct_pair_when_both_exist(self, db_session):
        """
        La propiedad que hace obvio el bug: resolver un par SÓLO por su inverso debe dar
        exactamente el mismo resultado numérico que resolverlo cuando el par directo sí
        está cargado (misma tasa de negocio, mismo día).
        """
        # Sesión de la fixture: sólo el inverso.
        _add_rate(db_session, "VES", "USDT", 1 / 36.5)
        via_inverse = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("USDT", "VES")
        assert via_inverse is not None

        # Sesión aparte con el par cargado directo, para comparar.
        engine2 = create_engine("sqlite:///:memory:")
        TestBase.metadata.create_all(engine2)
        session2 = sessionmaker(bind=engine2)()
        _add_rate(session2, "USDT", "VES", 36.5)
        via_direct = WhatsAppRateResolver(session2).get_rate_entry_for_pair("USDT", "VES")
        session2.close()
        assert via_direct is not None

        amount = 250.0
        got_via_inverse = WhatsAppRateResolver.apply_rate(amount, via_inverse.rate, via_inverse.inverse_percentage)
        got_via_direct = WhatsAppRateResolver.apply_rate(amount, via_direct.rate, via_direct.inverse_percentage)
        assert got_via_inverse == pytest.approx(got_via_direct)
        assert got_via_inverse == pytest.approx(amount * 36.5)


class TestConvencionDeProduccion:
    """
    La forma REAL en que producción guarda las tasas, que no es la de los otros fixtures.

    Medido el 2026-09-02 en la base de producción:

        USDT→VES   rate=960  inverse=False   →  VES = USDT × 960
        VES→USDT   rate=967  inverse=True    →  USDT = VES / 967

    Las dos filas llevan la MISMA magnitud —«VES por USDT»— y lo único que cambia es el
    flag, que decide multiplicar o dividir. Eso es justo lo que hace que derivar un sentido
    del otro tenga que conservar la tasa y voltear sólo el flag; el código roto producía
    `1/967` con el flag volteado, que es dos veces la recíproca.

    Los demás fixtures del fichero guardan `VES→USDT` con `inverse=False` y la tasa ya
    invertida (1/36.5). La aritmética sale igual porque el resolver es agnóstico, pero no
    retrata la convención real, así que este caso la fija aparte.
    """

    def test_la_fila_inversa_de_produccion_se_lee_bien(self, db_session):
        # Sólo VES→USDT cargada, tal cual la guarda producción.
        _add_rate(db_session, "VES", "USDT", 967.0, inverse=True)
        r = WhatsAppRateResolver(db_session)

        entry = r.get_rate_entry_for_pair("USDT", "VES")
        assert entry is not None
        # Se conserva la magnitud y se voltea el flag: NO 1/967.
        assert entry.rate == pytest.approx(967.0)
        assert entry.inverse_percentage is False
        # Y lo que importa: 1 USDT son 967 VES, no 0,00103.
        assert WhatsAppRateResolver.apply_rate(1.0, entry.rate, entry.inverse_percentage) == pytest.approx(967.0)

    def test_los_dos_sentidos_reales_coinciden(self, db_session):
        """
        Con las dos filas de producción cargadas, ir por el directo o por el inverso tiene
        que dar lo mismo. Es la propiedad que el bug rompía.
        """
        _add_rate(db_session, "USDT", "VES", 960.0)
        r = WhatsAppRateResolver(db_session)
        directo = r.get_rate_entry_for_pair("USDT", "VES")
        assert directo is not None
        ves_por_usdt = WhatsAppRateResolver.apply_rate(1.0, directo.rate, directo.inverse_percentage)
        assert ves_por_usdt == pytest.approx(960.0)


class TestRealAffectedPairs:
    """
    Los dos pares que HOY en producción sólo tienen tasa activa en un sentido, así que
    el sentido contrario pasa obligatoriamente por el fallback de `_get_direct_entry`
    (ver informe del bug): USD->VES (afecta VES->USD) y ZELLE->USDT (afecta USDT->ZELLE).
    """

    def test_ves_to_usd(self, db_session):
        # Sólo USD->VES está cargado en prod.
        _add_rate(db_session, "USD", "VES", 190.0)
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("VES", "USD")
        assert entry is not None
        assert entry.rate == pytest.approx(190.0)
        assert entry.inverse_percentage is True
        # 190 VES deben rendir 1 USD, no 190² (36.100) ni 1/190.
        assert WhatsAppRateResolver.apply_rate(190.0, entry.rate, entry.inverse_percentage) == pytest.approx(1.0)

    def test_usdt_to_zelle(self, db_session):
        # Sólo ZELLE->USDT está cargado en prod.
        _add_rate(db_session, "ZELLE", "USDT", 0.93)  # ZELLE con margen: 1 ZELLE = 0.93 USDT
        r = WhatsAppRateResolver(db_session)
        entry = r.get_rate_entry_for_pair("USDT", "ZELLE")
        assert entry is not None
        assert entry.rate == pytest.approx(0.93)
        assert entry.inverse_percentage is True
        # 0.93 USDT deben rendir 1 ZELLE, no 0.93² ni 1/0.93.
        assert WhatsAppRateResolver.apply_rate(0.93, entry.rate, entry.inverse_percentage) == pytest.approx(1.0)


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

    def test_quoting_at_the_pair_rate_reads_zero(self, db_session):
        """Sin margen configurado y cotizando a la tasa del par: 0%, que es la verdad."""
        _add_rate(db_session, "ZELLE", "VES", 791.2)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("ZELLE", "VES")
        assert WhatsAppRateResolver.implied_margin(entry, 791.2) == 0.0

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


class TestImpliedMarginWithoutConfiguredMargin:
    """
    Un par sin margen configurado (la tasa cruda de Binance, p. ej. USDT-BRL) igual permite
    leer lo que se cobró: lo que se le pagó al cliente contra la tasa del par.
    """

    def test_paying_under_the_pair_rate_is_the_margin(self, db_session):
        _add_rate(db_session, "USDT", "BRL", 5.078)  # sin percentage
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("USDT", "BRL")
        assert entry.base_percentage is None
        # 21 USDT pagados como 100 BRL → 4,7619 por USDT
        assert WhatsAppRateResolver.implied_margin(entry, 100 / 21) == pytest.approx(6.23, abs=0.01)

    def test_paying_at_the_pair_rate_is_zero_not_unknown(self, db_session):
        _add_rate(db_session, "USDT", "BRL", 5.078)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("USDT", "BRL")
        assert WhatsAppRateResolver.implied_margin(entry, 5.078) == 0.0

    def test_a_rate_that_is_not_from_this_pair_still_reads_nothing(self, db_session):
        _add_rate(db_session, "USDT", "BRL", 5.078)
        entry = WhatsAppRateResolver(db_session).get_rate_entry_for_pair("USDT", "BRL")
        assert WhatsAppRateResolver.implied_margin(entry, 800.0) is None
