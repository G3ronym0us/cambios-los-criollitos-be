"""
Siembra escenarios para probar la LISTA DE MOVIMIENTOS de fondos (pantalla /admin/funds).

Cubre exactamente lo que muestra la lista tras el cambio:
  - moderador (gestor del movimiento)
  - cliente (de la operación ligada, solo EXCHANGE)
  - fecha/hora  → varias, para ver el orden descendente
  - monto
  - porcentaje y monto de ganancia (de la transacción ligada, solo EXCHANGE)

Escenarios:
  A) EXCHANGE con cliente + ganancia (varios gestores y clientes distintos).
  B) EXCHANGE con ganancia pero SIN cliente (transacción sin operación) → cliente en blanco.
  C) DEPOSIT / PERSONAL / ADJUSTMENT (sin transacción) → cliente y ganancia ocultos.
  D) Relleno hasta pasar de 50 filas para ver la PAGINACIÓN (50/pág en el front).

Idempotente: borra sus propias filas (marcadas con SEED_TAG) antes de re-sembrar.
NUNCA correr contra producción: aborta si DATABASE_URL no apunta a localhost.

    python -m app.cli.seed_fund_movements            # siembra (idempotente)
    python -m app.cli.seed_fund_movements --reset     # solo borra lo sembrado y sale
    python -m app.cli.seed_fund_movements --count 60  # total de filas EXCHANGE de relleno
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash
from app.database.connection import SessionLocal
from app.enums.user_roles import UserRole
from app.models.currency_pair import CurrencyPair
from app.models.fund import FundGroup, FundGroupMember, FundMovement, FundMovementType
from app.models.transaction import Transaction, TransactionStatus
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationScenario,
    WhatsAppOperationStatus,
)

# Marca que identifica todo lo sembrado por este script (para borrarlo y re-sembrar).
SEED_TAG = "SEEDFUNDMOV"
FUND_NAME = "Zelle/Paypal"
FUND_CURRENCY = "USD"


def _guard_local() -> None:
    url = settings.DATABASE_URL or ""
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"❌ DATABASE_URL no es local ({url.split('@')[-1]}); esto solo se siembra en dev")


def _get_or_create_user(db: Session, username: str, full_name: str, email: str) -> User:
    row = db.query(User).filter(User.username == username).first()
    if row is None:
        row = User(
            username=username,
            full_name=full_name,
            email=email,
            hashed_password=get_password_hash("criollitos17.."),
            role=UserRole.MODERATOR,
            is_active=True,
            is_verified=True,
        )
        db.add(row)
        db.flush()
    return row


def _get_or_create_client(db: Session, phone: str, display_name: str) -> WhatsAppClient:
    row = db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
    if row is None:
        row = WhatsAppClient(phone=phone, display_name=display_name)
        db.add(row)
        db.flush()
    elif not row.display_name:
        row.display_name = display_name
    return row


def _ensure_member(db: Session, group_id: int, user_id: int) -> None:
    exists = (
        db.query(FundGroupMember)
        .filter(FundGroupMember.group_id == group_id, FundGroupMember.user_id == user_id)
        .first()
    )
    if exists is None:
        db.add(FundGroupMember(group_id=group_id, user_id=user_id, is_fund_manager=True))
        db.flush()


def _reset(db: Session) -> None:
    """Borra movimientos, operaciones y transacciones sembradas (por la marca SEED_TAG)."""
    movs = db.query(FundMovement).filter(FundMovement.reference == SEED_TAG).all()
    for m in movs:
        db.delete(m)
    db.flush()

    ops = db.query(WhatsAppOperation).filter(WhatsAppOperation.notes == SEED_TAG).all()
    for o in ops:
        db.delete(o)
    db.flush()

    txs = db.query(Transaction).filter(Transaction.description == SEED_TAG).all()
    for t in txs:
        db.delete(t)
    db.flush()
    db.commit()
    print(f"🧹 Borradas {len(movs)} movimientos, {len(ops)} operaciones, {len(txs)} transacciones sembradas.")


def _make_exchange(
    db: Session,
    group: FundGroup,
    gestor: User,
    client: WhatsAppClient | None,
    pair: CurrencyPair,
    amount: float,
    percentage: float,
    when: datetime,
) -> FundMovement:
    """EXCHANGE con transacción (ganancia) y, si hay cliente, operación que lo enlaza."""
    profit = round(amount * percentage / 100.0, 2)
    tx = Transaction(
        user_id=gestor.id,
        from_currency_symbol=FUND_CURRENCY,
        to_currency_symbol="VES",
        from_amount=amount,
        to_amount=amount,
        exchange_rate=1.0,
        description=SEED_TAG,
        transaction_type="exchange",
        total_profit_percentage=percentage,
        profit_amount=profit,
        profit_amount_usdt=profit,
        status=TransactionStatus.COMPLETED,
        completed_at=when,
    )
    db.add(tx)
    db.flush()

    if client is not None:
        op = WhatsAppOperation(
            client_id=client.id,
            currency_pair_id=pair.id,
            amount=amount,
            currency=FUND_CURRENCY,
            amount_usdt=amount,
            usdt_rate=1.0,
            from_amount=amount,
            to_amount=amount,
            rate_used=1.0,
            amount_side=WhatsAppAmountSide.SEND,
            status=WhatsAppOperationStatus.COMPLETED,
            scenario=WhatsAppOperationScenario.NORMAL,
            fund_group_id=group.id,
            transaction_id=tx.id,
            notes=SEED_TAG,
            expires_at=when + timedelta(hours=1),
            completed_at=when,
        )
        db.add(op)
        db.flush()

    mv = FundMovement(
        group_id=group.id,
        user_id=gestor.id,
        movement_type=FundMovementType.EXCHANGE,
        amount=amount,
        currency=FUND_CURRENCY,
        amount_usdt=amount,
        usdt_rate=1.0,
        transaction_id=tx.id,
        reference=SEED_TAG,
        movement_date=when,
    )
    db.add(mv)
    db.flush()
    return mv


def _make_simple(
    db: Session,
    group: FundGroup,
    gestor: User,
    mtype: FundMovementType,
    amount: float,
    when: datetime,
    notes: str,
) -> FundMovement:
    """DEPOSIT / PERSONAL / ADJUSTMENT: sin transacción → sin cliente ni ganancia."""
    mv = FundMovement(
        group_id=group.id,
        user_id=gestor.id,
        movement_type=mtype,
        amount=amount,
        currency=FUND_CURRENCY,
        amount_usdt=amount,
        usdt_rate=1.0,
        reference=SEED_TAG,
        notes=notes,
        movement_date=when,
    )
    db.add(mv)
    db.flush()
    return mv


def seed(db: Session, fill_count: int) -> FundGroup:
    group = db.query(FundGroup).filter(FundGroup.name == FUND_NAME).first()
    if group is None:
        group = FundGroup(name=FUND_NAME, currency=FUND_CURRENCY, is_active=True)
        db.add(group)
        db.flush()

    admin = db.query(User).filter(User.username == "admin").first()
    jean = _get_or_create_user(db, "jean", "Jean Pérez", "jean@cambiosloscriollitos.com")
    diohandres = _get_or_create_user(
        db, "diohandres", "Diohandres Rojas", "diohandres@cambiosloscriollitos.com"
    )
    _ensure_member(db, group.id, admin.id)
    _ensure_member(db, group.id, jean.id)
    _ensure_member(db, group.id, diohandres.id)

    naldin = _get_or_create_client(db, "13174961478", "Naldin")
    maria = _get_or_create_client(db, "584140001122", "María González")
    pedro = _get_or_create_client(db, "584241234567", "Pedro Ramírez")

    pair = db.query(CurrencyPair).filter(CurrencyPair.pair_symbol == "ZELLE-VES").first()
    if pair is None:
        pair = db.query(CurrencyPair).first()

    gestores = [admin, jean, diohandres]
    clientes = [naldin, maria, pedro]

    now = datetime.now(timezone.utc)
    created = 0

    # ── A) EXCHANGE con cliente + ganancia (los 3 más recientes, bien visibles) ──
    _make_exchange(db, group, jean, naldin, pair, 220.0, 5.0, now - timedelta(minutes=5))
    _make_exchange(db, group, diohandres, maria, pair, 500.0, 3.5, now - timedelta(minutes=20))
    _make_exchange(db, group, admin, pedro, pair, 100.0, 10.0, now - timedelta(hours=1))
    created += 3

    # ── B) EXCHANGE con ganancia pero SIN cliente (transacción sin operación) ──
    _make_exchange(db, group, jean, None, pair, 75.0, 4.0, now - timedelta(hours=2))
    created += 1

    # ── C) Sin transacción: cliente y ganancia deben quedar ocultos ──
    _make_simple(db, group, diohandres, FundMovementType.DEPOSIT, 1000.0,
                 now - timedelta(hours=3), "Depósito Zelle de prueba")
    _make_simple(db, group, admin, FundMovementType.PERSONAL, 50.0,
                 now - timedelta(hours=4), "Gasto personal del gestor")
    _make_simple(db, group, jean, FundMovementType.ADJUSTMENT, 12.5,
                 now - timedelta(hours=5), "Corrección manual")
    created += 3

    # ── D) Relleno EXCHANGE para pasar de 50 filas (paginación) ──
    remaining = max(0, fill_count - created)
    for i in range(remaining):
        _make_exchange(
            db,
            group,
            gestores[i % len(gestores)],
            clientes[i % len(clientes)],
            pair,
            round(50 + (i * 7) % 400 + 0.5, 2),
            round(2.0 + (i % 9), 2),
            now - timedelta(hours=6, minutes=i * 13),
        )
    created += remaining

    db.commit()
    print(f"✅ Sembrados {created} movimientos en el grupo «{group.name}» ({group.uuid}).")
    return group


def main() -> None:
    _guard_local()
    parser = argparse.ArgumentParser(description="Seed de movimientos de fondo para pruebas")
    parser.add_argument("--reset", action="store_true", help="Solo borrar lo sembrado y salir")
    parser.add_argument("--count", type=int, default=60, help="Total de filas EXCHANGE (default 60)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        _reset(db)
        if args.reset:
            return
        group = seed(db, args.count)
        total = (
            db.query(FundMovement).filter(FundMovement.group_id == group.id).count()
        )
        print(f"📊 Total de movimientos en «{group.name}»: {total} (páginas de 50 en el front).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
