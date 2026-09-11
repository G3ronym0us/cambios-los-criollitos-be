"""
Las operaciones que se quedaron en QUOTED con su comprobante entrante ya vinculado.

Pasaba porque `set_operation` solo sincronizaba el estado en la rama de SALIENTES: un entrante
se enganchaba (por FK directo o por `whatsapp_payment_allocations`, si el comprobante se
repartió) y la cotización seguía QUOTED y vencida, fuera de todas las bandejas. Arreglado en
el mismo cambio que trae este arrastre (`WhatsAppPaymentService._sync_status_from_incoming`);
esto es solo para las que ya quedaron así. En producción son 10.

No reimplementa la regla: llama al mismo método que usa `set_operation` en caliente, así que
las mismas guardas aplican (solo toca QUOTED/PENDING, el TTL no bloquea, mira FK y allocations).

    python -m app.cli.backfill_incoming_quoted_to_pending --dry-run   # solo informa
    python -m app.cli.backfill_incoming_quoted_to_pending             # escribe
"""

import argparse

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppPaymentAllocation
from app.services.whatsapp_payment_service import WhatsAppPaymentService


def find_stuck_operations(db: Session) -> list[WhatsAppOperation]:
    """QUOTED con respaldo entrante ya puesto, por FK directo o por reparto."""
    con_fk = db.query(WhatsAppIncomingPayment.whatsapp_operation_id).filter(
        WhatsAppIncomingPayment.whatsapp_operation_id.isnot(None)
    )
    con_allocation = db.query(WhatsAppPaymentAllocation.whatsapp_operation_id)

    return (
        db.query(WhatsAppOperation)
        .filter(WhatsAppOperation.status == WhatsAppOperationStatus.QUOTED)
        .filter(
            or_(
                WhatsAppOperation.id.in_(con_fk),
                WhatsAppOperation.id.in_(con_allocation),
            )
        )
        .order_by(WhatsAppOperation.id)
        .all()
    )


def run(db: Session, dry_run: bool = False) -> int:
    """Ejecuta el arrastre sobre `db` y devuelve cuántas operaciones pasaron (o pasarían, en
    dry-run) a PENDING. `db` es un parámetro -no una sesión abierta aquí dentro- para que el
    test de integración pueda correr esto contra su propia sesión transaccional."""
    service = WhatsAppPaymentService(db)
    ops = find_stuck_operations(db)
    print(f"{len(ops)} operaciones en QUOTED con comprobante entrante ya vinculado")

    moved = 0
    for op in ops:
        cliente = (op.client.display_name or op.client.phone) if op.client else "—"
        print(
            f"  op {op.id} ({op.uuid}) cliente={cliente} "
            f"{op.from_amount} {op.currency} → {op.to_amount}  cotizada {op.quoted_at}"
        )
        if not dry_run:
            # La regla vive en un solo sitio: aquí no se copia el UPDATE, se llama al método
            # que ya la aplica desde `set_operation`. `had_incoming=True` porque el filtro de
            # arriba ya garantiza que la op tenía comprobante antes de esta pasada -- este flag
            # solo importa para el camino de vuelta (PENDING→QUOTED), que aquí no se dispara:
            # todas siguen QUOTED al entrar.
            service._sync_status_from_incoming(op, None, had_incoming=True)
            db.flush()
            if op.status == WhatsAppOperationStatus.PENDING:
                moved += 1

    if dry_run:
        print("dry-run: no se escribió nada")
    else:
        db.commit()
        print(f"{moved} operaciones pasadas a PENDING")
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pasa a PENDING las operaciones QUOTED con un comprobante entrante ya vinculado"
    )
    parser.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        run(db, dry_run=args.dry_run)
    finally:
        db.close()


if __name__ == "__main__":
    main()
