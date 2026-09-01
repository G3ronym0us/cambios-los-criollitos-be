"""
Siembra en la base LOCAL clientes con dólares en efectivo pendientes, como los de verdad.

Existe porque el caso que el operador tiene en producción —USD-VES, mano a mano— NO se puede
reproducir con los otros sembradores: allá el cliente paga en efectivo, así que la operación
no tiene comprobante entrante NI saliente, y ninguna base de desarrollo llega sola a ese
estado. Sin estas filas, la cola de «por entregar» sale vacía y no hay nada que mirar.

Copia la forma de producción (medida el 2026-09-01): 120 operaciones USD-VES en PENDING, sin
un solo comprobante, con la tasa fijada a mano, repartidas entre una docena de clientes.

    python -m app.cli.seed_cash_pending           # siembra; si ya hay algo sembrado, no hace nada
    python -m app.cli.seed_cash_pending --reset   # borra SOLO lo sembrado y vuelve a sembrar
    python -m app.cli.seed_cash_pending --clean   # borra SOLO lo sembrado y sale

**Marca el par USD-VES como `settles_in_cash`**, que es lo que hace que estas operaciones
cuenten: sin la bandera la regla exige comprobante entrante y las borra a todas. Al limpiar
se devuelve la bandera a como estaba.

Todo cuelga de teléfonos `5849910000xx` —un bloque propio, distinto del de
`seed_payment_cases`, para que limpiar uno no se lleve el otro—. NUNCA correr contra
producción: aborta si DATABASE_URL no apunta a localhost.
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
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import (
    WhatsAppOutgoingPayment,
    WhatsAppOutgoingSettlement,
)

#: Bloque de teléfonos propio: es lo que permite limpiar sin tocar nada más.
SEED_PREFIX = "58499100"
PAIR = ("USD", "VES")
#: La tasa a la que se cotiza el efectivo. En prod va de 810 a 935 según el día.
RATE = 920.0


def _guard_local() -> None:
    url = settings.DATABASE_URL or ""
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"❌ DATABASE_URL no es local ({url.split('@')[-1]}); esto solo se siembra en dev")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def t(hours_ago: float) -> datetime:
    return _now() - timedelta(hours=hours_ago)


def _currency(db: Session, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


def _cash_pair(db: Session) -> CurrencyPair:
    """El par USD-VES, marcado como de efectivo — que es la mitad del caso a probar."""
    frm, to = PAIR
    symbol = f"{frm}-{to}"
    pair = db.query(CurrencyPair).filter(CurrencyPair.pair_symbol == symbol).first()
    if pair is None:
        pair = CurrencyPair(
            from_currency_id=_currency(db, frm).id,
            to_currency_id=_currency(db, to).id,
            pair_symbol=symbol,
            is_active=True,
        )
        db.add(pair)
        db.flush()

    pair.settles_in_cash = True

    rate = (
        db.query(ExchangeRate)
        .filter(ExchangeRate.currency_pair_id == pair.id, ExchangeRate.is_active.is_(True))
        .first()
    )
    if rate is None:
        db.add(
            ExchangeRate(
                currency_pair_id=pair.id, from_currency=frm, to_currency=to,
                rate=RATE, is_active=True, created_at=t(72),
            )
        )
    db.flush()
    return pair


def _client(db: Session, suffix: str, name: str) -> WhatsAppClient:
    phone = f"{SEED_PREFIX}{suffix}"
    row = db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
    if row is None:
        row = WhatsAppClient(phone=phone)
        db.add(row)
    row.display_name = name
    row.is_tracked = True
    db.flush()
    return row


def _operation(
    db: Session,
    client: WhatsAppClient,
    pair: CurrencyPair,
    usd: float,
    at: datetime,
    beneficiary: str | None,
    status: WhatsAppOperationStatus = WhatsAppOperationStatus.PENDING,
    rate: float = RATE,
) -> WhatsAppOperation:
    """
    Una operación de efectivo: valor en USD, sin comprobante de ninguna de las dos patas.

    `notes` lleva el mismo texto que pone el bot cuando el operador dicta la tasa, porque es
    la huella que distingue estas operaciones en producción.
    """
    op = WhatsAppOperation(
        client_id=client.id,
        currency_pair_id=pair.id,
        from_amount=usd,
        to_amount=round(usd * rate, 2),
        rate_used=rate,
        amount_side=WhatsAppAmountSide.SEND,
        status=status,
        amount=usd,
        currency=PAIR[0],
        beneficiary_alias=beneficiary,
        notes=f"Tasa fijada a mano: 1 USD = {rate:,.2f} VES",
        quoted_at=at,
        created_at=at,
        expires_at=at + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    return op


def _partial(db: Session, op: WhatsAppOperation, covered_usd: float, at: datetime) -> None:
    """Le cuadra una parte con un comprobante saliente: la fila queda en «Completar»."""
    payment = WhatsAppOutgoingPayment(
        client_phone=op.client.phone,
        amount=round(covered_usd * float(op.rate_used), 2),
        currency=PAIR[1],
        bank_to="Banesco",
        reference=f"SEEDCASH-{op.id}",
        raw_text=f"Pago móvil · Bs {covered_usd * float(op.rate_used):,.2f}",
        whatsapp_operation_id=op.id,
        settled_amount=covered_usd,
        settled_reference_rate=float(op.rate_used),
        created_at=at,
    )
    db.add(payment)
    db.flush()
    db.add(
        WhatsAppOutgoingSettlement(
            outgoing_payment_id=payment.id,
            whatsapp_operation_id=op.id,
            settled_amount=covered_usd,
            settled_reference_rate=float(op.rate_used),
            created_at=at,
        )
    )
    db.flush()


def _already_seeded(db: Session) -> bool:
    """¿Hay ya operaciones sembradas? Volver a sembrar encima las duplicaría."""
    return (
        db.query(WhatsAppOperation.id)
        .join(WhatsAppClient, WhatsAppClient.id == WhatsAppOperation.client_id)
        .filter(WhatsAppClient.phone.like(f"{SEED_PREFIX}%"))
        .first()
        is not None
    )


def seed(db: Session) -> list[str]:
    pair = _cash_pair(db)
    hechos: list[str] = []

    # C1 · el cliente gordo: muchas operaciones chicas, como Brayan en producción.
    c = _client(db, "01", "Efectivo · Brayan Maiorama")
    for i, (usd, horas) in enumerate(
        [(80, 150), (100, 121), (20, 96), (75, 73), (30, 49), (100, 26), (60, 5)]
    ):
        _operation(db, c, pair, usd, t(horas), f"Beneficiario {i + 1}")
    hechos.append("C1  Brayan · 7 ops · 465,00 USD · la más vieja lleva 6 d")

    # C2 · pocas y recientes: sirve para ver el ámbar sin el rojo de la antigüedad.
    c = _client(db, "02", "Efectivo · Neurys")
    for usd, horas in [(50, 9), (40, 4), (25, 1)]:
        _operation(db, c, pair, usd, t(horas), "Neurys Rodríguez")
    hechos.append("C2  Neurys · 3 ops · 115,00 USD · todas de hoy")

    # C3 · el cliente con los tres casos de la cola en una sola pantalla.
    c = _client(db, "03", "Efectivo · Mariana Melville")
    _operation(db, c, pair, 130, t(200), "Mariana Melville")
    parcial = _operation(db, c, pair, 100, t(170), "Luis Bracho")
    _partial(db, parcial, 40, t(50))
    _operation(db, c, pair, 60, t(30), None)  # sin beneficiario → «Falta dato»
    # Una cotización que nadie confirmó: en un par de efectivo NO es deuda, y esta fila
    # está aquí justamente para comprobar que la pantalla no la cuenta.
    _operation(db, c, pair, 500, t(120), "Nadie", status=WhatsAppOperationStatus.QUOTED)
    hechos.append("C3  Mariana · 130 completa + 100 con 40 cubiertos + 60 sin beneficiario")
    hechos.append("C3  Mariana · y una QUOTED de 500 que NO debe aparecer ni sumar")

    return hechos


def _seeded_phones(db: Session) -> list[str]:
    return [
        p[0]
        for p in db.query(WhatsAppClient.phone)
        .filter(WhatsAppClient.phone.like(f"{SEED_PREFIX}%"))
        .all()
    ]


def clean(db: Session) -> None:
    """Borra SOLO lo sembrado, de las hojas hacia la raíz, y apaga la bandera del par."""
    phones = _seeded_phones(db)
    if not phones:
        print("🧹 Nada sembrado que borrar")
    else:
        client_ids = [
            c[0]
            for c in db.query(WhatsAppClient.id).filter(WhatsAppClient.phone.in_(phones)).all()
        ]
        op_ids = [
            o[0]
            for o in db.query(WhatsAppOperation.id)
            .filter(WhatsAppOperation.client_id.in_(client_ids or [-1]))
            .all()
        ]
        if op_ids:
            db.query(WhatsAppOutgoingSettlement).filter(
                WhatsAppOutgoingSettlement.whatsapp_operation_id.in_(op_ids)
            ).delete(synchronize_session=False)
        db.query(WhatsAppOutgoingPayment).filter(
            WhatsAppOutgoingPayment.client_phone.in_(phones)
        ).delete(synchronize_session=False)
        if op_ids:
            db.query(WhatsAppOperation).filter(
                WhatsAppOperation.id.in_(op_ids)
            ).delete(synchronize_session=False)
        if client_ids:
            db.query(WhatsAppClient).filter(
                WhatsAppClient.id.in_(client_ids)
            ).delete(synchronize_session=False)
        print(f"🧹 Borrados {len(client_ids)} clientes y {len(op_ids)} operaciones sembradas")

    # La bandera la puso este sembrador; se devuelve para no dejar el par tocado.
    pair = (
        db.query(CurrencyPair)
        .filter(CurrencyPair.pair_symbol == f"{PAIR[0]}-{PAIR[1]}")
        .first()
    )
    if pair is not None and pair.settles_in_cash:
        pair.settles_in_cash = False
        print("🧹 USD-VES vuelve a NO ser par de efectivo")


def main(reset: bool = False, clean_only: bool = False) -> None:
    _guard_local()
    db: Session = SessionLocal()
    try:
        if reset or clean_only:
            clean(db)
            if clean_only:
                db.commit()
                return
        # Sembrar dos veces duplicaría las operaciones: los clientes se reutilizan por
        # teléfono, pero cada operación es una fila nueva. Se para y se dice cómo rehacerlo.
        if _already_seeded(db):
            print("ℹ️  Ya hay efectivo sembrado; nada que hacer")
            print("   rehacerlo: python -m app.cli.seed_cash_pending --reset")
            return
        hechos = seed(db)
        db.commit()
        print("✅ Efectivo pendiente sembrado (USD-VES marcado como par de efectivo)\n")
        for linea in hechos:
            print(f"   {linea}")
        print("\n   Total que debe enseñar la pantalla: 13 operaciones · 830,00 USD · 3 clientes (770,00 marcables: 60,00 esperan beneficiario)")
        print("   limpiar después: python -m app.cli.seed_cash_pending --clean")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Siembra clientes con dólares en efectivo por cobrar (USD-VES)"
    )
    parser.add_argument("--reset", action="store_true", help="borra lo sembrado y vuelve a sembrar")
    parser.add_argument("--clean", action="store_true", help="borra lo sembrado y sale")
    args = parser.parse_args()
    main(reset=args.reset, clean_only=args.clean)
