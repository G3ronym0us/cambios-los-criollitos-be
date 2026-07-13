"""Completa entregas recibidas que quedaron PENDING y genera sus transacciones.

Uso:
    docker compose exec -T backend python -m app.cli.repair_received_deliveries
"""

import sys

from app.core.config import settings
from app.database.connection import SessionLocal
from app.models.user import User
from app.services.whatsapp_quote_service import WhatsAppQuoteService


def repair_received_deliveries() -> list[str]:
    email = getattr(settings, "BOT_SERVICE_USER_EMAIL", None)
    if not email:
        raise ValueError("BOT_SERVICE_USER_EMAIL no está configurado")

    db = SessionLocal()
    try:
        service_user = db.query(User).filter(User.email == email).first()
        if service_user is None:
            raise ValueError(f"No existe el bot service user: {email}")

        repaired = WhatsAppQuoteService(db).repair_received_deliveries(service_user)
        print(f"Operaciones reparadas: {len(repaired)}")
        for op_uuid in repaired:
            print(op_uuid)
        return repaired
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    try:
        repair_received_deliveries()
    except Exception as exc:  # noqa: BLE001
        print(f"Error reparando entregas recibidas: {exc}")
        sys.exit(1)
