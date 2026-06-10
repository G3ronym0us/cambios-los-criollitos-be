from sqlalchemy.orm import Session
from typing import List, Optional
from app.models.push_subscription import PushSubscription


class PushSubscriptionRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert(self, user_id: int, endpoint: str, p256dh: str, auth: str,
               user_agent: Optional[str] = None) -> PushSubscription:
        sub = self.db.query(PushSubscription).filter(
            PushSubscription.endpoint == endpoint
        ).first()
        if sub:
            sub.user_id = user_id
            sub.p256dh = p256dh
            sub.auth = auth
            sub.user_agent = user_agent
        else:
            sub = PushSubscription(
                user_id=user_id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                user_agent=user_agent,
            )
            self.db.add(sub)
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def get_all(self) -> List[PushSubscription]:
        return self.db.query(PushSubscription).all()

    def get_by_user(self, user_id: int) -> List[PushSubscription]:
        return self.db.query(PushSubscription).filter(
            PushSubscription.user_id == user_id
        ).all()

    def delete_by_endpoint(self, endpoint: str) -> bool:
        deleted = self.db.query(PushSubscription).filter(
            PushSubscription.endpoint == endpoint
        ).delete()
        self.db.commit()
        return deleted > 0
