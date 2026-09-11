"""
Lectura del corpus: qué pasó DESPUÉS de cada análisis.

La bitácora guarda lo que el analizador leyó; si leyó bien no se sabe hasta ver qué hizo el
operador con la operación. Este service hace ese join y le pone un veredicto a cada fila.

Es derivación pura: no escribe nada y se puede rehacer entera cuando cambie el criterio. Por
eso vive aquí y no en una columna — un veredicto guardado envejece mal, y el día que se
descubra que `cancelled` en realidad tapaba dos casos distintos habría que migrar filas en
vez de cambiar una función.

Lo consumen `app.cli.export_analysis_corpus` (JSONL para entrenar) y
`app.cli.import_analysis_labels` (la corrección a mano vuelve a entrar).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.whatsapp_message_analysis import WhatsAppMessageAnalysis
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.models.whatsapp_operation_message import WhatsAppOperationMessage

SUPERSEDED_MARK = "[cancel] superseded_by_correction"

# Una corrección cancela la cotización vieja justo después de crear la nueva. La ventana es
# amplia a propósito: de más agarra la op equivocada del mismo cliente, de menos se pierde el
# reemplazo y la fila se queda sin su etiqueta buena.
REPLACEMENT_WINDOW = timedelta(minutes=5)

# Los que necesitan ojo humano: el join llega hasta aquí y no más.
NEEDS_REVIEW = ("ghost_quote", "cancelled")

VERDICTS = {
    "correct": "op COMPLETED — la lectura sostuvo una operación que se cerró",
    "superseded": "op cancelada por corrección — leyó mal; el reemplazo trae el bueno",
    "cancelled": "op cancelada por otro motivo — ambiguo, mirar a mano",
    "open": "op todavía QUOTED/PENDING — aún no dice nada",
    "ghost_quote": "dedujo QUOTE con monto y no nació ninguna operación",
    "op_gone": "sí nació una operación, pero hoy no está en la base — no dice nada del análisis",
    "no_action": "no dedujo cotización y no nació operación — el caso aburrido y correcto",
}


class AnalysisCorpusService:
    def __init__(self, db: Session):
        self.db = db

    def rows_since(self, days: int) -> list[WhatsAppMessageAnalysis]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return (
            self.db.query(WhatsAppMessageAnalysis)
            .filter(WhatsAppMessageAnalysis.created_at >= cutoff)
            .order_by(WhatsAppMessageAnalysis.created_at.asc())
            .all()
        )

    def operations_for(
        self, rows: list[WhatsAppMessageAnalysis]
    ) -> dict[str, WhatsAppOperation]:
        """
        Mapa wa_message_id → operación.

        La vía principal es `whatsapp_operation_messages`, el mismo índice que el bot usa
        para el revoke. Pero esa tabla solo existe desde el 2026-07-27, y el mapa que el bot
        guarda en su SQLite viene de julio: las filas del backfill traen su operación de
        origen en `context.source_operation_uuid`, y sin esa segunda vía 300 análisis que sí
        produjeron una cotización se leerían como que no produjeron ninguna.
        """
        message_ids = [r.wa_message_id for r in rows if r.wa_message_id]
        found: dict[str, WhatsAppOperation] = {}
        if message_ids:
            links = (
                self.db.query(WhatsAppOperationMessage)
                .filter(WhatsAppOperationMessage.wa_message_id.in_(message_ids))
                .all()
            )
            if links:
                ops = {
                    op.id: op
                    for op in self.db.query(WhatsAppOperation)
                    .filter(
                        WhatsAppOperation.id.in_([l.whatsapp_operation_id for l in links])
                    )
                    .all()
                }
                found = {
                    link.wa_message_id: ops[link.whatsapp_operation_id]
                    for link in links
                    if link.whatsapp_operation_id in ops
                }

        # Segunda vía, solo para lo que quedó sin resolver.
        pending = {
            r.wa_message_id: source_operation_uuid(r)
            for r in rows
            if r.wa_message_id and r.wa_message_id not in found and source_operation_uuid(r)
        }
        if pending:
            by_uuid = {
                str(op.uuid): op
                for op in self.db.query(WhatsAppOperation)
                .filter(WhatsAppOperation.uuid.in_(list(pending.values())))
                .all()
            }
            for message_id, op_uuid in pending.items():
                op = by_uuid.get(op_uuid)
                if op is not None:
                    found[message_id] = op
        return found

    def verdict(
        self, row: WhatsAppMessageAnalysis, op: Optional[WhatsAppOperation]
    ) -> str:
        if op is None:
            # Si la fila sabe de qué operación nació, que no aparezca no dice nada sobre la
            # lectura: la operación se borró. Llamarlo `ghost_quote` acusaría al analizador
            # de inventar una cotización que en realidad sí existió — y para las filas del
            # backfill, que salen del mapa de mensajes que crearon cotizaciones, ese
            # veredicto es estructuralmente imposible.
            if source_operation_uuid(row):
                return "op_gone"
            output = row.output or {}
            deduced_quote = output.get("intent") == "QUOTE" and output.get("amount") is not None
            return "ghost_quote" if deduced_quote else "no_action"
        if op.status == WhatsAppOperationStatus.COMPLETED:
            return "correct"
        if op.status == WhatsAppOperationStatus.CANCELLED:
            return "superseded" if SUPERSEDED_MARK in (op.notes or "") else "cancelled"
        return "open"

    def find_replacement(self, op: WhatsAppOperation) -> Optional[WhatsAppOperation]:
        """
        La cotización que reemplazó a `op`. No hay vínculo explícito en la base: se busca la
        siguiente del mismo cliente dentro de la ventana. Es una inferencia, y por eso el
        JSONL la marca aparte en vez de mezclarla con lo que sí está registrado.
        """
        if op.cancelled_at is None:
            return None
        return (
            self.db.query(WhatsAppOperation)
            .filter(
                WhatsAppOperation.client_id == op.client_id,
                WhatsAppOperation.id != op.id,
                WhatsAppOperation.quoted_at >= op.cancelled_at - REPLACEMENT_WINDOW,
                WhatsAppOperation.quoted_at <= op.cancelled_at + REPLACEMENT_WINDOW,
            )
            .order_by(WhatsAppOperation.quoted_at.asc())
            .first()
        )

    def enrich(
        self, row: WhatsAppMessageAnalysis, op: Optional[WhatsAppOperation]
    ) -> dict[str, Any]:
        """La fila con su veredicto y lo que pasó con su operación, lista para el JSONL."""
        verdict = self.verdict(row, op)
        record = {
            "uuid": str(row.uuid),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "client_phone": row.client_phone,
            "messages": row.messages,
            "context": row.context,
            "default_pair_symbol": row.default_pair_symbol,
            "analyzer": row.analyzer,
            "output": row.output,
            "verdict": verdict,
            "operation": _op_summary(op) if op else None,
            "replacement": None,
            # La corrección a mano manda sobre lo derivado: si alguien revisó la fila, su
            # veredicto es el que vale al armar el dataset.
            "label": row.label,
            "label_source": row.label_source,
        }
        if verdict == "superseded" and op is not None:
            replacement = self.find_replacement(op)
            if replacement is not None:
                record["replacement"] = _op_summary(replacement)
        return record


def source_operation_uuid(row: WhatsAppMessageAnalysis) -> Optional[str]:
    """
    La operación de la que la fila dice haber nacido, si lo sabe.

    Solo la traen las filas del backfill, que la leen del mapa mensaje→operación del SQLite
    del bot. Las de la captura viva no la tienen: cuando el bot analiza todavía no existe
    ninguna operación, así que para ellas el único puente posible es el índice del backend.
    """
    context = row.context or {}
    value = context.get("source_operation_uuid")
    return value if isinstance(value, str) and value else None


def _op_summary(op: WhatsAppOperation) -> dict[str, Any]:
    pair = op.currency_pair
    return {
        "operation_uuid": str(op.uuid),
        "status": op.status.value if op.status else None,
        "pair_symbol": pair.pair_symbol if pair else None,
        "from_amount": op.from_amount,
        "to_amount": op.to_amount,
        "amount_side": op.amount_side.value if op.amount_side else None,
        "bcv_usd": op.bcv_usd,
        "quoted_at": op.quoted_at.isoformat() if op.quoted_at else None,
    }
