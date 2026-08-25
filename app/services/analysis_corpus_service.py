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
        Mapa wa_message_id → operación, en dos consultas para toda la ventana. El puente es
        `whatsapp_operation_messages`, el mismo índice que el bot usa para el revoke.
        """
        message_ids = [r.wa_message_id for r in rows if r.wa_message_id]
        if not message_ids:
            return {}
        links = (
            self.db.query(WhatsAppOperationMessage)
            .filter(WhatsAppOperationMessage.wa_message_id.in_(message_ids))
            .all()
        )
        if not links:
            return {}
        ops = {
            op.id: op
            for op in self.db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.id.in_([l.whatsapp_operation_id for l in links]))
            .all()
        }
        return {
            link.wa_message_id: ops[link.whatsapp_operation_id]
            for link in links
            if link.whatsapp_operation_id in ops
        }

    def verdict(
        self, row: WhatsAppMessageAnalysis, op: Optional[WhatsAppOperation]
    ) -> str:
        if op is None:
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
