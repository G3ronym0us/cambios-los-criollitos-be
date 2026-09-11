"""
Qué cambia la sugerencia nueva contra los comprobantes REALES de producción.

No se despliega nada sin correr esto. Se lanza contra una copia de la base de prod (o contra
prod en solo lectura) y responde tres preguntas:

  1. cuántas sugerencias de OTRO cliente quedan — el defecto que se está arreglando: debe ser 0
  2. cuántas filas pasan a CREATE — debería acercarse al 18% de clientes no seguidos
  3. cuántas sugerencias CORRECTAS se pierden por la ventana de ±72 h — debería ser ~0,
     porque el p99 del desfase real medido es 23,8 h. Si NO es ~0, la ventana está mal
     elegida y se revisa ANTES de desplegar.

El punto 3 no se puede contestar con el matcher (el vínculo bueno ya está hecho): se cuenta
contra los vínculos que YA existen, que son la verdad de campo.

    docker compose exec -T backend python scripts/diff_suggestions.py

En local, fuera del contenedor, hace falta la raíz del proyecto en el path:

    PYTHONPATH=. venv/bin/python scripts/diff_suggestions.py
"""

from sqlalchemy import text

from app.database.connection import SessionLocal
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.services.operation_match_service import (
    INCOMING_WINDOW_HOURS,
    OperationMatchService,
)


def main() -> None:
    db = SessionLocal()
    try:
        ids = [
            r[0]
            for r in db.query(WhatsAppIncomingPayment.id)
            .filter(WhatsAppIncomingPayment.is_irrelevant.is_(False))
            .order_by(WhatsAppIncomingPayment.id)
            .all()
        ]
        svc = OperationMatchService(db)
        nuevas = {i["payment_id"]: i for i in svc.suggest_for_payments(ids, "incoming")}

        crear = sum(1 for i in nuevas.values() if i["kind"] == "CREATE")
        otro_cliente = sum(1 for i in nuevas.values() if not i.get("same_client", True))
        abonos = sum(1 for i in nuevas.values() if i.get("coverage") == "PARTIAL")

        print(f"comprobantes            {len(ids)}")
        print(f"con sugerencia LINK     {len(nuevas) - crear}")
        print(f"  de las que abonan     {abonos}")
        print(f"  de OTRO cliente       {otro_cliente}   (debe ser 0)")
        print(f"proponen CREATE         {crear}   ({crear * 100 // max(1, len(ids))}%)")

        # Punto 3: vínculos ya hechos —y por tanto correctos— que la ventana habría descartado.
        fuera = db.execute(
            text(
                """
                SELECT count(*) FROM whatsapp_incoming_payments p
                JOIN whatsapp_operations o ON o.id = p.whatsapp_operation_id
                WHERE abs(EXTRACT(EPOCH FROM (p.created_at - o.created_at))) / 3600.0 > :h
                """
            ),
            {"h": float(INCOMING_WINDOW_HOURS)},
        ).scalar_one()
        vinculos = db.execute(
            text(
                "SELECT count(*) FROM whatsapp_incoming_payments "
                "WHERE whatsapp_operation_id IS NOT NULL"
            )
        ).scalar_one()
        print(
            f"vínculos reales         {vinculos}\n"
            f"  fuera de ±{INCOMING_WINDOW_HOURS:g} h        {fuera}   "
            f"(si es más que un puñado, sube INCOMING_WINDOW_HOURS y vuelve a medir)"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
