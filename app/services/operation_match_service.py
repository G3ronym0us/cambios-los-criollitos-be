"""
Emparejamiento de comprobantes con operaciones — la ÚNICA implementación.

Antes vivía duplicada: el bot tenía `selectOperationForOutgoing` / `selectForwardedIncoming`
(whatsapp-bot/src/operations.ts) y el front una copia de los mismos umbrales para ordenar el
selector de "vincular pago". Dos copias de la misma regla de negocio se separan solas; aquí
quedan los umbrales y ambos consumidores los leen de este módulo.

Dos políticas sobre las MISMAS primitivas, porque los consumidores deciden cosas distintas:

  * `pick_auto_match` — la del bot, que vincula solo y sin supervisión. Conservadora: exige
    ±1% sobre `to_amount`, ventana de 24 h, que la op no tenga ya un saliente, y ante varias
    candidatas desambigua por tokens del comprobante presentes en `notes`; si sigue ambiguo
    NO vincula y deja que el operador decida. Es la semántica histórica del bot, byte por byte.

  * `rank_candidates` + `pick_suggestion` — la del front, donde el operador ve la lista y
    confirma. Puntúa monto y cercanía en el tiempo para ordenar, y solo marca "sugerida" con
    confianza cuando gana con claridad. Aquí sí se prorratea el pendiente de una op
    parcialmente cubierta, porque el comprobante que falta es del tamaño del resto.

La diferencia entre ambas es deliberada y está probada en tests/test_operation_match.py:
el ranking puede sugerir donde el bot se abstiene, nunca al revés.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationScenario
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment

# ---------------------------------------------------------------------------
# Umbrales. Cambiar aquí cambia bot y front a la vez — que es el punto.
# ---------------------------------------------------------------------------

#: Dentro de este margen relativo el monto se considera "el mismo".
AMOUNT_TOLERANCE = 0.01
#: Más allá de esto el monto ya no compite por la sugerencia.
AMOUNT_CUTOFF = 0.02
#: Horas a las que la cercanía temporal vale la mitad. Era 6 h, que con el p90 real en 32 min
#: dejaba a una candidata de hace 5 horas puntuando 0,55 y empatándole a la correcta.
TIME_HALF_LIFE_HOURS = 1.0
#: El monto manda; la hora solo desempata.
AMOUNT_WEIGHT = 0.75
#: Ventaja mínima sobre la segunda candidata para dar la sugerencia por inequívoca.
SUGGESTION_MARGIN = 0.05
#: Ventana por defecto del matcher automático de salientes.
DEFAULT_WINDOW_HOURS = 24
#: Mínimo de caracteres para que un dato del comprobante sirva de token desambiguador.
MIN_TOKEN_LENGTH = 4
#: Estados en los que una operación sigue admitiendo el comprobante del cliente.
OPEN_STATUSES = ("QUOTED", "PENDING")

#: Ventana dura del ranking del PANEL del lado entrante. El ranking no tenía ninguna: el
#: tiempo puntuaba pero nunca descartaba, así que una op de hace tres meses con el monto
#: exacto podía ganar. Medido en producción (98 vínculos): el p90 del desfase op→comprobante
#: es 32 min y el p99 es 23,8 h, así que 72 h es holgado a propósito — cubre al cliente que
#: paga el lunes lo que se cotizó el viernes. No lo lee el bot, que tiene su propia
#: `DEFAULT_WINDOW_HOURS`.
INCOMING_WINDOW_HOURS = 72

#: El comprobante reenviado es el MISMO, no uno parecido: tolerancia mucho más dura.
FORWARDED_TOLERANCE = 0.001
#: El reenvío al grupo es un asiento contable, y el operador lo hace cuando puede — no en la
#: hora siguiente. Con 60 min el Zelle de $25 del 2026-08-24 23:39 reenviado a las 00:56
#: (77 min) no calzaba y quedaba un saliente fantasma. Ampliar es barato: cuando dos
#: candidatas caen dentro de la ventana, el desempate por cédula/teléfono devuelve `None`
#: antes que adivinar, así que el peor caso sigue siendo "no calza", nunca "calza mal".
FORWARDED_WINDOW_MINUTES = 6 * 60

#: Candidatas que puntúa una página de `POST /operations/match`. Es el mismo tope (500) que
#: ya usaba `rank_for_payment` cuando cargaba SIEMPRE las últimas operaciones de todo el
#: sistema; la diferencia es que ahora se aplica DESPUÉS del filtro (`phone`/`search`/
#: `status`), no antes. Sin filtro (alcance "Ver todas") sigue siendo un recorte real de las
#: más recientes — el mismo que ya asumía el front al pedir un lote global—; con `phone` casi
#: nunca muerde: acota al historial de UN cliente, que en producción no ha pasado de unos
#: pocos cientos de operaciones.
MATCH_POOL_LIMIT = 500


def receipt_fingerprint(text: Optional[str]) -> str:
    """El texto del OCR sin espacios de más ni mayúsculas: dos lecturas de la MISMA captura."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Normaliza a UTC consciente: en la BD conviven timestamps con y sin tzinfo."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Candidatas y criterios (planos, para que los tests no necesiten BD)
# ---------------------------------------------------------------------------


@dataclass
class OperationCandidate:
    uuid: str
    to_amount: float
    to_currency: Optional[str]
    from_amount: float
    from_currency: Optional[str]
    created_at: Optional[datetime]
    notes: Optional[str] = None
    status: Optional[str] = None
    has_outgoing_payment: bool = False
    has_incoming_payment: bool = False
    # Valor del trato y cuánto cubren ya sus salientes, para prorratear el pendiente.
    value_amount: Optional[float] = None
    delivered_amount: float = 0.0
    pending_amount: Optional[float] = None
    # Lo que el cliente ya pagó de su lado (Σ whatsapp_payment_allocations) y lo que le
    # falta. El lado saliente tiene su propio par (`delivered_amount`/`pending_amount`):
    # son cosas distintas y no se mezclan.
    collected_incoming: float = 0.0
    missing_incoming: float = 0.0

    def __post_init__(self) -> None:
        # `from_model` ya lo calcula; esto cubre la construcción directa (tests y el helper
        # `op()`), donde solo se pasa `collected_incoming`.
        if not self.missing_incoming:
            self.missing_incoming = round((self.from_amount or 0.0) - self.collected_incoming, 2)

    @classmethod
    def from_model(
        cls,
        op: WhatsAppOperation,
        *,
        has_outgoing_payment: bool = False,
        has_incoming_payment: bool = False,
        collected_incoming: float = 0.0,
    ) -> "OperationCandidate":
        value = op.amount if op.amount is not None else op.from_amount
        delivered = op.delivered_amount
        cp = op.currency_pair
        return cls(
            uuid=str(op.uuid),
            to_amount=op.to_amount or 0.0,
            to_currency=cp.to_currency.symbol if cp and cp.to_currency else None,
            from_amount=op.from_amount or 0.0,
            from_currency=cp.from_currency.symbol if cp and cp.from_currency else None,
            created_at=_aware(op.created_at),
            notes=op.notes,
            status=op.status.value if op.status else None,
            has_outgoing_payment=has_outgoing_payment,
            has_incoming_payment=has_incoming_payment,
            value_amount=value,
            delivered_amount=delivered,
            pending_amount=round((value or 0) - delivered, 2),
            collected_incoming=collected_incoming,
            missing_incoming=round((op.from_amount or 0.0) - collected_incoming, 2),
        )


@dataclass
class OutgoingCriteria:
    """Lo que el comprobante saliente sabe de sí mismo."""

    amount: Optional[float]
    currency: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    bank_to: Optional[str] = None
    window_hours: int = DEFAULT_WINDOW_HOURS
    #: Momento del comprobante; si falta, se usa `now` al puntuar.
    created_at: Optional[datetime] = None

    def tokens(self) -> list[str]:
        """
        Lo que identifica a UNA PERSONA en el comprobante: su cédula y su teléfono.

        El banco quedó fuera a propósito. Identifica a un banco entre veinte, y en Venezuela
        el 0102 es el más común de todos: cuando dos tratos del mismo monto van al mismo
        banco, ese token los empata a los dos y anula lo que la cédula ya había resuelto sola
        (pago 5079 contra las ops 3968 y 3975, 2026-08-27). Es la misma lista que usa
        `ForwardedCriteria`.
        """
        return [
            t for t in (self.identification, self.phone_to) if t and len(t) >= MIN_TOKEN_LENGTH
        ]


@dataclass
class IncomingCandidate:
    """Un entrante ya registrado, candidato a ser el comprobante que se reenvió al grupo."""

    id: int
    amount: Optional[float]
    currency: Optional[str]
    created_at: Optional[datetime]
    provider: Optional[str] = None
    reference: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    raw_text: Optional[str] = None

    @classmethod
    def from_model(cls, p: WhatsAppIncomingPayment) -> "IncomingCandidate":
        return cls(
            id=p.id,
            amount=p.amount,
            currency=p.currency,
            created_at=_aware(p.created_at),
            provider=p.provider,
            reference=p.reference,
            identification=p.identification,
            phone_to=p.phone_to,
            raw_text=p.raw_text,
        )


@dataclass
class ForwardedCriteria:
    provider: Optional[str]
    amount: Optional[float]
    currency: Optional[str]
    reference: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    #: Texto del OCR del comprobante reenviado: la prueba de que es la MISMA imagen.
    raw_text: Optional[str] = None
    window_minutes: int = FORWARDED_WINDOW_MINUTES

    def tokens(self) -> list[str]:
        return [
            t for t in (self.identification, self.phone_to) if t and len(t) >= MIN_TOKEN_LENGTH
        ]


@dataclass
class MatchScore:
    uuid: str
    delta: Optional[float]
    relative: Optional[float]
    currency_matches: bool
    amount_score: float
    time_score: float
    score: float
    within_tolerance: bool
    #: Solo del lado entrante: "CLOSES" | "PARTIAL". `None` en salientes, que no tienen clases.
    coverage: Optional[str] = None


@dataclass
class Suggestion:
    uuid: str
    #: Gana con claridad: el front puede preseleccionarla sin arriesgar un vínculo por inercia.
    confident: bool


@dataclass
class MatchPage:
    """
    Una página de `OperationMatchService.rank_for_payment`: candidatas YA filtradas, puntuadas
    y ordenadas — lo que antes armaba el navegador cruzando dos peticiones (`GET /operations`
    + `POST /operations/match`) y ordenando/recortando él mismo (`sortScored` /
    `buildOperationQuery` en `LinkOperationPanel.tsx`).

    `items` trae la operación real junto a su puntaje, no solo el uuid: así el router arma la
    respuesta completa (lo que hoy pinta cada tarjeta del cajón) sin una segunda consulta.
    """

    items: list[tuple[WhatsAppOperation, MatchScore]]
    suggestion: Optional[Suggestion]
    #: Total tras el filtro — NO el tamaño de `MATCH_POOL_LIMIT` ni el de la página — para que
    #: el pie del cajón pueda decir "26–50 de 312" igual que ya hace `GET /operations`.
    total: int
    page: int
    limit: int


# ---------------------------------------------------------------------------
# Primitivas puras
# ---------------------------------------------------------------------------


def expected_amount(
    cand: OperationCandidate, table: str
) -> tuple[Optional[float], Optional[str]]:
    """
    Qué monto de la operación le toca comparar a este comprobante:
      outgoing → `to_amount` (lo que le pagamos al cliente)
      incoming → `from_amount` (lo que el cliente entrega)
    Si la op ya está parcialmente cubierta se prorratea el pendiente sobre ese lado.
    """
    if table == "incoming":
        # Lo que le toca comparar no es el lado `from` entero sino lo que queda por cobrar:
        # un trato de 500 al que ya le entraron 400 lo cierra un comprobante de 100.
        return cand.missing_incoming, cand.from_currency
    delivered = cand.delivered_amount or 0
    pending = cand.pending_amount or 0
    value = cand.value_amount or cand.from_amount or 0
    if delivered > 0.01 and pending > 0.01 and value > 0 and cand.to_amount > 0:
        return cand.to_amount * (pending / value), cand.to_currency
    return cand.to_amount, cand.to_currency


def incoming_coverage(missing: Optional[float], paid: Optional[float]) -> Optional[str]:
    """
    Qué le hace este comprobante a lo que la operación todavía tiene por cobrar.

    `CLOSES` la deja sin faltante; `PARTIAL` cubre una parte y deja resto. `None` significa
    que no es candidata: o no falta nada, o el comprobante se pasa de lo que falta — y eso
    último es saldo a favor del cliente, una decisión aparte que nadie debe tomar por inercia
    desde una sugerencia.
    """
    if not paid or paid <= 0 or missing is None or missing <= 0:
        return None
    if abs(missing - paid) <= paid * AMOUNT_TOLERANCE:
        return "CLOSES"
    if missing > paid:
        return "PARTIAL"
    return None


def _time_score(cand: OperationCandidate, reference: datetime) -> float:
    """1 si la op nació junto al comprobante, 0,5 a 6 h, ~0,2 a 24 h. Decae suave."""
    created = cand.created_at
    if created is None:
        return 0.0
    hours = abs((reference - created).total_seconds()) / 3600.0
    return 1.0 / (1.0 + hours / TIME_HALF_LIFE_HOURS)


def score_candidate(
    cand: OperationCandidate,
    criteria: OutgoingCriteria,
    table: str,
    now: datetime,
) -> MatchScore:
    reference = criteria.created_at or now
    time_score = _time_score(cand, reference)
    paid = criteria.amount
    exp_amount, exp_currency = expected_amount(cand, table)

    if not paid or paid <= 0 or exp_amount is None:
        return MatchScore(cand.uuid, None, None, False, 0.0, time_score, 0.0, False)

    # Si a alguno de los dos lados le falta la moneda no se castiga: el OCR no siempre la saca.
    currency_matches = (
        not criteria.currency or not exp_currency or exp_currency == criteria.currency
    )
    delta = exp_amount - paid
    relative = abs(delta) / paid
    if not currency_matches:
        return MatchScore(cand.uuid, delta, relative, False, 0.0, time_score, 0.0, False)

    if table == "incoming":
        coverage = incoming_coverage(exp_amount, paid)
        if coverage is None:
            return MatchScore(cand.uuid, None, None, currency_matches, 0.0, time_score, 0.0, False)
        if cand.created_at is None:
            return MatchScore(cand.uuid, None, None, currency_matches, 0.0, 0.0, 0.0, False)
        # Fuera de la ventana no compite, aunque el monto cuadre al céntimo.
        if abs((reference - cand.created_at).total_seconds()) > INCOMING_WINDOW_HOURS * 3600:
            return MatchScore(cand.uuid, None, None, currency_matches, 0.0, time_score, 0.0, False)
        if coverage == "CLOSES":
            amount_score = max(0.0, 1.0 - relative / AMOUNT_CUTOFF)
            score = AMOUNT_WEIGHT * amount_score + (1 - AMOUNT_WEIGHT) * time_score
        else:
            # En un abono el monto no dice nada (cualquier cifra que quepa es igual de
            # válida), así que lo único que ordena es el tiempo. Las dos escalas nunca se
            # comparan entre sí: `rank_candidates` ordena por clase ANTES que por puntaje.
            amount_score = 0.0
            score = time_score
        return MatchScore(
            uuid=cand.uuid,
            delta=delta,
            relative=relative,
            currency_matches=True,
            amount_score=amount_score,
            time_score=time_score,
            score=score,
            within_tolerance=coverage == "CLOSES",
            coverage=coverage,
        )

    amount_score = max(0.0, 1.0 - relative / AMOUNT_CUTOFF)
    score = (
        AMOUNT_WEIGHT * amount_score + (1 - AMOUNT_WEIGHT) * time_score
        if amount_score > 0
        else 0.0
    )
    return MatchScore(
        uuid=cand.uuid,
        delta=delta,
        relative=relative,
        currency_matches=True,
        amount_score=amount_score,
        time_score=time_score,
        score=score,
        within_tolerance=relative <= AMOUNT_TOLERANCE,
    )


#: Orden de las clases de cobertura del lado entrante: un cierre siempre gana a un abono, y
#: lo que no es candidata (`None`) se hunde al fondo. Ese fondo importa porque el cajón
#: enseña la lista ENTERA, no solo la sugerencia: sin esto una op que este comprobante no
#: puede cubrir salía por delante de otra a la que sí le abona.
_COVERAGE_RANK = {"CLOSES": 0, "PARTIAL": 1}


def _coverage_rank(score: MatchScore, table: str) -> int:
    """El saliente no tiene clases: todas empatan y ordena el puntaje, como siempre."""
    if table != "incoming":
        return 0
    return _COVERAGE_RANK.get(score.coverage, 2)


def rank_candidates(
    candidates: Iterable[OperationCandidate],
    criteria: OutgoingCriteria,
    table: str,
    now: datetime,
) -> list[MatchScore]:
    """
    Puntúa todas y devuelve de mejor a peor.

    Del lado ENTRANTE la clase manda sobre el puntaje: primero todo lo que CIERRA, después
    todo lo que ABONA, y al final lo que este comprobante no puede cubrir. Un cierre exacto
    es una afirmación mucho más fuerte que una coincidencia horaria. Del lado saliente
    `coverage` es siempre None y el orden queda exactamente como estaba. Desempate, en
    ambos: la más reciente primero.
    """
    scores: list[tuple[MatchScore, Optional[datetime]]] = [
        (score_candidate(cand, criteria, table, now), cand.created_at) for cand in candidates
    ]
    scores.sort(
        key=lambda pair: (
            _coverage_rank(pair[0], table),
            -pair[0].score,
            -(pair[1].timestamp() if pair[1] else 0),
        )
    )
    return [s for s, _ in scores]


def pick_suggestion(scored: Sequence[MatchScore]) -> Optional[Suggestion]:
    """
    Política del FRONT: la mejor candidata, marcada como inequívoca solo si ninguna otra de
    SU MISMA CLASE queda igual de cerca. En la duda el operador elige a mano.
    """
    closing = [s for s in scored if s.coverage == "CLOSES"]
    partial = [s for s in scored if s.coverage == "PARTIAL"]
    # Salientes: no hay clases, se mantiene el criterio de siempre.
    plain = [s for s in scored if s.coverage is None and s.within_tolerance]

    eligible = sorted(closing or plain or partial, key=lambda s: s.score, reverse=True)
    if not eligible:
        return None
    best = eligible[0]
    second = eligible[1] if len(eligible) > 1 else None
    confident = second is None or (best.score - second.score) >= SUGGESTION_MARGIN
    return Suggestion(uuid=best.uuid, confident=confident)


def pick_auto_match(
    candidates: Iterable[OperationCandidate],
    criteria: OutgoingCriteria,
    now: datetime,
    table: str = "outgoing",
) -> Optional[str]:
    """
    Política del BOT: vincula solo cuando no hay duda posible, porque nadie supervisa.

    Del lado SALIENTE es la semántica histórica de `selectOperationForOutgoing`, preservada
    sin cambios: ±1% sobre `to_amount` (sin prorratear), misma moneda, dentro de la ventana,
    sin saliente previo — y si quedan varias, desambigua por tokens del comprobante dentro de
    `notes`. Ambiguo ⇒ None: el comprobante queda suelto y lo vincula el operador.

    Del lado ENTRANTE la regla es el espejo (`from_amount`, `from_currency`, sin entrante
    previo) más una condición extra: la operación tiene que seguir ABIERTA. Antes el bot no
    comparaba nada — colgaba el comprobante de la op abierta más reciente del cliente, así
    que un recibo de 15 acababa en una op de 120 que ya tenía el suyo.
    """
    if criteria.amount is None or criteria.amount <= 0:
        return None

    incoming = table == "incoming"
    since = now - timedelta(hours=criteria.window_hours or DEFAULT_WINDOW_HOURS)
    lo = criteria.amount * (1 - AMOUNT_TOLERANCE)
    hi = criteria.amount * (1 + AMOUNT_TOLERANCE)

    def eligible(c: OperationCandidate) -> bool:
        amount = c.from_amount if incoming else c.to_amount
        currency = c.from_currency if incoming else c.to_currency
        # Una op con su comprobante de ese lado no puede absorber otro: dos pagos del mismo
        # monto son dos operaciones, no una duplicada.
        taken = c.has_incoming_payment if incoming else c.has_outgoing_payment
        if not (lo <= amount <= hi):
            return False
        if criteria.currency and currency != criteria.currency:
            return False
        if c.created_at is None or c.created_at < since:
            return False
        if taken:
            return False
        # Un comprobante del cliente solo puede entrar en un trato todavía abierto; del lado
        # saliente no se filtra por estado (se paga también contra ops ya completadas).
        if incoming and c.status not in OPEN_STATUSES:
            return False
        return True

    matches = [c for c in candidates if eligible(c)]
    matches.sort(key=lambda c: c.created_at, reverse=True)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].uuid

    tokens = criteria.tokens()
    if not tokens:
        return None
    # Gana la MEJOR coincidencia, no "la única que coincide en algo". Con `any` bastaba que
    # un dato suelto apareciera para que una candidata sobreviviera, así que la que calzaba
    # en todo empataba con la que calzaba de casualidad. Un empate arriba sigue siendo
    # ambiguo y se devuelve None: nadie supervisa al bot y vincular mal corrompe la data.
    puntuadas = [(sum(1 for t in tokens if t in (c.notes or "")), c) for c in matches]
    mejor = max(p for p, _ in puntuadas)
    if mejor == 0:
        return None
    top = [c for p, c in puntuadas if p == mejor]
    return top[0].uuid if len(top) == 1 else None


#: Cuánto se puede apartar del objetivo un conjunto para seguir contando como "cuadra".
COMBINATION_TOLERANCE = 0.01
#: Tope de candidatos que entran a la búsqueda. 2^18 recorre en un parpadeo; más arriba no
#: aporta nada — un trato pagado con más de 18 comprobantes no lo resuelve una sugerencia.
COMBINATION_MAX_CANDIDATES = 18


def suggest_combination(
    candidates: Sequence[dict], pending: float, rate: Optional[float]
) -> list[int]:
    """
    El subconjunto de comprobantes cuya suma cuadra con lo que le falta a la operación.

    Es exactamente lo que el operador hace de cabeza cuando un trato se pagó en partes: probar
    cuáles suman. En el caso 3898 son 6.277 + 250.000 + 65.723 = 322.000, que es 350 a 920.

    Devuelve el conjunto MÁS PEQUEÑO que cuadra —entre dos que cuadran, el de menos piezas es
    casi siempre el bueno— y `[]` cuando ninguno lo hace: sin propuesta es mejor que con una
    propuesta a medias, que el operador tendría que deshacer.
    """
    if not candidates or not rate or rate <= 0 or pending <= 0:
        return []
    objetivo = pending * rate
    libres = [c for c in candidates if (c.get("free_amount") or 0) > 0][:COMBINATION_MAX_CANDIDATES]
    if not libres:
        return []

    margen = max(objetivo * COMBINATION_TOLERANCE, 0.01)
    mejor: Optional[list[int]] = None
    for mascara in range(1, 1 << len(libres)):
        suma = 0.0
        elegidos: list[int] = []
        for i, c in enumerate(libres):
            if mascara >> i & 1:
                suma += c["free_amount"]
                elegidos.append(c["payment_id"])
        if abs(suma - objetivo) <= margen and (mejor is None or len(elegidos) < len(mejor)):
            mejor = elegidos
    return mejor or []


def pick_forwarded_incoming(
    candidates: Iterable[IncomingCandidate],
    used_source_ids: set[int],
    criteria: ForwardedCriteria,
    now: datetime,
) -> Optional[int]:
    """
    El entrante que se reenvió a un grupo: es el MISMO comprobante saliendo como asiento
    contable, así que la tolerancia es ±0,1% y la ventana de 60 min. Un comprobante no puede
    reenviarse a dos grupos, de ahí `used_source_ids`.

    Vale para CUALQUIER moneda, no solo Zelle (caso BRL→VES, 2026-08-06: el entrante en reales
    reenviado al grupo se guardaba como un saliente fantasma además del pago real en Bs).

    Zelle se queda con la semántica histórica —monto, proveedor y su confirmación única
    bastan—. Para el resto de monedas eso no alcanza: dos pagos móviles del mismo monto en una
    hora son cosa de todos los días, y confundirlos borraría un saliente REAL. Por eso se exige
    una prueba de que es el mismo comprobante: la referencia bancaria, o la huella del texto
    del OCR (reenviar es mandar la misma imagen, así que el texto sale idéntico).

    **La referencia sólo decide cuando la traen LOS DOS.** Un lado sin referencia no es un lado
    distinto, es un lado que no la pudo leer: el cliente manda la pantalla en español (sin
    código a la vista) y el operador reenvía la de su banco, que sí trae "Confirmation:". Con
    la regla de igualdad estricta ese par se rechazaba por un dato que sólo uno tenía.
    """
    if criteria.amount is None or criteria.amount <= 0 or not criteria.currency:
        return None

    fingerprint = receipt_fingerprint(criteria.raw_text)

    def same_receipt(c: IncomingCandidate) -> bool:
        """La prueba de que la candidata es el MISMO comprobante, no uno parecido."""
        # Dos referencias distintas son dos transferencias distintas, siempre.
        if criteria.reference and c.reference and c.reference != criteria.reference:
            return False
        # Zelle: monto + proveedor + ventana bastan (semántica histórica).
        if criteria.currency == "ZELLE":
            return True
        if criteria.reference and c.reference:
            return True
        # Sin referencia en ambos lados, la prueba es la huella del OCR.
        return bool(fingerprint) and receipt_fingerprint(c.raw_text) == fingerprint

    #: Atajo: fuera de Zelle, sin huella y sin referencia propia no hay prueba posible.
    if criteria.currency != "ZELLE" and not fingerprint and not criteria.reference:
        return None

    since = now - timedelta(minutes=criteria.window_minutes or FORWARDED_WINDOW_MINUTES)
    lo = criteria.amount * (1 - FORWARDED_TOLERANCE)
    hi = criteria.amount * (1 + FORWARDED_TOLERANCE)

    matches = [
        c
        for c in candidates
        if c.currency == criteria.currency
        and c.amount is not None
        and lo <= c.amount <= hi
        and c.created_at is not None
        and c.created_at >= since
        and c.id not in used_source_ids
        and (not criteria.provider or (c.provider or "").lower() == criteria.provider.lower())
        and same_receipt(c)
    ]
    matches.sort(key=lambda c: c.created_at, reverse=True)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].id

    tokens = criteria.tokens()
    if not tokens:
        return None
    refined = [
        c
        for c in matches
        if any(
            (c.identification and t in c.identification) or (c.phone_to and t in c.phone_to)
            for t in tokens
        )
    ]
    return refined[0].id if len(refined) == 1 else None


# ---------------------------------------------------------------------------
# Capa con BD: carga de candidatas
# ---------------------------------------------------------------------------


class OperationMatchService:
    """Carga candidatas de la BD y aplica las políticas de arriba."""

    def __init__(self, db: Session):
        self.db = db

    def _client_phones_for_many(self, phones: Sequence[str]) -> dict[str, list[str]]:
        """
        `_client_phones_for` para un lote, en UNA consulta.

        Llamarla en bucle sobre una página de 50 comprobantes son 50 viajes a la base para
        resolver lo mismo: qué teléfonos son de un socio y por tanto alcanzan sus operaciones
        anónimas (`anon:partner:{user_id}`, ver `_apply_scenario` en whatsapp_quote_service).
        """
        from app.models.fund import FundGroupMember

        unicos = [p for p in dict.fromkeys(phones) if p]
        if not unicos:
            return {}
        socios: dict[str, list[int]] = {}
        for phone, user_id in (
            self.db.query(FundGroupMember.whatsapp_phone, FundGroupMember.user_id)
            .filter(FundGroupMember.whatsapp_phone.in_(unicos))
            .distinct()
            .all()
        ):
            socios.setdefault(phone, []).append(user_id)
        return {p: [p, *(f"anon:partner:{uid}" for uid in socios.get(p, []))] for p in unicos}

    def _client_phones_for(self, phone: str) -> list[str]:
        """
        Bajo qué clientes pueden estar las ops de este teléfono. Normalmente solo el suyo,
        pero si el número es el de un socio (`FundGroupMember.whatsapp_phone`) sus ops se
        reasignan al cliente anónimo `anon:partner:{user_id}` (ver `_apply_scenario` en
        whatsapp_quote_service): el comprobante que llega en su chat directo tiene que poder
        alcanzarlas igual. Sin esto, todo saliente pagado en el chat de un socio quedaba
        suelto (caso Dionis, 6→11 de agosto de 2026: 40 comprobantes sin operación). Ver
        `_client_phones_for_many`.
        """
        return self._client_phones_for_many([phone]).get(phone, [phone])

    def _operations_query(
        self,
        *,
        phone: Optional[str] = None,
        search: Optional[str] = None,
        group_jid: Optional[str] = None,
        scenario: Optional[WhatsAppOperationScenario] = None,
        statuses: Optional[Sequence[str]] = None,
        phones: Optional[Sequence[str]] = None,
        created_between: Optional[tuple[datetime, datetime]] = None,
        payable_by_receipt: bool = False,
    ):
        """
        La consulta filtrada, SIN ordenar ni recortar — la comparten quien cuenta el total
        (`_count_operations`) y quien carga el lote a puntuar (`_load_operation_models`), para
        que "cuántas hay tras el filtro" y "cuáles se puntúan" nunca diverjan por tener cada
        una su propia copia de los mismos `filter()`.

        `search` es nuevo aquí: antes solo lo sabía `WhatsAppQuoteService.list_operations`
        (`GET /operations`). El cajón de "vincular pago" ahora pide filtro Y puntuación al
        mismo endpoint (`POST /operations/match`), así que tiene que poder buscar igual.

        `phones` es la versión "ya resuelta" de `phone` (varios comprobantes a la vez, ver
        `suggest_for_payments`); `created_between` acota por fecha de creación de la op —
        la ventana del ranking del lado entrante — y `payable_by_receipt` excluye lo que
        JAMÁS puede cerrarse con un comprobante entrante (pares en efectivo, VIA_PARTNER).
        """
        from app.models.currency_pair import CurrencyPair
        from app.models.fund import FundGroup
        from app.models.whatsapp_client import WhatsAppClient
        from app.models.whatsapp_operation import WhatsAppOperationStatus

        q = self.db.query(WhatsAppOperation).options(
            selectinload(WhatsAppOperation.outgoing_payments)
        )
        # El buscador cruza cliente (nombre o teléfono), así que el join tiene que existir
        # aunque no haya `phone` — mismo criterio que `list_operations`.
        if phone or search or phones:
            q = q.join(WhatsAppClient, WhatsAppClient.id == WhatsAppOperation.client_id)
        if phone:
            q = q.filter(WhatsAppClient.phone.in_(self._client_phones_for(phone)))
        if phones:
            q = q.filter(WhatsAppClient.phone.in_(list(phones)))
        if created_between:
            desde, hasta = created_between
            q = q.filter(WhatsAppOperation.created_at >= desde)
            q = q.filter(WhatsAppOperation.created_at <= hasta)
        if search:
            like = f"%{search.strip()}%"
            q = q.filter(
                or_(
                    WhatsAppClient.display_name.ilike(like),
                    WhatsAppClient.phone.ilike(like),
                )
            )
        if scenario:
            q = q.filter(WhatsAppOperation.scenario == scenario)
        if statuses:
            q = q.filter(
                WhatsAppOperation.status.in_([WhatsAppOperationStatus(s) for s in statuses])
            )
        if payable_by_receipt:
            # Ni un par en efectivo ni un trato VIA_PARTNER reciben nunca comprobante del
            # cliente: el uno se paga con billetes y en el otro cobra el socio en su propio
            # chat. Ofrecerlos como candidatos solo puede terminar en un vínculo falso.
            q = q.join(CurrencyPair, CurrencyPair.id == WhatsAppOperation.currency_pair_id)
            q = q.filter(
                or_(
                    CurrencyPair.settles_in_cash.is_(False),
                    CurrencyPair.settles_in_cash.is_(None),
                )
            )
            q = q.filter(
                WhatsAppOperation.scenario != WhatsAppOperationScenario.VIA_PARTNER
            )
        if group_jid:
            # El bot conoce el grupo por su JID de WhatsApp; la op lo referencia por FK.
            group_ids = [
                g.id
                for g in self.db.query(FundGroup)
                .filter(FundGroup.whatsapp_group_jid == group_jid)
                .all()
            ]
            if not group_ids:
                return None
            q = q.filter(WhatsAppOperation.fund_group_id.in_(group_ids))
        return q

    def _load_operation_models(self, *, limit: int = 200, **filters) -> list[WhatsAppOperation]:
        q = self._operations_query(**filters)
        if q is None:
            return []
        return q.order_by(WhatsAppOperation.created_at.desc()).limit(limit).all()

    def _count_operations(self, **filters) -> int:
        """Total tras el filtro, SIN el tope de `limit` — lo que necesita el pie de página."""
        q = self._operations_query(**filters)
        if q is None:
            return 0
        return q.order_by(None).count()

    def _load_operations(self, *, limit: int = 200, **filters) -> list[OperationCandidate]:
        return self._to_candidates(self._load_operation_models(limit=limit, **filters))

    def _to_candidates(self, ops: Sequence[WhatsAppOperation]) -> list[OperationCandidate]:
        op_ids = [o.id for o in ops]
        out_taken: set[int] = set()
        collected: dict[int, float] = {}
        if op_ids:
            out_taken = {
                r[0]
                for r in self.db.query(WhatsAppOutgoingPayment.whatsapp_operation_id)
                .filter(WhatsAppOutgoingPayment.whatsapp_operation_id.in_(op_ids))
                .distinct()
                .all()
            }
            # Suma de lo ya asignado por operación: un entrante puede repartirse entre varias
            # (Zelle de 220 → 200 a BRL y 20 a VES), así que el "tiene entrante" del FK crudo
            # no basta para saber CUÁNTO cubre cada una.
            from app.models.whatsapp_payment import WhatsAppPaymentAllocation

            collected = {
                row[0]: float(row[1] or 0)
                for row in self.db.query(
                    WhatsAppPaymentAllocation.whatsapp_operation_id,
                    func.sum(WhatsAppPaymentAllocation.amount).label("total"),
                )
                .filter(WhatsAppPaymentAllocation.whatsapp_operation_id.in_(op_ids))
                .group_by(WhatsAppPaymentAllocation.whatsapp_operation_id)
                .all()
            }
        return [
            OperationCandidate.from_model(
                o,
                has_outgoing_payment=o.id in out_taken,
                has_incoming_payment=collected.get(o.id, 0.0) > 0,
                collected_incoming=collected.get(o.id, 0.0),
            )
            for o in ops
        ]

    # -- Política del bot -------------------------------------------------

    def auto_match(
        self,
        criteria: OutgoingCriteria,
        *,
        table: str = "outgoing",
        phone: Optional[str] = None,
        group_jid: Optional[str] = None,
        scenario: Optional[WhatsAppOperationScenario] = None,
        limit: int = 200,
    ) -> Optional[WhatsAppOperation]:
        """La operación a vincular, o None si hay ambigüedad. Devuelve el modelo entero
        porque el bot la necesita completa acto seguido (para completar la op)."""
        candidates = self._load_operations(
            phone=phone,
            group_jid=group_jid,
            scenario=scenario,
            # Del lado entrante solo compiten ops abiertas: se recorta ya en la consulta para
            # que el límite no se gaste en operaciones cerradas.
            statuses=OPEN_STATUSES if table == "incoming" else None,
            limit=limit,
        )
        matched = pick_auto_match(candidates, criteria, datetime.now(timezone.utc), table)
        if matched is None:
            return None
        return (
            self.db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.uuid == matched)
            .first()
        )

    def auto_match_forwarded_incoming(
        self, criteria: ForwardedCriteria, *, limit: int = 500
    ) -> Optional[WhatsAppIncomingPayment]:
        if not criteria.currency or not criteria.amount:
            return None
        rows = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(WhatsAppIncomingPayment.currency == criteria.currency)
            .order_by(WhatsAppIncomingPayment.created_at.desc())
            .limit(limit)
            .all()
        )
        used = {
            r[0]
            for r in self.db.query(WhatsAppOutgoingPayment.source_payment_id)
            .filter(WhatsAppOutgoingPayment.source_payment_id.isnot(None))
            .distinct()
            .all()
        }
        candidates = [IncomingCandidate.from_model(p) for p in rows]
        matched = pick_forwarded_incoming(
            candidates, used, criteria, datetime.now(timezone.utc)
        )
        if matched is None:
            return None
        return next((p for p in rows if p.id == matched), None)

    # -- Política del front -----------------------------------------------

    def rank_for_payment(
        self,
        payment_id: int,
        table: str,
        *,
        phone: Optional[str] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        order_by: str = "suggested",
        page: int = 1,
        limit: int = 200,
        scope: str = "client",
    ) -> MatchPage:
        """
        Una página de operaciones YA filtradas, puntuadas contra el comprobante y ordenadas
        según `order_by` — lo que antes resolvía el navegador sobre un lote sin filtrar
        (`sortScored` + `buildOperationQuery` en `LinkOperationPanel.tsx`).

        Los filtros son LOS MISMOS que `GET /operations` (`phone`, `search`, `status`) a
        propósito: es la misma pregunta —"qué operaciones ve el operador en el cajón de
        vincular"— con una columna más (la puntuación contra este comprobante). Así el cajón
        vive de UN solo endpoint: ya no hace falta pedir el listado y el ranking por separado
        y cruzarlos por uuid en el cliente.
        """
        model = (
            WhatsAppIncomingPayment if table == "incoming" else WhatsAppOutgoingPayment
        )
        payment = self.db.query(model).filter(model.id == payment_id).first()
        if payment is None:
            return MatchPage(items=[], suggestion=None, total=0, page=page, limit=limit)

        criteria = OutgoingCriteria(
            amount=payment.amount,
            currency=payment.currency,
            identification=payment.identification,
            phone_to=payment.phone_to,
            bank_to=payment.bank_to,
            created_at=_aware(payment.created_at),
        )
        # Por defecto el cajón enseña las operaciones DEL CLIENTE del comprobante (con sus
        # alias de socio) y solo las abiertas: ese es el universo real de lo que ese pago
        # puede cerrar. `scope="all"` es el botón «buscar en todos los clientes», que el
        # operador pulsa a sabiendas — y ahí `same_client` deja de ser siempre cierto.
        filters = dict(phone=phone, search=search, statuses=[status] if status else None)
        if scope == "client" and table == "incoming" and phone is None:
            filters["phones"] = self._client_phones_for_many([payment.client_phone]).get(
                payment.client_phone, []
            )
            filters["statuses"] = filters["statuses"] or list(OPEN_STATUSES)
            filters["payable_by_receipt"] = True

        # El total cuenta sobre TODO lo que cumple el filtro, sin el tope de seguridad de
        # `MATCH_POOL_LIMIT`: el pie del cajón no debe decir "26 de 200" solo porque el
        # ranking no puntúa más de 500 candidatas de una sentada (ver la nota de
        # `MATCH_POOL_LIMIT` sobre cuándo ese tope sí muerde).
        total = self._count_operations(**filters)

        ops = self._load_operation_models(limit=MATCH_POOL_LIMIT, **filters)
        candidates = self._to_candidates(ops)
        by_uuid_op = {str(o.uuid): o for o in ops}
        by_uuid_cand = {c.uuid: c for c in candidates}

        now = datetime.now(timezone.utc)
        scored = rank_candidates(candidates, criteria, table, now)
        suggestion = pick_suggestion(scored)
        ordered = self._order_scores(scored, order_by, suggestion, by_uuid_cand)

        start = max(0, (page - 1) * limit)
        page_scores = ordered[start : start + limit]
        items = [(by_uuid_op[s.uuid], s) for s in page_scores]
        return MatchPage(items=items, suggestion=suggestion, total=total, page=page, limit=limit)

    @staticmethod
    def _order_scores(
        scored: Sequence[MatchScore],
        order_by: str,
        suggestion: Optional[Suggestion],
        by_uuid_cand: dict,
    ) -> list[MatchScore]:
        """
        Los tres botones del cajón ("sugerida" / "monto" / "hora"), ahora resueltos aquí en
        vez de en `sortScored` (front). `scored` ya viene de `rank_candidates` ordenado por
        (-score, -created_at desc); ese orden se reutiliza tal cual para "sugerida".
        """
        epoch = datetime.min.replace(tzinfo=timezone.utc)

        def created_at(s: MatchScore) -> datetime:
            cand = by_uuid_cand.get(s.uuid)
            return cand.created_at if cand and cand.created_at else epoch

        if order_by == "time":
            # "hora": recencia pura, sin mirar el comprobante — el orden que había antes de
            # que existiera la puntuación.
            return sorted(scored, key=created_at, reverse=True)

        if order_by == "amount":
            # "monto": cercanía relativa al comprobante (`score.relative`, igual que leía el
            # front). Sin comparación posible (`relative` None) al final; empate por
            # recencia, el mismo desempate que usaba `sortScored`.
            return sorted(
                scored,
                key=lambda s: (
                    s.relative if s.relative is not None else float("inf"),
                    -created_at(s).timestamp(),
                ),
            )

        # "suggested" (default): el orden de `scored` ya es por puntaje combinado, pero la
        # sugerida se sube al frente si no encabezaba — puede no hacerlo, porque
        # `pick_suggestion` solo compite ENTRE las candidatas dentro de tolerancia, mientras
        # que `rank_candidates` no distingue eso al ordenar (ver el test de este módulo que lo
        # fuerza). Es exactamente lo que hacía `sortScored` en el front con `suggestionUuid`.
        ordered = list(scored)
        if suggestion is not None:
            idx = next((i for i, s in enumerate(ordered) if s.uuid == suggestion.uuid), -1)
            if idx > 0:
                ordered.insert(0, ordered.pop(idx))
        return ordered

    def _create_hint(self, payment, alias_phones: Sequence[str]) -> Optional[dict]:
        """
        Con qué par nacería la operación de este comprobante, y cuánto daría.

        Orden de preferencia: el par preferido del cliente → el que más usa en su historial →
        el que se deduce de la moneda del comprobante. Si ninguno resuelve se devuelve `None`
        y la tarjeta abre el formulario vacío; adivinar un par es peor que no proponerlo.

        La tasa se pide A LA FECHA DEL COMPROBANTE, no la de hoy: la bandeja se procesa días
        después y cotizar con la tasa de hoy un cambio del lunes reescribe el margen.
        """
        from app.models.currency_pair import CurrencyPair
        from app.models.whatsapp_client import WhatsAppClient
        from app.repositories.exchange_rate_repository import ExchangeRateRepository
        from app.services.whatsapp_rate_resolver import WhatsAppRateResolver

        cliente = (
            self.db.query(WhatsAppClient)
            .filter(WhatsAppClient.phone.in_(list(alias_phones)))
            .first()
        )
        par, motivo = None, None
        if cliente is not None and cliente.preferred_pair_id:
            par = (
                self.db.query(CurrencyPair)
                .filter(CurrencyPair.id == cliente.preferred_pair_id)
                .first()
            )
            motivo = "preferred"
        if par is None and cliente is not None:
            fila = (
                self.db.query(
                    WhatsAppOperation.currency_pair_id,
                    func.count(WhatsAppOperation.id).label("n"),
                )
                .filter(WhatsAppOperation.client_id == cliente.id)
                .group_by(WhatsAppOperation.currency_pair_id)
                .order_by(func.count(WhatsAppOperation.id).desc())
                .first()
            )
            if fila is not None:
                par = self.db.query(CurrencyPair).filter(CurrencyPair.id == fila[0]).first()
                motivo = "most_used"
        if par is None and payment.currency:
            par = (
                self.db.query(CurrencyPair)
                .join(CurrencyPair.from_currency)
                .filter(CurrencyPair.is_active.is_(True))
                .filter(CurrencyPair.from_currency.has(symbol=payment.currency))
                .first()
            )
            motivo = "currency" if par is not None else None
        if par is None:
            return None

        repo = ExchangeRateRepository(self.db)
        at = _aware(payment.created_at)
        tasa = repo.get_rate_by_pair_at(par.uuid, at) if at else None
        if tasa is None:
            tasa = repo.get_active_rate_by_pair(par.uuid)
        if tasa is None:
            return None
        destino = WhatsAppRateResolver.apply_rate(payment.amount, tasa.rate, tasa.inverse_percentage)
        return {
            "pair_uuid": str(par.uuid),
            "pair_symbol": f"{par.from_currency.symbol}/{par.to_currency.symbol}",
            "reason": motivo,
            "rate": tasa.rate,
            "rate_at": tasa.created_at.date().isoformat() if tasa.created_at else None,
            "from_amount": payment.amount,
            "to_amount": round(destino, 2),
            "from_currency": par.from_currency.symbol,
            "to_currency": par.to_currency.symbol,
        }

    def suggest_for_payments(
        self, payment_ids: Sequence[int], table: str, *, limit: int = 500
    ) -> list[dict]:
        """
        La operación sugerida para varios comprobantes de una vez, para pintarla en el
        listado sin una petición por fila.

        Es `rank_for_payment` repetida, pero cargando las candidatas UNA sola vez: la parte
        cara es la consulta, no la puntuación (que es aritmética en memoria). Devuelve solo
        los comprobantes que tienen sugerencia, con lo justo para dibujar la celda.
        """
        if not payment_ids:
            return []

        model = (
            WhatsAppIncomingPayment if table == "incoming" else WhatsAppOutgoingPayment
        )
        payments = self.db.query(model).filter(model.id.in_(payment_ids)).all()
        if not payments:
            return []

        now = datetime.now(timezone.utc)

        if table == "incoming":
            ventana = timedelta(hours=INCOMING_WINDOW_HOURS)
            alias = self._client_phones_for_many([p.client_phone for p in payments])
            todos = sorted({t for lista in alias.values() for t in lista})
            fechas = [_aware(p.created_at) for p in payments if p.created_at]
            if not todos or not fechas:
                return []
            ops = self._load_operation_models(
                limit=MATCH_POOL_LIMIT,
                phones=todos,
                statuses=OPEN_STATUSES,
                created_between=(min(fechas) - ventana, max(fechas) + ventana),
                payable_by_receipt=True,
            )
            candidates = self._to_candidates(ops)
            # Las candidatas se reparten por cliente en memoria: la consulta ya vino acotada
            # al lote entero, y volver a la base una vez por comprobante no aporta nada.
            # `_to_candidates` y `_load_operation_models` devuelven listas paralelas.
            por_telefono: dict[str, list[OperationCandidate]] = {}
            for cand, modelo in zip(candidates, ops):
                telefono = modelo.client.phone if modelo.client else None
                if telefono:
                    por_telefono.setdefault(telefono, []).append(cand)
        else:
            candidates = self._load_operations(limit=limit)
            if not candidates:
                return []
            alias = {}
            por_telefono = {}
        by_uuid = {c.uuid: c for c in candidates}
        # Modelo real de la op, para lo que la candidata plana no trae (nombre del cliente,
        # vencimiento). Solo existe del lado entrante, donde `ops` sí se cargó arriba.
        por_uuid_op = {str(o.uuid): o for o in ops} if table == "incoming" else {}

        out: list[dict] = []
        for payment in payments:
            criteria = OutgoingCriteria(
                amount=payment.amount,
                currency=payment.currency,
                identification=payment.identification,
                phone_to=payment.phone_to,
                bank_to=payment.bank_to,
                created_at=_aware(payment.created_at),
            )
            if table == "incoming":
                propias = [
                    c
                    for t in alias.get(payment.client_phone, [])
                    for c in por_telefono.get(t, [])
                ]
                scored = rank_candidates(propias, criteria, table, now)
            else:
                scored = rank_candidates(candidates, criteria, table, now)
            suggestion = pick_suggestion(scored)
            if suggestion is None:
                if table == "incoming":
                    hint = self._create_hint(payment, alias.get(payment.client_phone, []))
                    if hint is not None:
                        out.append({"payment_id": payment.id, "kind": "CREATE", "create_hint": hint})
                continue
            cand = by_uuid.get(suggestion.uuid)
            best = next((s for s in scored if s.uuid == suggestion.uuid), None)
            if cand is None or best is None:
                continue
            item = {
                "payment_id": payment.id,
                "kind": "LINK",
                "operation_uuid": cand.uuid,
                "confident": suggestion.confident,
                "score": round(best.score, 4),
                "delta": best.delta,
                "from_amount": cand.from_amount,
                "from_currency": cand.from_currency,
                "to_amount": cand.to_amount,
                "to_currency": cand.to_currency,
                "status": cand.status,
            }
            if table == "incoming":
                modelo = por_uuid_op.get(str(cand.uuid))
                horas = (
                    (_aware(payment.created_at) - cand.created_at).total_seconds() / 3600.0
                    if cand.created_at and payment.created_at
                    else None
                )
                item.update(
                    {
                        "coverage": best.coverage,
                        "client_name": (
                            modelo.client.display_name if modelo and modelo.client else None
                        ),
                        "client_uuid": (
                            str(modelo.client.uuid) if modelo and modelo.client else None
                        ),
                        "same_client": bool(
                            modelo
                            and modelo.client
                            and modelo.client.phone in alias.get(payment.client_phone, [])
                        ),
                        "operation_created_at": (
                            cand.created_at.isoformat() if cand.created_at else None
                        ),
                        "hours_apart": round(horas, 2) if horas is not None else None,
                        "expired": bool(
                            modelo and modelo.expires_at and _aware(modelo.expires_at) <= now
                        ),
                        "missing_before": cand.missing_incoming,
                        "missing_after": round(
                            max(0.0, cand.missing_incoming - (payment.amount or 0)), 2
                        ),
                    }
                )
            out.append(item)
        return out
