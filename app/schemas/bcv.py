from datetime import datetime
from typing import Literal

from pydantic import BaseModel


BcvCurrency = Literal["USD", "EUR"]


class BcvRateResponse(BaseModel):
    """Tasa oficial del BCV que regía en el instante consultado (o la última)."""

    currency: BcvCurrency = "USD"
    rate: float
    source: str
    fetched_at: datetime

    class Config:
        from_attributes = True
