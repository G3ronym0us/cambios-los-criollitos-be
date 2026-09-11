"""
Agregado de `GET /admin/overview`: la home del panel en un solo viaje.

No recalcula nada — compone sobre los servicios que ya tienen la lógica de cada cifra
(`WhatsAppPaymentService.payments_stats`, `WhatsAppQuoteService.get_stats`,
`ClientPendingService.pending_overview`, `RateAlertRepository`, `TransactionRepository`).
Este módulo solo decide QUÉ mostrar y CÓMO aislar un bloque que falla.

Cada bloque corre en su propio try/except: si uno revienta, va en `null` con su nombre en
`errors` y el resto se sirve igual con 200. Es un estado real del diseño («no se pudieron
leer las divergencias; las otras cifras están al día»), no un atajo.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.core.timezones import CARACAS_TZ
from app.enums.user_roles import UserRole
from app.models.user import User
from app.repositories.rate_alert_repository import RateAlertRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.admin_overview import (
    OverviewAlerts,
    OverviewClients,
    OverviewMe,
    OverviewOperations,
    OverviewPayments,
)
from app.services.client_pending_service import ClientPendingService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import WhatsAppQuoteService

logger = logging.getLogger(__name__)


class AdminOverviewService:
    def __init__(self, db: Session):
        self.db = db

    def get_overview(self, current_user: User) -> dict:
        role = current_user.role.value if current_user.role else UserRole.MODERATOR.value
        errors: list[str] = []

        payload: dict = {
            # "Z", no "+00:00": el resto de campos con fecha ya sale así porque pasan por
            # `model_dump(mode="json")` (pydantic v2 serializa datetime UTC con "Z"); este es
            # el único que se arma a mano, fuera de un modelo.
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "role": role,
            "errors": errors,
            "payments": self._safe("payments", errors, self._payments_block),
            "operations": self._safe("operations", errors, self._operations_block),
            "me": self._safe("me", errors, self._me_block, current_user),
        }

        # Ausentes (no null) para MODERATOR: son decisiones de ROOT. Ver el docstring del
        # schema (`app/schemas/admin_overview.py`) sobre por qué esto se arma a mano en vez
        # de con un modelo pydantic de nivel superior.
        if role == UserRole.ROOT.value:
            payload["alerts"] = self._safe("alerts", errors, self._alerts_block)
            payload["clients"] = self._safe("clients", errors, self._clients_block)

        return payload

    def _safe(self, name: str, errors: list[str], fn: Callable, *args) -> Optional[dict]:
        try:
            return fn(*args)
        except Exception:
            logger.exception("admin_overview: el bloque '%s' falló", name)
            errors.append(name)
            return None

    # ── Bloques ──────────────────────────────────────────────────────────────

    def _payments_block(self) -> dict:
        """Reshape de `payments_stats("incoming")`: es la bandeja que de verdad pide atención
        (la de salientes no tiene el concepto de "monto sin asignar")."""
        stats = WhatsAppPaymentService(self.db).payments_stats("incoming")
        top_unassigned = stats["unassigned"][0] if stats["unassigned"] else None
        block = OverviewPayments(
            needs_attention=stats["needs_attention"],
            unassigned_amount=top_unassigned["amount"] if top_unassigned else 0.0,
            unassigned_currency=top_unassigned["currency"] if top_unassigned else None,
            unassigned_truncated=stats["unassigned_truncated"],
            unlinked=stats["unlinked"],
            to_review=stats["to_review"],
            partially_split=stats["partially_split"],
            received_today=stats["received_today"],
            reconciled_today=stats["reconciled_today"],
        )
        return block.model_dump(mode="json")

    def _operations_block(self) -> dict:
        stats = WhatsAppQuoteService(self.db).get_stats()
        block = OverviewOperations(
            to_settle=stats["to_settle"],
            # OJO: `stats["to_settle_amount"]` (del schema viejo, `WhatsAppStatsResponse`) es
            # lo que FALTA. Aquí el contrato pide el TOTAL del trato y lo ya cubierto aparte.
            to_settle_amount=stats["to_settle_total_amount"],
            to_settle_covered=stats["to_settle_covered_amount"],
            to_deliver=stats["to_deliver"],
            to_deliver_oldest_at=stats["to_deliver_oldest_at"],
            expiring=stats["expiring"],
            expiring_next_at=stats["expiring_next_at"],
            completed_today=stats["completed_today"],
            completed_daily_avg_week=stats["completed_daily_avg_week"],
        )
        return block.model_dump(mode="json")

    def _me_block(self, current_user: User) -> dict:
        """Gancho al mismo reporte de `GET /transactions/reports/my-profits`, acotado a hoy
        y sumando `profit_amount_usdt` (la referencia universal) en vez de `profit_amount`
        (que va en la moneda del trato y no se puede sumar entre pares distintos)."""
        start_of_day = datetime.now(CARACAS_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_of_day = start_of_day + timedelta(days=1)
        summary = TransactionRepository(self.db).get_user_profit_usdt_summary(
            current_user.id, start_of_day, end_of_day
        )
        block = OverviewMe(
            profit_today=round(summary["total_profit_usdt"], 2),
            profit_currency="USDT",
            transactions_today=summary["transaction_count"],
        )
        return block.model_dump(mode="json")

    def _alerts_block(self) -> dict:
        repo = RateAlertRepository(self.db)
        block = OverviewAlerts(
            unseen=repo.count_unacknowledged(),
            top=repo.top_unacknowledged_by_deviation(limit=3),
        )
        return block.model_dump(mode="json")

    def _clients_block(self) -> dict:
        overview = ClientPendingService(self.db).pending_overview(top_n=3)
        block = OverviewClients(**overview)
        return block.model_dump(mode="json")
