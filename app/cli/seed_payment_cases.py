"""
Siembra en la base LOCAL un comprobante por cada estado que sabe pintar /admin/payments.

Existe porque la bandeja tiene una docena de estados y en una base de desarrollo normal solo
se dan dos o tres: sin ellos, media pantalla —la franja del remanente al vincular, el aviso
del reparto sin respaldo, el préstamo indexado a BCV, la confirmación de operación huérfana—
no se puede mirar ni ajustar.

    python -m app.cli.seed_payment_cases           # siembra lo que falte (idempotente)
    python -m app.cli.seed_payment_cases --reset   # borra SOLO lo sembrado y vuelve a sembrar
    python -m app.cli.seed_payment_cases --clean   # borra SOLO lo sembrado y sale

Todo lo que crea cuelga de teléfonos `5849990000xx` y del JID de grupo de prueba, así que la
limpieza no toca datos reales. NUNCA correr contra producción: aborta si DATABASE_URL no
apunta a localhost.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import SessionLocal
from app.models.bcv_rate import BcvRate
from app.models.client_loan import ClientLoan, ClientLoanPreferredValue, ClientLoanStatus
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.models.fund import FundGroup, FundGroupMember, FundMovement, FundMovementType
from app.models.user import User
from app.models.whatsapp_balance import WhatsAppBalanceEntry, WhatsAppBalanceEntryType
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import (
    WhatsAppIncomingPayment,
    WhatsAppOutgoingPayment,
    WhatsAppPaymentAllocation,
)

# Prefijo de los teléfonos sembrados: es lo que permite limpiar sin tocar nada real.
SEED_PREFIX = "58499000"
GROUP_JID = "120363999000000001@g.us"
FUND_NAME = "Fondo de pruebas"

# Tasas de referencia. Se registran con fecha ANTERIOR a los comprobantes: la valuación de
# préstamos busca la tasa vigente *antes* del pago, y sin eso el formulario sale sin cifras.
RATE_USD_VES = 250.0
RATE_ZELLE_VES = 248.0
RATE_ZELLE_BRL = 4.57
RATE_USD_COP = 3900.0
RATE_USDT_VES = 252.0
RATE_BCV = 240.0


def _guard_local() -> None:
    url = settings.DATABASE_URL or ""
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"❌ DATABASE_URL no es local ({url.split('@')[-1]}); esto solo se siembra en dev")


def _currency(db: Session, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


def _pair(db: Session, frm: str, to: str, rate: float, at: datetime) -> CurrencyPair:
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
    existing = (
        db.query(ExchangeRate)
        .filter(ExchangeRate.currency_pair_id == pair.id, ExchangeRate.is_active.is_(True))
        .first()
    )
    if existing is None:
        db.add(
            ExchangeRate(
                currency_pair_id=pair.id,
                from_currency=frm,
                to_currency=to,
                rate=rate,
                is_active=True,
                created_at=at,
            )
        )
    db.flush()
    return pair


def _operator(db: Session) -> User:
    user = db.query(User).filter(User.email == settings.ROOT_USER_EMAIL).first()
    if user is None:
        user = db.query(User).filter(User.role == "ROOT").first()
    if user is None:
        sys.exit("❌ No existe un usuario ROOT; corre antes: python create_root_user.py")
    return user


def _fund(db: Session, operator: User) -> FundGroup:
    group = db.query(FundGroup).filter(FundGroup.name == FUND_NAME).first()
    if group is None:
        group = FundGroup(name=FUND_NAME, currency="USD", is_active=True)
        db.add(group)
        db.flush()
    group.whatsapp_group_jid = GROUP_JID
    member = (
        db.query(FundGroupMember)
        .filter(FundGroupMember.group_id == group.id, FundGroupMember.user_id == operator.id)
        .first()
    )
    if member is None:
        db.add(FundGroupMember(group_id=group.id, user_id=operator.id, is_fund_manager=True))
    db.flush()
    return group


def _client(db: Session, suffix: str, name: str) -> WhatsAppClient:
    phone = f"{SEED_PREFIX}{suffix}"
    row = db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
    if row is None:
        row = WhatsAppClient(phone=phone, is_tracked=True)
        db.add(row)
    row.display_name = name
    row.is_tracked = True
    db.flush()
    return row


def _operation(
    db: Session,
    client: WhatsAppClient,
    pair: CurrencyPair,
    from_amount: float,
    to_amount: float,
    at: datetime,
    status: WhatsAppOperationStatus = WhatsAppOperationStatus.QUOTED,
    fund: FundGroup | None = None,
) -> WhatsAppOperation:
    op = WhatsAppOperation(
        client_id=client.id,
        currency_pair_id=pair.id,
        from_amount=from_amount,
        to_amount=to_amount,
        rate_used=to_amount / from_amount if from_amount else 0,
        amount_side=WhatsAppAmountSide.SEND,
        status=status,
        amount=from_amount,
        currency=pair.pair_symbol.split("-")[0],
        fund_group_id=fund.id if fund else None,
        quoted_at=at,
        created_at=at,
        expires_at=at + timedelta(days=30),
    )
    db.add(op)
    db.flush()
    return op


def _seeded_phones(db: Session) -> list[str]:
    return [GROUP_JID] + [
        p[0]
        for p in db.query(WhatsAppClient.phone)
        .filter(WhatsAppClient.phone.like(f"{SEED_PREFIX}%"))
        .all()
    ]


def clean(db: Session) -> None:
    """Borra SOLO lo sembrado, de las hojas hacia la raíz."""
    phones = _seeded_phones(db)
    if not phones:
        print("🧹 Nada sembrado que borrar")
        return

    client_ids = [
        c[0] for c in db.query(WhatsAppClient.id).filter(WhatsAppClient.phone.in_(phones)).all()
    ]
    op_ids = [
        o[0]
        for o in db.query(WhatsAppOperation.id)
        .filter(WhatsAppOperation.client_id.in_(client_ids or [-1]))
        .all()
    ]
    inc_ids = [
        p[0]
        for p in db.query(WhatsAppIncomingPayment.id)
        .filter(WhatsAppIncomingPayment.client_phone.in_(phones))
        .all()
    ]

    if inc_ids:
        db.query(WhatsAppPaymentAllocation).filter(
            WhatsAppPaymentAllocation.incoming_payment_id.in_(inc_ids)
        ).delete(synchronize_session=False)
        db.query(WhatsAppBalanceEntry).filter(
            WhatsAppBalanceEntry.incoming_payment_id.in_(inc_ids)
        ).delete(synchronize_session=False)
    if client_ids:
        db.query(ClientLoan).filter(ClientLoan.client_id.in_(client_ids)).delete(
            synchronize_session=False
        )
        db.query(WhatsAppBalanceEntry).filter(
            WhatsAppBalanceEntry.client_id.in_(client_ids)
        ).delete(synchronize_session=False)
    db.query(WhatsAppIncomingPayment).filter(
        WhatsAppIncomingPayment.client_phone.in_(phones)
    ).delete(synchronize_session=False)
    db.query(WhatsAppOutgoingPayment).filter(
        WhatsAppOutgoingPayment.client_phone.in_(phones)
    ).delete(synchronize_session=False)
    if op_ids:
        db.query(FundMovement).filter(FundMovement.reference.like("SEED-%")).delete(
            synchronize_session=False
        )
        db.query(WhatsAppOperation).filter(WhatsAppOperation.id.in_(op_ids)).delete(
            synchronize_session=False
        )
    if client_ids:
        db.query(WhatsAppClient).filter(WhatsAppClient.id.in_(client_ids)).delete(
            synchronize_session=False
        )
    db.flush()
    print(f"🧹 Borrados los casos de prueba de {len(phones)} teléfonos")


def seed(db: Session) -> list[str]:
    now = datetime.now(timezone.utc)
    rates_at = now - timedelta(days=2)
    operator = _operator(db)
    fund = _fund(db, operator)

    p_usd_ves = _pair(db, "USD", "VES", RATE_USD_VES, rates_at)
    p_zelle_ves = _pair(db, "ZELLE", "VES", RATE_ZELLE_VES, rates_at)
    p_zelle_brl = _pair(db, "ZELLE", "BRL", RATE_ZELLE_BRL, rates_at)
    p_usd_cop = _pair(db, "USD", "COP", RATE_USD_COP, rates_at)
    _pair(db, "USDT", "VES", RATE_USDT_VES, rates_at)

    if db.query(BcvRate).filter(BcvRate.fetched_at <= now).first() is None:
        db.add(BcvRate(rate=RATE_BCV, source="seed", fetched_at=rates_at))
        db.flush()

    hechos: list[str] = []
    t = lambda mins: now - timedelta(minutes=mins)  # noqa: E731

    # ---------- ENTRANTES ----------

    # E1 · sugerencia que calza al céntimo → "Cubre la operación exacta" en verde
    c = _client(db, "01", "Seed · Sugerida exacta")
    _operation(db, c, p_usd_ves, 300, 300 * RATE_USD_VES, t(30))
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=300, currency="USD", provider="zelle",
        bank_from="Bank of America", reference="SEED-E1",
        raw_text="Zelle payment sent\n$300.00\nConfirmation SEED-E1", created_at=t(25)))
    hechos.append("E1  entrante 300 USD · sugerida exacta")

    # E2 · el comprobante trae de más → franja ámbar del remanente + "sin asignar" en la fila
    c = _client(db, "02", "Seed · Con sobrante")
    _operation(db, c, p_usd_ves, 200, 200 * RATE_USD_VES, t(40))
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=250, currency="USD", provider="zelle",
        bank_from="Chase", reference="SEED-E2",
        raw_text="Zelle payment sent\n$250.00\nConfirmation SEED-E2", created_at=t(35)))
    hechos.append("E2  entrante 250 USD vs op de 200 · sobran 50 (franja del pie)")

    # E3 · la operación pide más de lo que llegó → "faltarían"
    c = _client(db, "03", "Seed · Faltante")
    _operation(db, c, p_usd_ves, 500, 500 * RATE_USD_VES, t(50))
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=120, currency="USD", provider="zelle",
        bank_from="Wells Fargo", reference="SEED-E3",
        raw_text="Zelle payment sent\n$120.00", created_at=t(45)))
    hechos.append("E3  entrante 120 USD vs op de 500 · faltarían 380")

    # E4 · sin cotizaciones del cliente → estado vacío del buscador
    c = _client(db, "04", "Seed · Sin candidatas")
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=77, currency="USD", provider="paypal",
        reference="SEED-E4", raw_text="PayPal · $77.00", created_at=t(55)))
    hechos.append("E4  entrante 77 USD · sin cotizaciones (estado vacío)")

    # E5 · ya conciliado
    c = _client(db, "05", "Seed · Completada")
    op = _operation(db, c, p_usd_ves, 400, 400 * RATE_USD_VES, t(600),
                    status=WhatsAppOperationStatus.COMPLETED, fund=fund)
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=400, currency="USD", provider="zelle",
        bank_from="Truist", reference="SEED-E5", whatsapp_operation_id=op.id,
        raw_text="Zelle payment sent\n$400.00", created_at=t(595)))
    hechos.append("E5  entrante 400 USD · completada")

    # E6 · repartido entre dos operaciones y con sobrante → aviso del reparto + acreditar saldo
    c = _client(db, "06", "Seed · Reparto con sobrante")
    op_brl = _operation(db, c, p_zelle_brl, 137, 137 * RATE_ZELLE_BRL, t(70), fund=fund)
    op_ves = _operation(db, c, p_zelle_ves, 63, 63 * RATE_ZELLE_VES, t(69))
    pay = WhatsAppIncomingPayment(
        client_phone=c.phone, amount=220, currency="ZELLE", provider="zelle",
        bank_from="Bank of America", reference="SEED-E6",
        whatsapp_operation_id=op_brl.id,
        raw_text="Zelle payment sent\n$220.00\nConfirmation SEED-E6", created_at=t(65))
    db.add(pay)
    db.flush()
    db.add_all([
        WhatsAppPaymentAllocation(incoming_payment_id=pay.id, whatsapp_operation_id=op_brl.id, amount=137),
        WhatsAppPaymentAllocation(incoming_payment_id=pay.id, whatsapp_operation_id=op_ves.id, amount=63),
    ])
    hechos.append("E6  entrante 220 ZELLE repartido 137+63 · quedan 20 sin respaldo")

    # E7 · repartido y cuadrado
    c = _client(db, "07", "Seed · Reparto cuadrado")
    op_a = _operation(db, c, p_usd_ves, 100, 100 * RATE_USD_VES, t(80))
    op_b = _operation(db, c, p_usd_cop, 80, 80 * RATE_USD_COP, t(79))
    pay = WhatsAppIncomingPayment(
        client_phone=c.phone, amount=180, currency="USD", provider="zelle",
        reference="SEED-E7", whatsapp_operation_id=op_a.id,
        raw_text="Zelle payment sent\n$180.00", created_at=t(75))
    db.add(pay)
    db.flush()
    db.add_all([
        WhatsAppPaymentAllocation(incoming_payment_id=pay.id, whatsapp_operation_id=op_a.id, amount=100),
        WhatsAppPaymentAllocation(incoming_payment_id=pay.id, whatsapp_operation_id=op_b.id, amount=80),
    ])
    hechos.append("E7  entrante 180 USD repartido 100+80 · sin sobrante")

    # E8 · acreditado como saldo a favor
    c = _client(db, "08", "Seed · Saldo a favor")
    pay = WhatsAppIncomingPayment(
        client_phone=c.phone, amount=90, currency="USD", provider="zelle",
        reference="SEED-E8", raw_text="Zelle payment sent\n$90.00", created_at=t(90))
    db.add(pay)
    db.flush()
    db.add(WhatsAppBalanceEntry(
        client_id=c.id, entry_type=WhatsAppBalanceEntryType.CREDIT, amount=90, currency="USD",
        incoming_payment_id=pay.id, notes="Seed · abonos a la tasa del día"))
    hechos.append("E8  entrante 90 USD · acreditado al saldo")

    # E9 · el OCR no sacó el monto → "Revisar OCR" y monto en rojo
    c = _client(db, "09", "Seed · OCR ilegible")
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=None, currency=None,
        reference="SEED-E9", raw_text="captura borrosa · no se pudo leer el monto",
        created_at=t(100)))
    hechos.append("E9  entrante sin monto · Revisar OCR")

    # E10 · corregido a mano → insignia y tachado del valor original
    c = _client(db, "10", "Seed · Corregido a mano")
    _operation(db, c, p_usd_ves, 220, 220 * RATE_USD_VES, t(115))
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=220, currency="USD", provider="zelle",
        bank_from="Bank of America", reference="SEED-E10",
        corrected_at=t(105), correction_original="200.00",
        raw_text="Zelle payment sent\n$200.00\nConfirmation SEED-E10", created_at=t(110)))
    hechos.append("E10 entrante 220 USD · corregido a mano (OCR leyó 200)")

    # E11 · llegó por el grupo de la cuenta alquilada (client_phone = JID)
    db.add(WhatsAppIncomingPayment(
        client_phone=GROUP_JID, amount=150, currency="USDT", provider="binance",
        reference="SEED-E11", raw_text="Binance Pay · 150 USDT", created_at=t(120)))
    hechos.append("E11 entrante 150 USDT · desde grupo (fondo sin operación)")

    # E12 · único comprobante de una op CON transacción de fondo: es el que dispara la
    # confirmación de "operación sin ningún comprobante" con el detalle de lo que se revierte.
    c = _client(db, "12", "Seed · Huérfana al desvincular")
    op = _operation(db, c, p_zelle_brl, 200, 200 * RATE_ZELLE_BRL, t(130), fund=fund)
    db.add(WhatsAppIncomingPayment(
        client_phone=c.phone, amount=200, currency="ZELLE", provider="zelle",
        reference="SEED-E12", whatsapp_operation_id=op.id,
        raw_text="Zelle payment sent\n$200.00", created_at=t(125)))
    # El preview reconoce los EXCHANGE sueltos por fondo + tipo + fecha de la operación:
    # `movement_date` tiene que ser exactamente `op.created_at` o no los lista.
    db.add(FundMovement(
        group_id=fund.id, user_id=operator.id,
        movement_type=FundMovementType.EXCHANGE, amount=200, currency="ZELLE",
        movement_date=t(130),
        reference="SEED-E12", notes="Seed · movimiento que se revierte al borrar"))
    hechos.append("E12 entrante 200 ZELLE · único de su op, con movimiento de fondo")

    # ---------- SALIENTES ----------

    # S1 · sin clasificar y con candidata → insignia SUGERIDA, ninguna opción con anillo
    c = _client(db, "20", "Seed · Saliente sugerido")
    _operation(db, c, p_zelle_ves, 100, 100 * RATE_ZELLE_VES, t(20),
               status=WhatsAppOperationStatus.PENDING)
    db.add(WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=100 * RATE_ZELLE_VES, currency="VES",
        bank_to="Mercantil", account_number="0102-0000-4471", identification="V-19.882.104",
        reference="SEED-S1", raw_text="Pago móvil · Bs 24.800,00", created_at=t(15)))
    hechos.append("S1  saliente 24.800 VES · sin clasificar, con sugerida")

    # S2 · sin clasificar y sin candidata
    c = _client(db, "21", "Seed · Saliente sin sugerida")
    db.add(WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=1_950_000, currency="COP", bank_to="Nequi",
        reference="SEED-S2", raw_text="Nequi · $1.950.000", created_at=t(18)))
    hechos.append("S2  saliente 1.950.000 COP · sin clasificar, sin sugerida")

    # S3 · préstamo YA registrado en VES indexado a BCV → el cajón abre en "Préstamo registrado"
    c = _client(db, "22", "Seed · Préstamo BCV")
    out = WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=48_000, currency="VES", bank_to="Banesco",
        reference="SEED-S3", raw_text="Pago móvil · Bs 48.000,00", created_at=t(200))
    db.add(out)
    db.flush()
    db.add(ClientLoan(
        client_id=c.id, outgoing_payment_id=out.id,
        fiat_amount=Decimal("48000"), fiat_currency="VES",
        usdt_amount=Decimal(str(round(48_000 / RATE_USDT_VES, 8))),
        usdt_rate=Decimal(str(RATE_USDT_VES)),
        bcv_amount=Decimal(str(round(48_000 / RATE_BCV, 8))),
        bcv_rate=Decimal(str(RATE_BCV)),
        valuation_at=t(200), preferred_value=ClientLoanPreferredValue.BCV,
        status=ClientLoanStatus.OPEN, notes="Seed · deuda indexada a BCV"))
    hechos.append("S3  saliente 48.000 VES · préstamo registrado (referencia BCV)")

    # S4 · préstamo SIN registrar en VES → el formulario abre con BCV preseleccionado
    c = _client(db, "23", "Seed · Préstamo por registrar")
    db.add(WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=31_500, currency="VES", bank_to="Banco de Venezuela",
        reference="SEED-S4", raw_text="Pago móvil · Bs 31.500,00", created_at=t(210)))
    hechos.append("S4  saliente 31.500 VES · para registrar préstamo con BCV por defecto")

    # S5 · gasto personal
    c = _client(db, "24", "Seed · Gasto personal")
    db.add(WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=45, currency="USD", provider="zelle",
        reference="SEED-S5", is_personal_expense=True,
        personal_description="Recarga de teléfono del operador",
        raw_text="Zelle · $45.00", created_at=t(300)))
    hechos.append("S5  saliente 45 USD · gasto personal")

    # S6 · irrelevante
    c = _client(db, "25", "Seed · Irrelevante")
    db.add(WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=12, currency="USD",
        reference="SEED-S6", is_irrelevant=True,
        irrelevant_description="Comprobante duplicado",
        raw_text="Zelle · $12.00", created_at=t(310)))
    hechos.append("S6  saliente 12 USD · irrelevante")

    # S7 · ya vinculado a una operación → el anillo cae en "Pago de una operación"
    c = _client(db, "26", "Seed · Saliente vinculado")
    op = _operation(db, c, p_zelle_ves, 150, 150 * RATE_ZELLE_VES, t(320),
                    status=WhatsAppOperationStatus.COMPLETED, fund=fund)
    db.add(WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=150 * RATE_ZELLE_VES, currency="VES",
        bank_to="Provincial", reference="SEED-S7", whatsapp_operation_id=op.id,
        settled_amount=150, settled_reference_rate=RATE_ZELLE_VES,
        raw_text="Pago móvil · Bs 37.200,00", created_at=t(315)))
    hechos.append("S7  saliente 37.200 VES · vinculado y cubierto")

    # S8 · cubre solo una parte de su operación → "faltan X" en el buscador
    c = _client(db, "27", "Seed · Cobertura parcial")
    op = _operation(db, c, p_zelle_ves, 300, 300 * RATE_ZELLE_VES, t(330),
                    status=WhatsAppOperationStatus.PENDING, fund=fund)
    db.add(WhatsAppOutgoingPayment(
        client_phone=c.phone, amount=120 * RATE_ZELLE_VES, currency="VES",
        bank_to="Bicentenario", reference="SEED-S8", whatsapp_operation_id=op.id,
        settled_amount=120, settled_reference_rate=RATE_ZELLE_VES,
        raw_text="Pago móvil · Bs 29.760,00", created_at=t(325)))
    hechos.append("S8  saliente 29.760 VES · cubre 120 de una op de 300")

    return hechos


def main(reset: bool = False, clean_only: bool = False) -> None:
    _guard_local()
    db: Session = SessionLocal()
    try:
        if reset or clean_only:
            clean(db)
            if clean_only:
                db.commit()
                return
        hechos = seed(db)
        db.commit()
        print(f"✅ {len(hechos)} casos sembrados en /admin/payments\n")
        for linea in hechos:
            print(f"   {linea}")
        print("\n   limpiar después: python -m app.cli.seed_payment_cases --clean")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Siembra un pago por cada estado de la bandeja")
    parser.add_argument("--reset", action="store_true", help="borra lo sembrado y vuelve a sembrar")
    parser.add_argument("--clean", action="store_true", help="borra lo sembrado y sale")
    args = parser.parse_args()
    main(reset=args.reset, clean_only=args.clean)
