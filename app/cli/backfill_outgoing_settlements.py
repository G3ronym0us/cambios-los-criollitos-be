"""
Llena `settled_amount` de los comprobantes SALIENTES que nacieron ya vinculados a su
operación (los que crea el bot con `operation_uuid`). Ese camino nunca liquidaba el
comprobante, así que la operación mostraba "Entregado 0,00 de <valor> · pendiente <valor>"
aunque su pago saliente estuviera ahí.

Se liquida con la tasa COTIZADA de la operación (`rate_used`), no con la de hoy: solo se
tocan los comprobantes cuya moneda es la de destino de la operación, que es donde esa tasa
aplica. Los demás se informan y se dejan como están —hay que decidirlos a mano, porque
medirlos con la tasa vigente falsearía una operación vieja.

    python -m app.cli.backfill_outgoing_settlements --dry-run   # solo informa
    python -m app.cli.backfill_outgoing_settlements             # escribe
"""

import argparse

from app.database.connection import SessionLocal
from app.models.whatsapp_operation import WhatsAppOperation
from app.models.whatsapp_payment import WhatsAppOutgoingPayment
from app.services.whatsapp_payment_service import WhatsAppPaymentService


def run(dry_run: bool = False, limit: int = 0) -> None:
    db = SessionLocal()
    try:
        service = WhatsAppPaymentService(db)
        q = (
            db.query(WhatsAppOutgoingPayment)
            .filter(
                WhatsAppOutgoingPayment.whatsapp_operation_id.isnot(None),
                WhatsAppOutgoingPayment.settled_amount.is_(None),
            )
            .order_by(WhatsAppOutgoingPayment.id)
        )
        if limit:
            q = q.limit(limit)
        rows = q.all()
        print(f"{len(rows)} comprobantes salientes sin liquidar")

        settled = 0
        skipped: dict[str, int] = {}

        def skip(reason: str) -> None:
            skipped[reason] = skipped.get(reason, 0) + 1

        for payment in rows:
            op = (
                db.query(WhatsAppOperation)
                .filter(WhatsAppOperation.id == payment.whatsapp_operation_id)
                .first()
            )
            if op is None:
                skip("sin operación")
                continue
            cp = op.currency_pair
            quoted_to = cp.to_currency.symbol if cp and cp.to_currency else None
            payment_currency = (payment.currency or "").upper()
            if not quoted_to or payment_currency != quoted_to.upper():
                skip(f"moneda {payment_currency or '—'} ≠ destino {quoted_to or '—'}")
                continue
            if not op.rate_used:
                skip("operación sin tasa cotizada")
                continue
            if not payment.amount:
                skip("comprobante sin monto")
                continue

            rate = float(1 / op.rate_used) if op.inverse_percentage else float(op.rate_used)
            value = round(float(payment.amount) / rate, 2)
            # Mismo criterio que la liquidación en vivo: lo que se queda a centavos del
            # pendiente es redondeo del pago, no plata que falte.
            value = service._snap_to_pending(value, op, payment)
            if value <= 0:
                skip("cubriría 0 o menos")
                continue
            if not dry_run:
                payment.settled_amount = value
                payment.settled_reference_rate = rate
                # La sesión va sin autoflush: sin esto, dos comprobantes de la misma
                # operación se medirían los dos contra el pendiente entero.
                db.flush()
            settled += 1

        if not dry_run:
            db.commit()

        prefix = "(dry-run) " if dry_run else ""
        print(f"{prefix}{settled} comprobantes liquidados")
        for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
            print(f"  ⚠️  {count} sin tocar: {reason}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Liquida los salientes que se crearon ya vinculados a su operación"
    )
    parser.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    parser.add_argument("--limit", type=int, default=0, help="procesa solo N comprobantes")
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
