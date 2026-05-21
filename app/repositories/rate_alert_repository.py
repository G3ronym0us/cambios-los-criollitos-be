from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from uuid import UUID
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

    def acknowledge(self, alert_uuid: UUID) -> Optional[RateAlert]:
        alert = self.db.query(RateAlert).filter(RateAlert.uuid == alert_uuid).first()
        if not alert:
            return None
        alert.is_acknowledged = True
        self.db.commit()
        self.db.refresh(alert)
        return alert
