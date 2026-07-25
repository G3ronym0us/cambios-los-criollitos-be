"""
Crea el reparto de las operaciones que ya estaban registradas: todo al fondo que las atendió,
por su porcentaje configurado, y recalcula la ganancia de su transacción y la de cada socio.

Antes de esto la ganancia de una operación era el margen entero que se le cobró al cliente y
no tenía dueño. Ahora el fondo se queda su parte (7 de 8) y sus socios se la reparten, así que
la «Acumulada» del fondo y los reportes por usuario vuelven a moverse.

Solo toca operaciones COMPLETED con fondo y con transacción: una operación sin fondo no
reparte, sin transacción no hay ganancia que recalcular, y una cotización que nunca se cerró
no dejó ganancia (entre ellas hay cotizaciones disparatadas —"cuánto es 1.176.000.000 Bs"—
que solo meterían ruido). Es idempotente: una operación que ya tiene reparto se salta.

    python -m app.cli.backfill_profit_allocations --dry-run
    python -m app.cli.backfill_profit_allocations --since 2026-07-01
"""

import argparse
from datetime import datetime, timezone

from app.database.connection import SessionLocal
from app.models.fund import FundGroup
from app.models.transaction import Transaction
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.services.profit_allocation_service import ProfitAllocationService
from app.services.whatsapp_quote_service import WhatsAppQuoteService


def run(dry_run: bool = False, since: str = "2026-07-01", limit: int = 0) -> None:
    cutoff = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        allocation_svc = ProfitAllocationService(db)
        quote_svc = WhatsAppQuoteService(db)

        query = (
            db.query(WhatsAppOperation)
            .filter(
                WhatsAppOperation.status == WhatsAppOperationStatus.COMPLETED,
                WhatsAppOperation.fund_group_id.isnot(None),
                WhatsAppOperation.transaction_id.isnot(None),
                WhatsAppOperation.applied_percentage.isnot(None),
                WhatsAppOperation.created_at >= cutoff,
            )
            .order_by(WhatsAppOperation.id)
        )
        if limit:
            query = query.limit(limit)
        ops = query.all()
        print(f"{len(ops)} operaciones completadas con fondo desde {cutoff.date()}\n")

        done = 0
        skipped = 0
        profit_after = 0.0
        by_group: dict[str, float] = {}

        for op in ops:
            if allocation_svc.allocations(op):
                skipped += 1
                continue

            group = db.query(FundGroup).filter(FundGroup.id == op.fund_group_id).first()
            tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
            before = float(tx.profit_amount or 0) if tx else 0.0

            allocations = allocation_svc.ensure_defaults(op)
            if not allocations:
                skipped += 1
                continue
            allocation_svc.sync_amounts(op, op.amount_usdt)
            quote_svc.resync_transaction(op)

            percentage = allocations[0].percentage
            after = float(op.amount_usdt or 0) * percentage / 100
            shares = allocation_svc.member_shares(op)
            print(
                f"  op {op.id:>5} {op.amount:>9.2f} {op.currency:<6}"
                f"  cobrado {float(op.applied_percentage):>5.2f}% → {group.name} {percentage:>5.2f}%"
                f"   ganancia {before:.2f} → {after:.2f} USDT"
                + (f"   ({', '.join(f'{u.username} {p:.2f}%' for u, p in shares)})" if shares else "")
            )

            by_group[group.name] = by_group.get(group.name, 0) + after
            profit_after += after
            done += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

        prefix = "(dry-run) " if dry_run else ""
        print(f"\n{prefix}{done} operaciones repartidas ({skipped} ya lo tenían o sin fondo)")
        # El total anterior no se suma: las transacciones viejas tienen la ganancia en la
        # moneda del par (VES), así que sumarlas con las nuevas en USDT no significa nada.
        print(f"{prefix}ganancia repartida a fondos: {profit_after:.2f} USDT")
        for name, total in sorted(by_group.items(), key=lambda kv: -kv[1]):
            print(f"   {name}: {total:.2f} USDT")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reparte al fondo la ganancia de las operaciones")
    parser.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    parser.add_argument("--since", default="2026-07-01", help="fecha mínima (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=0, help="procesa solo N operaciones")
    args = parser.parse_args()
    run(dry_run=args.dry_run, since=args.since, limit=args.limit)
