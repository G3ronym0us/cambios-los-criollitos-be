"""
La tasa que REGÍA en un momento dado, contra Postgres real.

Un comprobante de la mañana leído por la tarde tiene que cotizarse con la tasa de la mañana:
con la de la tarde se le cambia el trato al cliente y aparece una ganancia que nadie cobró.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.exchange_rate import ExchangeRate
from app.repositories.exchange_rate_repository import ExchangeRateRepository


@pytest.fixture
def repo(db):
    return ExchangeRateRepository(db)


def _add_rate(db, pair, rate: float, when: datetime, active: bool = False) -> ExchangeRate:
    """Una tasa histórica del par: la de entonces ya no está activa, la desactivó la siguiente."""
    row = ExchangeRate(
        currency_pair_id=pair.id,
        from_currency=pair.pair_symbol.split("-")[0],
        to_currency=pair.pair_symbol.split("-")[1],
        rate=rate,
        is_active=active,
    )
    db.add(row)
    db.flush()
    # `created_at` lo pone la base al insertar; para simular el pasado se reescribe después.
    row.created_at = when
    db.flush()
    return row


def test_returns_the_rate_in_force_then_not_todays(db, repo, pairs):
    pair = pairs["ZELLE-VES"]
    now = datetime.now(timezone.utc)
    _add_rate(db, pair, 700.0, now - timedelta(days=2))
    _add_rate(db, pair, 800.0, now - timedelta(hours=6))

    # A media mañana de anteayer regía la de 700, aunque hoy haya dos más nuevas.
    got = repo.get_rate_by_pair_at(pair.uuid, now - timedelta(days=1))
    assert got is not None
    assert float(got.rate) == 700.0

    # Hace una hora ya regía la de 800.
    got = repo.get_rate_by_pair_at(pair.uuid, now - timedelta(hours=1))
    assert float(got.rate) == 800.0


def test_the_active_flag_does_not_decide_the_past(db, repo, pairs):
    """La vigente de entonces hoy está inactiva: buscar por `is_active` la perdería."""
    pair = pairs["ZELLE-VES"]
    now = datetime.now(timezone.utc)
    old = _add_rate(db, pair, 650.0, now - timedelta(days=3))
    assert old.is_active is False

    got = repo.get_rate_by_pair_at(pair.uuid, now - timedelta(days=2))
    assert float(got.rate) == 650.0


def test_before_any_rate_there_is_nothing_to_quote_with(db, repo, pairs):
    """Un comprobante anterior a todo el historial no inventa tasa: el caller cae a la vigente."""
    pair = pairs["ZELLE-VES"]
    ancient = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert repo.get_rate_by_pair_at(pair.uuid, ancient) is None


def test_unknown_pair_is_none(repo):
    from uuid import uuid4

    assert repo.get_rate_by_pair_at(uuid4(), datetime.now(timezone.utc)) is None
