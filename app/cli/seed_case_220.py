"""
Siembra en la base local el caso que motivó el modelo de "operación por valor": un Zelle de
220 que se pagó en dos monedas (2026-07-23, cliente 13174961478).

Deja los TRES comprobantes sin vincular, para poder armar la operación a mano desde el panel:

    entrante   220 ZELLE
    saliente   914,04 BRL   (Pix)
    saliente   15.658,4 VES

Además de lo necesario para operarlos: fondo en USD con el operador de gestor, pares
ZELLE-BRL y ZELLE-VES con sus tasas, y el cliente con ZELLE-BRL como par preferido.

    python -m app.cli.seed_case_220            # siembra lo que falte (idempotente)
    python -m app.cli.seed_case_220 --reset    # borra pagos y operaciones antes de sembrar

NUNCA correr contra producción: aborta si DATABASE_URL no apunta a localhost.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import SessionLocal
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.models.fund import FundGroup, FundGroupMember, FundMovement
from app.models.transaction import Transaction
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperation
from app.models.whatsapp_payment import (
    WhatsAppIncomingPayment,
    WhatsAppOutgoingPayment,
    WhatsAppPaymentAllocation,
)

CLIENT_PHONE = "13174961478"
FUND_NAME = "Zelle/Paypal"
# Las tasas reales del día del caso.
RATE_BRL = 4.5702
RATE_VES = 782.92


def _guard_local() -> None:
    url = settings.DATABASE_URL or ""
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"❌ DATABASE_URL no es local ({url.split('@')[-1]}); esto solo se siembra en dev")


def _currency(db: Session, symbol: str, name: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=name)
        db.add(row)
        db.flush()
    return row


def _pair(db: Session, from_symbol: str, to_symbol: str, rate: float) -> CurrencyPair:
    """Par + tasa activa: sin la tasa, el formulario de crear operación no calcula nada."""
    symbol = f"{from_symbol}-{to_symbol}"
    pair = db.query(CurrencyPair).filter(CurrencyPair.pair_symbol == symbol).first()
    if pair is None:
        pair = CurrencyPair(
            from_currency_id=_currency(db, from_symbol, from_symbol).id,
            to_currency_id=_currency(db, to_symbol, to_symbol).id,
            pair_symbol=symbol,
            is_active=True,
        )
        db.add(pair)
        db.flush()

    existing = (
        db.query(ExchangeRate)
        .filter(ExchangeRate.currency_pair_id == pair.id, ExchangeRate.is_active.is_(True))
        .first()
    )
    if existing is None:
        db.add(
            ExchangeRate(
                currency_pair_id=pair.id,
                from_currency=from_symbol,
                to_currency=to_symbol,
                rate=rate,
                is_active=True,
            )
        )
    else:
        existing.rate = rate
    db.flush()
    return pair


def _operator(db: Session) -> User:
    user = db.query(User).filter(User.email == settings.ROOT_USER_EMAIL).first()
    if user is None:
        sys.exit("❌ No existe el usuario root; corre antes: python create_root_user.py")
    # El panel exige verificado; el seed lo deja listo para entrar.
    user.is_verified = True
    user.is_active = True
    db.flush()
    return user


def _fund(db: Session, operator: User) -> FundGroup:
    group = db.query(FundGroup).filter(FundGroup.name == FUND_NAME).first()
    if group is None:
        group = FundGroup(name=FUND_NAME, currency="USD", is_active=True)
        db.add(group)
        db.flush()
    member = (
        db.query(FundGroupMember)
        .filter(FundGroupMember.group_id == group.id, FundGroupMember.user_id == operator.id)
        .first()
    )
    if member is None:
        db.add(FundGroupMember(group_id=group.id, user_id=operator.id, is_fund_manager=True))
        db.flush()
    return group


def _reset(db: Session) -> None:
    """Borra el rastro de WhatsApp para volver a empezar. No toca usuarios, pares ni fondos."""
    db.query(WhatsAppPaymentAllocation).delete(synchronize_session=False)
    db.query(WhatsAppIncomingPayment).delete(synchronize_session=False)
    db.query(WhatsAppOutgoingPayment).delete(synchronize_session=False)
    db.query(FundMovement).delete(synchronize_session=False)
    db.query(WhatsAppOperation).delete(synchronize_session=False)
    db.query(Transaction).delete(synchronize_session=False)
    db.flush()
    print("🧹 Pagos, operaciones, transacciones y movimientos borrados")


def seed(reset: bool = False) -> None:
    _guard_local()
    db: Session = SessionLocal()
    try:
        if reset:
            _reset(db)

        operator = _operator(db)
        fund = _fund(db, operator)
        pair_brl = _pair(db, "ZELLE", "BRL", RATE_BRL)
        _pair(db, "ZELLE", "VES", RATE_VES)

        client = db.query(WhatsAppClient).filter(WhatsAppClient.phone == CLIENT_PHONE).first()
        if client is None:
            client = WhatsAppClient(phone=CLIENT_PHONE, is_tracked=True)
            db.add(client)
        client.display_name = "Naldin"
        client.is_tracked = True
        client.preferred_pair_id = pair_brl.id
        db.flush()

        # Los tres comprobantes, con las horas reales del caso para que el orden se vea igual.
        base = datetime.now(timezone.utc).replace(hour=16, minute=0, second=0, microsecond=0)
        if db.query(WhatsAppIncomingPayment).filter(
            WhatsAppIncomingPayment.client_phone == CLIENT_PHONE
        ).first() is None:
            db.add(
                WhatsAppIncomingPayment(
                    client_phone=CLIENT_PHONE, amount=220, currency="ZELLE", provider="zelle",
                    raw_text="Zelle · Su pago fue enviado · Cantidad $220.00",
                    created_at=base + timedelta(minutes=97),
                )
            )
        if db.query(WhatsAppOutgoingPayment).filter(
            WhatsAppOutgoingPayment.client_phone == CLIENT_PHONE
        ).first() is None:
            db.add_all([
                WhatsAppOutgoingPayment(
                    client_phone=CLIENT_PHONE, amount=15658.4, currency="VES",
                    reference="27043249", raw_text="Pago móvil · Bs 15.658,40",
                    created_at=base + timedelta(minutes=41),
                ),
                WhatsAppOutgoingPayment(
                    client_phone=CLIENT_PHONE, amount=914.04, currency="BRL", provider="pix",
                    raw_text="Pagamento enviado no valor de R$ 914,04",
                    created_at=base + timedelta(minutes=63),
                ),
            ])
        db.commit()

        print("✅ Caso 220 sembrado")
        print(f"   fondo: {fund.name} ({fund.currency}) · gestor {operator.username}")
        print(f"   cliente: {CLIENT_PHONE} · par preferido ZELLE-BRL @ {RATE_BRL}")
        print("   comprobantes sin vincular: 220 ZELLE · 914,04 BRL · 15.658,4 VES")
        print("   → /admin/payments")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Siembra el caso 220 en la base local")
    parser.add_argument("--reset", action="store_true", help="borra pagos y operaciones antes")
    seed(reset=parser.parse_args().reset)
