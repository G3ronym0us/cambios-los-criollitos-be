"""Schemas del emparejamiento comprobante ↔ operación (app/services/operation_match_service.py)."""

from typing import Optional

from pydantic import BaseModel, Field

from app.services.operation_match_service import DEFAULT_WINDOW_HOURS, FORWARDED_WINDOW_MINUTES


# ---------- Bot: decisión binaria ----------


class OutgoingMatchRequest(BaseModel):
    """Criterios de un comprobante recién leído por OCR (aún sin guardar)."""

    #: Qué lado del trato es el comprobante: el que paga el cliente o el que le pagamos.
    table: str = Field("outgoing", pattern="^(incoming|outgoing)$")
    amount: Optional[float] = None
    currency: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    bank_to: Optional[str] = None
    window_hours: int = Field(DEFAULT_WINDOW_HOURS, ge=1, le=720)
    #: Alcance: el cliente que lo envió, o el grupo (escenario VIA_PARTNER).
    client_phone: Optional[str] = None
    group_jid: Optional[str] = None
    scenario: Optional[str] = None
    limit: int = Field(200, ge=1, le=500)


class OutgoingMatchResponse(BaseModel):
    #: UUID de la operación a vincular, o null si es ambiguo (decide el operador).
    operation_uuid: Optional[str] = None
    #: La operación entera, para que el bot no tenga que pedirla otra vez.
    operation: Optional[dict] = None


class ForwardedMatchRequest(BaseModel):
    """Criterios del Zelle que el operador reenvió a un grupo."""

    provider: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    reference: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    window_minutes: int = Field(FORWARDED_WINDOW_MINUTES, ge=1, le=1440)


class ForwardedMatchResponse(BaseModel):
    #: Id del entrante que es el mismo comprobante, o null si no se puede afirmar.
    payment_id: Optional[int] = None
    #: El pago entero, para que el bot no tenga que pedirlo otra vez.
    payment: Optional[dict] = None


# ---------- Front: ranking ----------


class OperationRankRequest(BaseModel):
    payment_id: int
    table: str = Field(..., pattern="^(incoming|outgoing)$")
    limit: int = Field(500, ge=1, le=500)


class OperationScoreResponse(BaseModel):
    uuid: str
    #: Esperado − pagado, con signo (el front lo pinta como "+43" / "-7").
    delta: Optional[float] = None
    relative: Optional[float] = None
    currency_matches: bool
    amount_score: float
    time_score: float
    score: float
    within_tolerance: bool


class SuggestionResponse(BaseModel):
    uuid: str
    confident: bool


class OperationRankResponse(BaseModel):
    suggestion: Optional[SuggestionResponse] = None
    candidates: list[OperationScoreResponse]


class PaymentSuggestionsRequest(BaseModel):
    """Sugerencia para una tanda de comprobantes: una página del listado de pagos."""

    payment_ids: list[int] = Field(..., max_length=200)
