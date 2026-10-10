"""
Crea la fila de reparto de los comprobantes SALIENTES que nacieron ya vinculados a su
operación sólo por FK (los que crea el bot con `operation_uuid`). Lo entregado se lee del
reparto (`whatsapp_outgoing_settlements`), así que esas operaciones mostraban «Entregado
0,00 de <valor>» y la cuenta del cliente las pintaba «sin comprobante» aunque su pago
estuviera ahí (op 5091 de Neurys; 876 salientes así en producción, 2026-10-10).

Se liquida con la tasa COTIZADA de la operación y con tope en lo que le falta, igual que al
vincular desde el panel (`WhatsAppPaymentService._settle_linked_payout`). Lo que no se
puede medir así —otra moneda, sin monto, operación ya cubierta por otro comprobante— se
informa y se deja como está, para decidirlo a mano.

    python -m app.cli.backfill_outgoing_settlements --dry-run   # solo informa
    python -m app.cli.backfill_outgoing_settlements             # escribe
"""

import argparse

from app.database.connection import SessionLocal
from app.models.whatsapp_operation import WhatsAppOperation
from app.models.whatsapp_payment import WhatsAppOutgoingPayment, WhatsAppOutgoingSettlement
from app.services.whatsapp_payment_service import WhatsAppPaymentService


def run(dry_run: bool = False, limit: int = 0) -> None:
    db = SessionLocal()
    try:
        service = WhatsAppPaymentService(db)
        q = (
            db.query(WhatsAppOutgoingPayment)
            .filter(
                WhatsAppOutgoingPayment.whatsapp_operation_id.isnot(None),
                ~db.query(WhatsAppOutgoingSettlement.id)
                .filter(WhatsAppOutgoingSettlement.outgoing_payment_id == WhatsAppOutgoingPayment.id)
                .exists(),
            )
            .order_by(WhatsAppOutgoingPayment.created_at, WhatsAppOutgoingPayment.id)
        )
        if limit:
            q = q.limit(limit)
        rows = q.all()
        print(f"{len(rows)} comprobantes salientes vinculados sin reparto")

        settled = 0
        skipped: dict[str, int] = {}
        for payment in rows:
            op = (
                db.query(WhatsAppOperation)
                .filter(WhatsAppOperation.id == payment.whatsapp_operation_id)
                .first()
            )
            reason = "sin operación" if op is None else service._settle_linked_payout(payment, op)
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            settled += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

        prefix = "(dry-run) " if dry_run else ""
        print(f"{prefix}{settled} comprobantes con su reparto creado")
        for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
            print(f"  ⚠️  {count} sin tocar: {reason}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Crea el reparto de los salientes que se crearon ya vinculados a su operación"
    )
    parser.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    parser.add_argument("--limit", type=int, default=0, help="procesa solo N comprobantes")
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
