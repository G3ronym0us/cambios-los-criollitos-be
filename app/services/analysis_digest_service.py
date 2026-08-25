"""
El resumen de lo que el analizador no pudo resolver, para mandárselo al operador una vez al
día en vez de un ping por mensaje.

Los avisos "Mensaje sin contexto" llegaban uno a uno y tapaban el chat, así que se silenciaron
y ahora todo queda en la bitácora. Pero silenciar sin más deja un caso caro invisible: un
número nuevo que quiere operar y cuyo mensaje no alcanzó para cotizar. Este resumen es la red
— no reemplaza al panel, donde se ve el detalle; solo avisa que hay algo que mirar.

Se cuenta lo NO resuelto: análisis que no derivaron en una operación y que parecían un intento
transaccional. El chit-chat no entra: es ruido correcto, no trabajo pendiente.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.whatsapp_message_analysis import WhatsAppMessageAnalysis
from app.models.whatsapp_operation_message import WhatsAppOperationMessage


class AnalysisDigestService:
    def __init__(self, db: Session):
        self.db = db

    def pending(self, hours: int = 24) -> list[WhatsAppMessageAnalysis]:
        """
        Los análisis del período que parecían operación y no produjeron ninguna.

        "No produjo operación" se mide contra `whatsapp_operation_messages`, el mismo índice
        que usa el corpus. Un mensaje que sí cotizó no es trabajo pendiente aunque el
        analizador lo haya leído raro: el operador ya lo tiene en su chat.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        marca = WhatsAppMessageAnalysis.context["looks_transactional"].astext
        rows = (
            self.db.query(WhatsAppMessageAnalysis)
            .filter(
                WhatsAppMessageAnalysis.created_at >= since,
                WhatsAppMessageAnalysis.analyzer == "heuristic-v1",
                marca == "true",
            )
            .order_by(WhatsAppMessageAnalysis.created_at.asc())
            .all()
        )
        if not rows:
            return []

        message_ids = [r.wa_message_id for r in rows if r.wa_message_id]
        con_operacion = set()
        if message_ids:
            con_operacion = {
                link.wa_message_id
                for link in self.db.query(WhatsAppOperationMessage)
                .filter(WhatsAppOperationMessage.wa_message_id.in_(message_ids))
                .all()
            }
        return [r for r in rows if r.wa_message_id not in con_operacion]

    def build_message(self, hours: int = 24) -> Optional[str]:
        """El texto a mandar, o None si no hay nada pendiente — el silencio también informa."""
        rows = self.pending(hours)
        if not rows:
            return None

        # Se agrupa por teléfono y no por mensaje: lo que el operador decide es a quién
        # atender, y un cliente que escribió cinco veces sigue siendo una sola decisión.
        por_telefono: dict[str, list[WhatsAppMessageAnalysis]] = {}
        for row in rows:
            por_telefono.setdefault(row.client_phone, []).append(row)

        nuevos = {
            phone: rs
            for phone, rs in por_telefono.items()
            if any((r.context or {}).get("untracked") for r in rs)
        }
        conocidos = {p: rs for p, rs in por_telefono.items() if p not in nuevos}

        lineas = [f"📋 *Sin resolver en {hours}h* — {len(rows)} mensajes de {len(por_telefono)} números"]

        # Los números nuevos van primero y con su texto: son el caso que cuesta dinero
        # perder, y el que motivó este resumen.
        if nuevos:
            lineas.append(f"\n🆕 *Números nuevos* ({len(nuevos)})")
            for phone, rs in list(nuevos.items())[:5]:
                muestra = (rs[-1].messages or [""])[-1].replace("\n", " ")[:60]
                lineas.append(f"  {phone} — \"{muestra}\"")
                lineas.append(f"    `!track {phone}`")
            if len(nuevos) > 5:
                lineas.append(f"  …y {len(nuevos) - 5} más")

        if conocidos:
            lineas.append(f"\n👤 *Clientes conocidos* ({len(conocidos)})")
            for phone, rs in list(conocidos.items())[:5]:
                muestra = (rs[-1].messages or [""])[-1].replace("\n", " ")[:60]
                lineas.append(f"  {phone} ({len(rs)}) — \"{muestra}\"")
            if len(conocidos) > 5:
                lineas.append(f"  …y {len(conocidos) - 5} más")

        lineas.append("\nEl detalle está en el panel, en Análisis.")
        return "\n".join(lineas)
