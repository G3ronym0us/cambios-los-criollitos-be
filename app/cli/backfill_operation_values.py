"""
Llena los equivalentes del valor de las operaciones (`amount_usdt`, `usdt_rate`, `bcv_amount`,
`bcv_rate`, `valuation_at`) para las que quedaron sin ellos: las anteriores a la migración
`d8e9f0a1b2c3`, que solo pudo copiar `amount` y `currency` en SQL.

Se valora con la tasa base vigente a la fecha de la operación (`app/services/valuation.py`),
no con la de hoy: una operación de marzo se valora con la tasa de marzo.

    python -m app.cli.backfill_operation_values --dry-run   # solo informa
    python -m app.cli.backfill_operation_values             # escribe
"""

import argparse

from app.database.connection import SessionLocal
from app.models.whatsapp_operation import WhatsAppOperation
from app.services import valuation


def run(dry_run: bool = False, limit: int = 0) -> None:
    db = SessionLocal()
    try:
        q = (
            db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.amount.isnot(None), WhatsAppOperation.valuation_at.is_(None))
            .order_by(WhatsAppOperation.id)
        )
        if limit:
            q = q.limit(limit)
        rows = q.all()
        print(f"{len(rows)} operaciones por valorar")

        done = 0
        sin_tasa: dict[str, int] = {}
        for op in rows:
            at = op.quoted_at or op.created_at
            value = valuation.equivalents(db, op.amount, op.currency, at)
            if value["usdt_amount"] is None:
                key = f"{op.currency}→USDT"
                sin_tasa[key] = sin_tasa.get(key, 0) + 1
            if not dry_run:
                op.amount_usdt = value["usdt_amount"]
                op.usdt_rate = value["usdt_rate"]
                op.bcv_amount = value["bcv_amount"]
                op.bcv_rate = value["bcv_rate"]
                op.valuation_at = at
            done += 1

        if not dry_run:
            db.commit()

        print(f"{'(dry-run) ' if dry_run else ''}{done} operaciones valoradas")
        for key, count in sorted(sin_tasa.items(), key=lambda kv: -kv[1]):
            print(f"  ⚠️  {count} sin tasa histórica {key} (quedan sin equivalente USDT)")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valora las operaciones sin equivalentes")
    parser.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    parser.add_argument("--limit", type=int, default=0, help="procesa solo N operaciones")
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
