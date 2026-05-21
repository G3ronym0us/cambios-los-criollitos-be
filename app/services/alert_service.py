from sqlalchemy.orm import Session
from typing import List
from app.repositories.rate_alert_repository import RateAlertRepository
from app.core.redis_pubsub import publish_alert


class AlertService:
    def __init__(self, db: Session):
        self.repo = RateAlertRepository(db)

    async def process_divergences(self, divergences: List[dict]) -> None:
        for data in divergences:
            alert = self.repo.create(data)
            payload = {
                "uuid": str(alert.uuid),
                "currency_pair_id": alert.currency_pair_id,
                "from_currency": alert.from_currency,
                "to_currency": alert.to_currency,
                "manual_rate": alert.manual_rate,
                "automatic_rate": alert.automatic_rate,
                "diff_percentage": alert.diff_percentage,
                "created_at": alert.created_at.isoformat() if alert.created_at else None,
            }
            await publish_alert(payload)
