"""
Respuesta de `GET /admin/overview`: el agregado que alimenta la home del panel de admin.

Un solo viaje en vez de las cinco llamadas (dos de ellas trayendo cientos de filas para
filtrar en el navegador) que armaba la home antes de este endpoint.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class OverviewPayments(BaseModel):
    needs_attention: int
    unassigned_amount: float
    unassigned_currency: Optional[str] = None
    unassigned_truncated: bool
    unlinked: int
    to_review: int
    partially_split: int
    received_today: int
    reconciled_today: int


class OverviewOperations(BaseModel):
    to_settle: int
    to_settle_amount: float
    to_settle_covered: float
    to_deliver: int
    to_deliver_oldest_at: Optional[datetime] = None
    expiring: int
    expiring_next_at: Optional[datetime] = None
    completed_today: int
    completed_daily_avg_week: float


class OverviewMe(BaseModel):
    profit_today: float
    profit_currency: str = "USDT"
    transactions_today: int


class OverviewAlertItem(BaseModel):
    pair_symbol: Optional[str] = None
    manual_rate: Optional[float] = None
    auto_rate: Optional[float] = None
    deviation_pct: Optional[float] = None
    # Horas sin lectura reciente de la tasa automática de ese par; null = está fresca.
    stale_hours: Optional[float] = None


class OverviewAlerts(BaseModel):
    unseen: int
    top: List[OverviewAlertItem] = []


class OverviewClientTotal(BaseModel):
    currency: str
    amount: float


class OverviewClientOldest(BaseModel):
    name: Optional[str] = None
    waiting_days: Optional[int] = None
    amount: float
    currency: Optional[str] = None


class OverviewClients(BaseModel):
    pending_count: int
    totals: List[OverviewClientTotal] = []
    oldest: List[OverviewClientOldest] = []


# No hay un `AdminOverview` de nivel superior a propósito: `alerts`/`clients` tienen que
# poder faltar del todo en la respuesta de un MODERATOR (ausentes), y llegar en `null` en la
# de un ROOT cuando el bloque falla — dos formas distintas de "no hay valor" que un mismo
# campo Optional de pydantic no puede distinguir al serializar. El router arma el dict final
# a mano (incluye la clave solo si el rol la tiene) y usa estos modelos solo para tipar y
# validar cada bloque por separado antes de montarlo.
