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
#: Horas a las que la cercanía temporal vale la mitad.
TIME_HALF_LIFE_HOURS = 6.0
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

#: El comprobante reenviado es el MISMO, no uno parecido: tolerancia mucho más dura.
FORWARDED_TOLERANCE = 0.001
FORWARDED_WINDOW_MINUTES = 60


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

    @classmethod
    def from_model(
        cls,
        op: WhatsAppOperation,
        *,
        has_outgoing_payment: bool = False,
        has_incoming_payment: bool = False,
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
        return [
            t
            for t in (self.identification, self.phone_to, self.bank_to)
            if t and len(t) >= MIN_TOKEN_LENGTH
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


@dataclass
class Suggestion:
    uuid: str
    #: Gana con claridad: el front puede preseleccionarla sin arriesgar un vínculo por inercia.
    confident: bool


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
        return cand.from_amount, cand.from_currency
    delivered = cand.delivered_amount or 0
    pending = cand.pending_amount or 0
    value = cand.value_amount or cand.from_amount or 0
    if delivered > 0.01 and pending > 0.01 and value > 0 and cand.to_amount > 0:
        return cand.to_amount * (pending / value), cand.to_currency
    return cand.to_amount, cand.to_currency


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


def rank_candidates(
    candidates: Iterable[OperationCandidate],
    criteria: OutgoingCriteria,
    table: str,
    now: datetime,
) -> list[MatchScore]:
    """Puntúa todas y devuelve de mejor a peor (desempate: la más reciente primero)."""
    scores: list[tuple[MatchScore, Optional[datetime]]] = [
        (score_candidate(cand, criteria, table, now), cand.created_at) for cand in candidates
    ]
    scores.sort(
        key=lambda pair: (
            -pair[0].score,
            -(pair[1].timestamp() if pair[1] else 0),
        )
    )
    return [s for s, _ in scores]


def pick_suggestion(scored: Sequence[MatchScore]) -> Optional[Suggestion]:
    """
    Política del FRONT: la mejor candidata, marcada como inequívoca solo si ninguna otra
    queda igual de cerca. En la duda el operador elige a mano.
    """
    eligible = sorted(
        [s for s in scored if s.within_tolerance], key=lambda s: s.score, reverse=True
    )
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
    refined = [c for c in matches if c.notes and any(t in c.notes for t in tokens)]
    return refined[0].uuid if len(refined) == 1 else None


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
    """
    if criteria.amount is None or criteria.amount <= 0 or not criteria.currency:
        return None

    fingerprint = receipt_fingerprint(criteria.raw_text)
    #: Sin referencia ni huella no hay forma de afirmar que es el mismo comprobante.
    needs_proof = criteria.currency != "ZELLE" and not criteria.reference
    if needs_proof and not fingerprint:
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
        # Si el comprobante trae referencia, identifica la transferencia: tiene que coincidir.
        and (not criteria.reference or c.reference == criteria.reference)
        and (not needs_proof or receipt_fingerprint(c.raw_text) == fingerprint)
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

    def _load_operations(
        self,
        *,
        phone: Optional[str] = None,
        group_jid: Optional[str] = None,
        scenario: Optional[WhatsAppOperationScenario] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 200,
    ) -> list[OperationCandidate]:
        from app.models.fund import FundGroup
        from app.models.whatsapp_client import WhatsAppClient
        from app.models.whatsapp_operation import WhatsAppOperationStatus

        q = self.db.query(WhatsAppOperation).options(
            selectinload(WhatsAppOperation.outgoing_payments)
        )
        if phone:
            q = q.join(WhatsAppClient).filter(WhatsAppClient.phone == phone)
        if scenario:
            q = q.filter(WhatsAppOperation.scenario == scenario)
        if statuses:
            q = q.filter(
                WhatsAppOperation.status.in_([WhatsAppOperationStatus(s) for s in statuses])
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
                return []
            q = q.filter(WhatsAppOperation.fund_group_id.in_(group_ids))

        ops = q.order_by(WhatsAppOperation.created_at.desc()).limit(limit).all()
        return self._to_candidates(ops)

    def _to_candidates(self, ops: Sequence[WhatsAppOperation]) -> list[OperationCandidate]:
        op_ids = [o.id for o in ops]
        inc_taken: set[int] = set()
        out_taken: set[int] = set()
        if op_ids:
            inc_taken = {
                r[0]
                for r in self.db.query(WhatsAppIncomingPayment.whatsapp_operation_id)
                .filter(WhatsAppIncomingPayment.whatsapp_operation_id.in_(op_ids))
                .distinct()
                .all()
            }
            out_taken = {
                r[0]
                for r in self.db.query(WhatsAppOutgoingPayment.whatsapp_operation_id)
                .filter(WhatsAppOutgoingPayment.whatsapp_operation_id.in_(op_ids))
                .distinct()
                .all()
            }
        return [
            OperationCandidate.from_model(
                o,
                has_outgoing_payment=o.id in out_taken,
                has_incoming_payment=o.id in inc_taken,
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
        self, payment_id: int, table: str, *, limit: int = 500
    ) -> tuple[list[MatchScore], Optional[Suggestion]]:
        """Puntúa las operaciones recientes contra un comprobante ya guardado."""
        model = (
            WhatsAppIncomingPayment if table == "incoming" else WhatsAppOutgoingPayment
        )
        payment = self.db.query(model).filter(model.id == payment_id).first()
        if payment is None:
            return [], None

        criteria = OutgoingCriteria(
            amount=payment.amount,
            currency=payment.currency,
            identification=payment.identification,
            phone_to=payment.phone_to,
            bank_to=payment.bank_to,
            created_at=_aware(payment.created_at),
        )
        candidates = self._load_operations(limit=limit)
        now = datetime.now(timezone.utc)
        scored = rank_candidates(candidates, criteria, table, now)
        return scored, pick_suggestion(scored)

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

        candidates = self._load_operations(limit=limit)
        if not candidates:
            return []
        by_uuid = {c.uuid: c for c in candidates}
        now = datetime.now(timezone.utc)

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
            scored = rank_candidates(candidates, criteria, table, now)
            suggestion = pick_suggestion(scored)
            if suggestion is None:
                continue
            cand = by_uuid.get(suggestion.uuid)
            best = next((s for s in scored if s.uuid == suggestion.uuid), None)
            if cand is None or best is None:
                continue
            out.append(
                {
                    "payment_id": payment.id,
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
            )
        return out
