from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, func
from typing import List, Optional
from uuid import UUID
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.models.rate_alert import RateAlert


class RateAlertRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: dict) -> RateAlert:
        alert = RateAlert(**data)
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        return alert

    def get_recent(self, limit: int = 50, only_unacknowledged: bool = False) -> List[RateAlert]:
        query = self.db.query(RateAlert)
        if only_unacknowledged:
            query = query.filter(RateAlert.is_acknowledged == False)
        return query.order_by(desc(RateAlert.created_at)).limit(limit).all()

    def count_unacknowledged(self) -> int:
        """El total real de divergencias no vistas — no el recorte de `get_recent`."""
        return self.db.query(func.count(RateAlert.id)).filter(
            RateAlert.is_acknowledged.is_(False)
        ).scalar() or 0

    def top_unacknowledged_by_deviation(self, limit: int = 3) -> list[dict]:
        """
        Las divergencias no vistas más grandes, con el símbolo del par y —si la tasa
        automática de ese par no se refrescó hace rato— cuántas horas lleva vieja.

        Una sola query: el par y la tasa activa (`is_active=True`, la que quedó viva tras el
        último scrape) se traen con LEFT JOIN en vez de resolverse fila por fila.
        """
        rows = (
            self.db.query(
                RateAlert.diff_percentage,
                RateAlert.manual_rate,
                RateAlert.automatic_rate,
                CurrencyPair.pair_symbol,
                ExchangeRate.created_at,
            )
            .join(CurrencyPair, CurrencyPair.id == RateAlert.currency_pair_id)
            .outerjoin(
                ExchangeRate,
                and_(
                    ExchangeRate.currency_pair_id == RateAlert.currency_pair_id,
                    ExchangeRate.is_active.is_(True),
                ),
            )
            .filter(RateAlert.is_acknowledged.is_(False))
            .order_by(desc(RateAlert.diff_percentage))
            .limit(limit)
            .all()
        )

        now = datetime.now(timezone.utc)
        out = []
        for diff_pct, manual_rate, auto_rate, pair_symbol, rate_created_at in rows:
            stale_hours = None
            if rate_created_at is not None:
                created = rate_created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                hours = (now - created).total_seconds() / 3600
                # El scraper corre cada hora; dos ciclos sin refrescar ya es "vieja", no ruido.
                if hours >= 2:
                    stale_hours = round(hours, 1)
            out.append({
                "pair_symbol": pair_symbol,
                "manual_rate": manual_rate,
                "auto_rate": auto_rate,
                "deviation_pct": round(diff_pct, 2) if diff_pct is not None else None,
                "stale_hours": stale_hours,
            })
        return out

    def acknowledge(self, alert_uuid: UUID) -> Optional[RateAlert]:
        alert = self.db.query(RateAlert).filter(RateAlert.uuid == alert_uuid).first()
        if not alert:
            return None
        alert.is_acknowledged = True
        self.db.commit()
        self.db.refresh(alert)
        return alert
