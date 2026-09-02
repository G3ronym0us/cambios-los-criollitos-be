"""Schemas del emparejamiento comprobante ↔ operación (app/services/operation_match_service.py)."""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.whatsapp import WhatsAppOperationResponse
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
    """Criterios del comprobante que el operador reenvió a un grupo."""

    provider: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    reference: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    #: Texto del OCR: fuera de Zelle es lo que prueba que es el MISMO comprobante.
    raw_text: Optional[str] = None
    window_minutes: int = Field(FORWARDED_WINDOW_MINUTES, ge=1, le=1440)


class ForwardedMatchResponse(BaseModel):
    #: Id del entrante que es el mismo comprobante, o null si no se puede afirmar.
    payment_id: Optional[int] = None
    #: El pago entero, para que el bot no tenga que pedirlo otra vez.
    payment: Optional[dict] = None


# ---------- Front: ranking ----------


class OperationRankRequest(BaseModel):
    """
    El cajón de "vincular pago" en UNA sola petición: los mismos filtros de `GET /operations`
    (`phone`, `search`, `status`, `page`, `limit`) más `order_by`, que reemplaza a los tres
    botones que hoy ordenan en el navegador ("sugerida" / "monto" / "hora" en
    `LinkOperationPanel.tsx`). Antes el front pedía el listado aparte y cruzaba por uuid con
    lo que devolvía este endpoint; ahora la respuesta ya trae la operación completa.
    """

    payment_id: int
    table: str = Field(..., pattern="^(incoming|outgoing)$")
    #: Igual que `GET /operations`: sin `phone` ni `search` el alcance es todo el sistema
    #: (recortado por `MATCH_POOL_LIMIT`, ver operation_match_service.py).
    phone: Optional[str] = None
    search: Optional[str] = None
    status: Optional[str] = None
    #: suggested (score combinado, con la sugerida al frente) | amount (cercanía al monto,
    #: `score.relative`) | time (fecha descendente). "suggested" es lo que pintaba el front
    #: por defecto.
    order_by: str = Field("suggested", pattern="^(suggested|amount|time)$")
    page: int = Field(1, ge=1)
    limit: int = Field(200, ge=1, le=500)


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


class OperationMatchItem(BaseModel):
    """Una candidata lista para pintar: la operación entera junto a su puntaje contra este
    comprobante — ya no hace falta pedir `GET /operations` aparte y cruzar por `uuid`."""

    operation: WhatsAppOperationResponse
    score: OperationScoreResponse


class OperationRankResponse(BaseModel):
    suggestion: Optional[SuggestionResponse] = None
    items: list[OperationMatchItem]
    #: Total tras el filtro (no el tamaño de la página) — para el pie del cajón, igual que en
    #: `WhatsAppOperationList`.
    total: int
    page: int
    limit: int


class PaymentSuggestionsRequest(BaseModel):
    """Sugerencia para una tanda de comprobantes: una página del listado de pagos."""

    payment_ids: list[int] = Field(..., max_length=200)
