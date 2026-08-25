"""
Escritura y mantenimiento de la bitácora del analizador (`whatsapp_message_analyses`).

Sostiene tres cosas y ninguna es una decisión del bot: registrar una corrida del analizador,
etiquetar a mano una fila, y purgar lo viejo. Ver el docstring del modelo para qué es el
corpus y de dónde salen las etiquetas.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.currency_pair import CurrencyPair
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_message_analysis import WhatsAppMessageAnalysis

# Un mensaje de WhatsApp admite decenas de miles de caracteres y aquí no aportan nada: lo
# que se analiza es un pedido de cambio. Se recorta por fila para que un pegote no infle la
# tabla ni el JSONL del export.
MAX_MESSAGE_CHARS = 4000
MAX_MESSAGES = 10


class AnalysisLogService:
    def __init__(self, db: Session):
        self.db = db

    def record(
        self,
        *,
        client_phone: str,
        messages: list[str],
        output: dict[str, Any],
        wa_message_id: Optional[str] = None,
        analyzer: str = "heuristic-v1",
        context: Optional[dict[str, Any]] = None,
    ) -> WhatsAppMessageAnalysis:
        """
        Deja constancia de una corrida del analizador.

        El par por defecto se resuelve aquí y no en el bot: es una lectura local que el
        backend ya tiene a mano, y pedírsela al bot costaría un request por mensaje. Leerlo
        después tampoco sirve — daría el par de hoy y no el que había al analizar.
        """
        row = WhatsAppMessageAnalysis(
            wa_message_id=wa_message_id,
            client_phone=client_phone,
            messages=[m[:MAX_MESSAGE_CHARS] for m in messages[-MAX_MESSAGES:]],
            analyzer=analyzer,
            output=output,
            context=context,
            default_pair_symbol=self._default_pair_symbol(client_phone),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def set_label(
        self, analysis_uuid: UUID, label: dict[str, Any], source: str = "manual"
    ) -> Optional[WhatsAppMessageAnalysis]:
        """Corrige a mano lo que el join con la operación no resuelve. None si no existe."""
        row = (
            self.db.query(WhatsAppMessageAnalysis)
            .filter(WhatsAppMessageAnalysis.uuid == analysis_uuid)
            .first()
        )
        if row is None:
            return None
        row.label = label
        row.label_source = source
        row.labeled_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return row

    def purge(self, days: int, include_labeled: bool = False, dry_run: bool = False) -> int:
        """
        Borra las filas más viejas que `days`. Devuelve cuántas (o cuántas borraría).

        Las etiquetadas se respetan por defecto: son el dataset ya revisado y su valor no
        caduca, mientras que una fila cruda sin etiquetar es solo texto de un cliente
        guardado sin haber servido para nada.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = self.db.query(WhatsAppMessageAnalysis).filter(
            WhatsAppMessageAnalysis.created_at < cutoff
        )
        if not include_labeled:
            query = query.filter(WhatsAppMessageAnalysis.label.is_(None))
        if dry_run:
            return query.count()
        deleted = query.delete(synchronize_session=False)
        self.db.commit()
        return deleted

    def _default_pair_symbol(self, phone: str) -> Optional[str]:
        row = (
            self.db.query(CurrencyPair.pair_symbol)
            .join(WhatsAppClient, WhatsAppClient.preferred_pair_id == CurrencyPair.id)
            .filter(WhatsAppClient.phone == phone)
            .first()
        )
        return row[0] if row else None
