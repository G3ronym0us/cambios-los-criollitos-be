"""Schemas del emparejamiento comprobante ↔ operación (app/services/operation_match_service.py)."""

from typing import Literal, Optional

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
    #: client (default) = solo las operaciones del cliente del comprobante (y sus alias de
    #: socio), abiertas. all = el botón «buscar en todos los clientes» del cajón, a sabiendas.
    #: Deviation del plan: el endpoint recibe TODO por body (`OperationRankRequest`), no hay
    #: otros query params — así que `scope` va aquí como un campo más, no como `Query(...)`.
    scope: Literal["client", "all"] = "client"


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
    #: Solo del lado entrante: "CLOSES" | "PARTIAL". `None` en salientes.
    coverage: Optional[str] = None


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
    #: **En desuso.** Las puntuaciones sueltas que devolvía este endpoint antes de traer la
    #: operación entera dentro de `items`. Se mantiene poblado sólo para no romper el front
    #: que ya está en producción, que hace `match?.candidates ?? []` y, sin esto, se quedaría
    #: sin el sello «SUGERIDA» y sin el orden por monto — en silencio, porque la lista vacía
    #: no da error. Se borra cuando el front nuevo esté desplegado en todas partes.
    candidates: list[OperationScoreResponse] = Field(default_factory=list)


class PaymentSuggestionsRequest(BaseModel):
    """Sugerencia para una tanda de comprobantes: una página del listado de pagos."""

    payment_ids: list[int] = Field(..., max_length=200)


class CreateHint(BaseModel):
    """Con qué par nacería la operación de este comprobante, y cuánto daría."""

    pair_uuid: Optional[str] = None
    pair_symbol: Optional[str] = None
    #: preferred | most_used | currency — por qué se eligió ese par. El front lo redacta.
    reason: Optional[str] = None
    rate: Optional[float] = None
    rate_at: Optional[str] = None
    from_amount: Optional[float] = None
    to_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_currency: Optional[str] = None


class PaymentSuggestionItem(BaseModel):
    payment_id: int
    #: LINK = hay una operación que la cubre. CREATE = el cliente no tiene ninguna abierta.
    kind: Literal["LINK", "CREATE"]
    operation_uuid: Optional[str] = None
    confident: bool = False
    #: CLOSES = deja la op sin faltante. PARTIAL = abona y queda resto. Solo del lado entrante.
    coverage: Optional[Literal["CLOSES", "PARTIAL"]] = None
    client_name: Optional[str] = None
    client_uuid: Optional[str] = None
    #: ¿La op es del mismo cliente que el chat del comprobante? En ámbar cuando es False.
    same_client: bool = True
    operation_created_at: Optional[str] = None
    #: Firmado: negativo significa que la operación nació DESPUÉS del comprobante.
    hours_apart: Optional[float] = None
    status: Optional[str] = None
    expired: bool = False
    score: Optional[float] = None
    delta: Optional[float] = None
    from_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_amount: Optional[float] = None
    to_currency: Optional[str] = None
    missing_before: Optional[float] = None
    missing_after: Optional[float] = None
    create_hint: Optional[CreateHint] = None


class PaymentSuggestionsResponse(BaseModel):
    items: list[PaymentSuggestionItem]
