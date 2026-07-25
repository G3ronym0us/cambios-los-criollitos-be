"""
Llena el margen (`applied_percentage`) de las operaciones que nacieron de un comprobante y
recalcula la ganancia de su transacción.

`create_operation_from_payment` no asignaba margen —solo lo hacía la cotización del bot—, así
que esas operaciones quedaron con ganancia 0 aunque se hubieran cobrado a la tasa del día. El
margen se lee de la tasa que usó la operación contra la tasa base del par vigente ESE día
(`WhatsAppRateResolver.implied_margin`), no con la de hoy.

No toca:
  - las migradas del SQLite del bot (`legacy_sqlite_id`), que nunca tuvieron margen que leer;
  - las que ya tienen `applied_percentage`;
  - aquellas cuya tasa no salió del par (BCV, manual, cruce por USDT) o da un margen fuera de
    rango: ahí inventaríamos una ganancia. Se reportan al final.

    python -m app.cli.backfill_operation_percentages --dry-run
    python -m app.cli.backfill_operation_percentages --since 2026-07-01
"""

import argparse
from datetime import datetime, timezone

from app.database.connection import SessionLocal
from app.models.transaction import Transaction
from app.models.whatsapp_operation import WhatsAppOperation
from app.services.whatsapp_quote_service import WhatsAppQuoteService
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver


def _pair_sides(op: WhatsAppOperation) -> tuple[str, str]:
    """
    Las dos monedas de la operación en el sentido en que se guardó `rate_used` (to por from).
    El valor manda: el `from` es la moneda de la op y el `to`, el otro lado del par.
    """
    pair = op.currency_pair
    left = pair.from_currency.symbol if pair and pair.from_currency else None
    right = pair.to_currency.symbol if pair and pair.to_currency else None
    source = (op.currency or left or "").upper()
    if source and right and source == right.upper():
        return source, (left or "").upper()
    return source, (right or "").upper()


def run(dry_run: bool = False, since: str = "2026-07-01", limit: int = 0) -> None:
    cutoff = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        resolver = WhatsAppRateResolver(db)
        quote_svc = WhatsAppQuoteService(db)

        query = (
            db.query(WhatsAppOperation)
            .filter(
                WhatsAppOperation.applied_percentage.is_(None),
                WhatsAppOperation.legacy_sqlite_id.is_(None),
                WhatsAppOperation.transaction_id.isnot(None),
                WhatsAppOperation.created_at >= cutoff,
            )
            .order_by(WhatsAppOperation.id)
        )
        if limit:
            query = query.limit(limit)
        ops = query.all()
        print(f"{len(ops)} operaciones sin margen desde {cutoff.date()}\n")

        done = 0
        profit_before = 0.0
        profit_after = 0.0
        skipped: dict[str, int] = {}
        for op in ops:
            from_currency, to_currency = _pair_sides(op)
            at = op.quoted_at or op.created_at
            effective = WhatsAppRateResolver.apply_rate(1.0, op.rate_used, op.inverse_percentage)
            entry = resolver.get_rate_entry_for_pair(from_currency, to_currency, at=at)
            margin = WhatsAppRateResolver.implied_margin(entry, effective)

            if margin is None:
                reason = (
                    f"sin tasa histórica {from_currency}→{to_currency}" if entry is None
                    else f"{from_currency}→{to_currency} sin margen legible en su tasa"
                )
                skipped[reason] = skipped.get(reason, 0) + 1
                continue

            tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
            before = float(tx.profit_amount or 0) if tx else 0.0
            after = float(op.amount_usdt or 0) * margin / 100
            print(
                f"  op {op.id:>5} {op.amount:>10.2f} {op.currency:<6} @ {effective:>12.4f}"
                f"  → {margin:>6.2f}%   ganancia {before:.2f} → {after:.2f} USDT"
            )

            if not dry_run:
                op.applied_percentage = margin
                op.default_percentage = entry.base_percentage
                quote_svc.resync_transaction(op)

            profit_before += before
            profit_after += after
            done += 1

        if not dry_run:
            db.commit()

        prefix = "(dry-run) " if dry_run else ""
        print(f"\n{prefix}{done} operaciones con margen; ganancia total "
              f"{profit_before:.2f} → {profit_after:.2f} USDT")
        for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
            print(f"  ⏭️  {count} sin tocar: {reason}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Llena el margen de las operaciones sin él")
    parser.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    parser.add_argument("--since", default="2026-07-01", help="fecha mínima (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=0, help="procesa solo N operaciones")
    args = parser.parse_args()
    run(dry_run=args.dry_run, since=args.since, limit=args.limit)
