from pydantic import BaseModel
from datetime import datetime
from uuid import UUID
from typing import List


class RateAlertOut(BaseModel):
    uuid: UUID
    currency_pair_id: int
    from_currency: str
    to_currency: str
    manual_rate: float
    automatic_rate: float
    diff_percentage: float
    is_acknowledged: bool
    created_at: datetime

    class Config:
        from_attributes = True


class RateAlertList(BaseModel):
    alerts: List[RateAlertOut]
    total: int


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: PushKeys


class PushUnsubscribeIn(BaseModel):
    endpoint: str


class PushPublicKeyOut(BaseModel):
    public_key: str
