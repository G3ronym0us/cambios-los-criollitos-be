"""
Lleva los movimientos viejos al modelo de dos patas.

Hasta ahora una operación dejaba UN movimiento `EXCHANGE`, siempre en el fondo de la moneda
que el cliente entrega y siempre restando del balance. Con el criterio de caja, esa pata
entra (`EXCHANGE_IN`) y falta la que sale.

Tres tratos, que el plan imprime por separado porque no son el mismo cambio:

  1. retipar        — la moneda del movimiento es la de la pata entrante: solo cambia el tipo.
  2. rehacer        — la moneda no es la de ninguna pata: el movimiento está expresado en la
                      moneda de un fondo distinto al que le toca. Se recalcula entero.
  3. crear_saliente — la moneda que pagamos resuelve a un fondo: falta su `EXCHANGE`.

    python -m app.cli.backfill_fund_legs              # dry-run: imprime y no escribe
    python -m app.cli.backfill_fund_legs --apply      # escribe
"""

import argparse

from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.fund import FundMovement, FundMovementType
from app.models.whatsapp_operation import WhatsAppOperation
from app.repositories.fund_repository import FundRepository
from app.services import valuation


def plan_backfill(db: Session) -> dict:
    """Qué habría que hacer con cada movimiento. No escribe nada."""
    repo = FundRepository(db)
    plan = {"retipar": [], "rehacer": [], "crear_saliente": []}

    movimientos = (
        db.query(FundMovement)
        .filter(
            FundMovement.transaction_id.isnot(None),
            FundMovement.movement_type == FundMovementType.EXCHANGE,
        )
        .order_by(FundMovement.id)
        .all()
    )

    for mov in movimientos:
        op = (
            db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.transaction_id == mov.transaction_id)
            .first()
        )
        if op is None or op.currency_pair is None:
            continue
        cp = op.currency_pair
        entra = valuation.settlement_currency(cp.from_currency.symbol) if cp.from_currency else None
        sale = valuation.settlement_currency(cp.to_currency.symbol) if cp.to_currency else None

        if mov.currency == entra:
            plan["retipar"].append((mov.id, FundMovementType.EXCHANGE_IN))
        else:
            # Expresado en la moneda de otro fondo: se recalcula desde la pata entrante.
            grupo_entra = repo.get_active_group_by_currency(entra)
            plan["rehacer"].append({
                "movement_id": mov.id,
                "group_id": grupo_entra.id if grupo_entra else None,
                "amount": round(float(op.from_amount or 0), 2),
                "currency": entra,
            })

        grupo_sale = repo.get_active_group_by_currency(sale)
        if grupo_sale is not None:
            plan["crear_saliente"].append({
                "operation_id": op.id,
                "transaction_id": op.transaction_id,
                "group_id": grupo_sale.id,
                "amount": round(float(op.to_amount or 0), 2),
                "currency": sale,
                "user_id": mov.user_id,
            })

    return plan


def apply_backfill(db: Session, plan: dict) -> None:
    """Escribe lo que dice el plan."""
    for movement_id, nuevo_tipo in plan["retipar"]:
        db.query(FundMovement).filter(FundMovement.id == movement_id).update(
            {"movement_type": nuevo_tipo}
        )

    for item in plan["rehacer"]:
        mov = db.query(FundMovement).filter(FundMovement.id == item["movement_id"]).first()
        if mov is None or item["group_id"] is None:
            continue
        eq = valuation.equivalents(db, item["amount"], item["currency"], mov.movement_date)
        mov.movement_type = FundMovementType.EXCHANGE_IN
        mov.group_id = item["group_id"]
        mov.amount = item["amount"]
        mov.currency = item["currency"]
        mov.amount_usdt = eq["usdt_amount"]
        mov.usdt_rate = (item["amount"] / eq["usdt_amount"]) if eq["usdt_amount"] else None

    for item in plan["crear_saliente"]:
        op = db.query(WhatsAppOperation).filter(
            WhatsAppOperation.id == item["operation_id"]
        ).first()
        if op is None:
            continue
        op.fund_group_out_id = item["group_id"]
        at = op.valuation_at or op.quoted_at
        eq = valuation.equivalents(db, item["amount"], item["currency"], at)
        db.add(FundMovement(
            group_id=item["group_id"],
            user_id=item["user_id"],
            movement_type=FundMovementType.EXCHANGE,
            amount=item["amount"],
            currency=item["currency"],
            amount_usdt=eq["usdt_amount"],
            usdt_rate=(item["amount"] / eq["usdt_amount"]) if eq["usdt_amount"] else None,
            transaction_id=item["transaction_id"],
            movement_date=at,
        ))

    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Arrastre de movimientos a las dos patas")
    parser.add_argument("--apply", action="store_true", help="escribe (por defecto: dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan = plan_backfill(db)
        print(f"\nretipar a EXCHANGE_IN: {len(plan['retipar'])}")
        for movement_id, _ in plan["retipar"]:
            print(f"  mov {movement_id}")
        print(f"\nrehacer (fondo y monto): {len(plan['rehacer'])}")
        for item in plan["rehacer"]:
            print(f"  mov {item['movement_id']} → fondo {item['group_id']} "
                  f"{item['amount']} {item['currency']}")
        print(f"\ncrear pata saliente: {len(plan['crear_saliente'])}")
        for item in plan["crear_saliente"]:
            print(f"  op {item['operation_id']} → fondo {item['group_id']} "
                  f"-{item['amount']} {item['currency']}")

        if args.apply:
            apply_backfill(db, plan)
            print("\nEscrito.")
        else:
            print("\nDry-run: no se escribió nada. Con --apply se aplica.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
