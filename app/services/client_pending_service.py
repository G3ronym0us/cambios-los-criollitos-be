"""
Lo que le debemos al cliente: agregado por par, y las entregas que lo saldan.

«Por entregar» es el trozo del valor de una operación que ningún comprobante de salida
cubre todavía — el mismo `missing` que el listado de Operaciones llama «por cuadrar»
(`needs=settle`), leído desde el lado del cliente en vez del de la operación.

    missing = valor − entregado − hueco declarado

**Cuidado con el nombre**: la tarjeta «por entregar» del listado de Operaciones es OTRA
cosa (`delivery_status`, el efectivo que falta mover en mano). Aquí, y en el módulo de
Clientes entero, «por entregar» es `missing > 0` **y su dinero ya entró**.

Esa segunda condición es la que separa una deuda de un trato en el papel. `missing` sale
de los comprobantes de SALIDA: mide lo que no le hemos pagado. Pero no le debemos nada
hasta que su plata llega — una operación registrada sin comprobante entrante es una
cotización o un trato a medio armar, no una deuda. Contarlas mezclaba las dos patas del
cambio y hacía que la lista pidiera pagar cosas que nadie había pagado todavía.

**La moneda**: `missing` va en la moneda del VALOR del trato (lo que entrega el cliente:
los USD de un USD/VES), no en la moneda con la que se le paga. Es la cifra exacta, la que
se suma y por la que se ordena. El equivalente en moneda de pago sale de la proporción del
propio trato y viaja aparte, para enseñarse con «≈» — nunca para sumarse.
"""

from datetime import datetime, timezone
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import Float, and_, func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.currency_pair import CurrencyPair
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.models.whatsapp_payment import (
    WhatsAppIncomingPayment,
    WhatsAppOutgoingSettlement,
    WhatsAppPaymentAllocation,
)
from app.models.whatsapp_pending_delivery import (
    WhatsAppPendingDelivery,
    WhatsAppPendingDeliveryItem,
)
from app.services.whatsapp_quote_service import QuoteServiceError

#: Debajo de esto es ruido de redondeo, no deuda. Igual que en el front.
EPSILON = 0.01

#: Motivo con el que se declara el hueco de una entrega sin comprobante.
DELIVERY_REASON = "CASH"


class ClientPendingService:
    def __init__(self, db: Session):
        self.db = db

    # ── Lectura ──────────────────────────────────────────────────────────────

    def _settled_subquery(self):
        """Cuánto cubren ya los comprobantes de cada operación, agregado en SQL."""
        return (
            self.db.query(
                WhatsAppOutgoingSettlement.whatsapp_operation_id.label("op_id"),
                func.coalesce(func.sum(WhatsAppOutgoingSettlement.settled_amount), 0).label(
                    "delivered"
                ),
            )
            .group_by(WhatsAppOutgoingSettlement.whatsapp_operation_id)
            .subquery()
        )

    def _first_incoming_subquery(self):
        """
        Cuándo entró el dinero de cada operación: su primer comprobante entrante.

        Mira los dos vínculos posibles —el FK del pago y el reparto— porque un Zelle
        repartido entre dos cambios sólo tiene FK a uno de ellos.
        """
        direct = self.db.query(
            WhatsAppIncomingPayment.whatsapp_operation_id.label("op_id"),
            WhatsAppIncomingPayment.created_at.label("paid_at"),
        ).filter(WhatsAppIncomingPayment.whatsapp_operation_id.isnot(None))

        allocated = (
            self.db.query(
                WhatsAppPaymentAllocation.whatsapp_operation_id.label("op_id"),
                WhatsAppIncomingPayment.created_at.label("paid_at"),
            )
            .join(
                WhatsAppIncomingPayment,
                WhatsAppIncomingPayment.id == WhatsAppPaymentAllocation.incoming_payment_id,
            )
        )

        union = direct.union_all(allocated).subquery()
        return (
            self.db.query(
                union.c.op_id.label("op_id"),
                func.min(union.c.paid_at).label("paid_at"),
            )
            .group_by(union.c.op_id)
            .subquery()
        )

    def _pending_query(self, pair: Optional[str] = None):
        """
        La consulta base de lo sin cubrir, ya unida a lo que hace falta para agregarla.

        Devuelve `(query, missing, since)` para que quien la use elija qué proyectar sin
        rearmar los joins.

        El join con los comprobantes entrantes es INNER a propósito: es lo que implementa
        «sólo debemos lo que ya nos pagaron». Cambiarlo a outer volvería a meter en la
        deuda las operaciones que nadie ha pagado.
        """
        settled = self._settled_subquery()
        paid = self._first_incoming_subquery()

        value = func.coalesce(WhatsAppOperation.amount, WhatsAppOperation.from_amount)
        missing = (
            value
            - func.coalesce(settled.c.delivered, 0)
            - func.coalesce(WhatsAppOperation.uncovered_amount, 0)
        )
        # La antigüedad se mide desde que entró el dinero. El `coalesce` es defensivo: el
        # join de abajo ya garantiza que hay comprobante, así que en la práctica manda
        # `paid_at`.
        since = func.coalesce(paid.c.paid_at, WhatsAppOperation.created_at)

        q = (
            self.db.query(WhatsAppOperation)
            .join(CurrencyPair, CurrencyPair.id == WhatsAppOperation.currency_pair_id)
            .outerjoin(settled, settled.c.op_id == WhatsAppOperation.id)
            .join(paid, paid.c.op_id == WhatsAppOperation.id)
            .filter(
                WhatsAppOperation.status.in_(
                    [WhatsAppOperationStatus.PENDING, WhatsAppOperationStatus.QUOTED]
                ),
                missing > EPSILON,
            )
        )
        if pair:
            q = q.filter(CurrencyPair.pair_symbol == pair)
        return q, missing, since

    def client_ids_with_pending(self, pair: Optional[str] = None) -> list[int]:
        """Los clientes a los que hoy les debemos algo. Para el filtro del listado."""
        q, _, _ = self._pending_query(pair)
        rows = q.with_entities(WhatsAppOperation.client_id).distinct().all()
        return [r[0] for r in rows if r[0] is not None]

    def pending_by_client_ids(
        self, client_ids: Iterable[int], pair: Optional[str] = None
    ) -> dict[int, list[dict]]:
        """
        La deuda de cada cliente agrupada por par, resuelta en UNA consulta.

        Agrupar por par y no dar un total único es deliberado: USD, VES y USDT no se suman
        sin inventarse una tasa. La moneda de cada grupo sale del par, que es lo que la hace
        homogénea.

        `payout_amount` se cae a `None` en cuanto una sola operación del grupo no se pueda
        convertir: media suma miente más que ninguna.
        """
        ids = [i for i in client_ids if i is not None]
        if not ids:
            return {}

        q, missing, since = self._pending_query(pair)
        # La proporción del propio trato, que es la conversión honesta: no hay que adivinar
        # de qué lado va la tasa ni leer `inverse_percentage`.
        payout = func.cast(missing, Float) * WhatsAppOperation.to_amount / func.nullif(
            WhatsAppOperation.from_amount, 0
        )

        rows = (
            q.filter(WhatsAppOperation.client_id.in_(ids))
            .with_entities(
                WhatsAppOperation.client_id.label("client_id"),
                CurrencyPair.id.label("pair_id"),
                func.sum(missing).label("amount"),
                func.count(WhatsAppOperation.id).label("operations"),
                func.min(since).label("oldest_at"),
                func.sum(payout).label("payout_amount"),
                # Cuántas se pudieron convertir: si no son todas, el equivalente del grupo
                # entero deja de ser cierto.
                func.count(payout).label("convertible"),
            )
            .group_by(WhatsAppOperation.client_id, CurrencyPair.id)
            .all()
        )

        pairs = {
            p.id: p
            for p in self.db.query(CurrencyPair)
            .filter(CurrencyPair.id.in_({r.pair_id for r in rows}))
            .options(
                selectinload(CurrencyPair.from_currency),
                selectinload(CurrencyPair.to_currency),
            )
            .all()
        }

        result: dict[int, list[dict]] = {}
        for row in rows:
            cp = pairs.get(row.pair_id)
            whole = row.convertible == row.operations
            result.setdefault(row.client_id, []).append(
                {
                    "pair_symbol": cp.pair_symbol if cp else None,
                    "currency": cp.from_currency.symbol if cp and cp.from_currency else None,
                    "amount": round(float(row.amount or 0), 2),
                    "operations": int(row.operations or 0),
                    "oldest_at": row.oldest_at,
                    "payout_currency": cp.to_currency.symbol if cp and cp.to_currency else None,
                    "payout_amount": (
                        round(float(row.payout_amount), 2)
                        if whole and row.payout_amount is not None
                        else None
                    ),
                }
            )

        # De mayor a menor deuda, que es el orden en que se lee.
        for entries in result.values():
            entries.sort(key=lambda e: e["amount"], reverse=True)
        return result

    # ── Escritura ────────────────────────────────────────────────────────────

    @staticmethod
    def _as_uuid(value) -> UUID:
        """Acepta un UUID o su texto: el servicio es público y lo llaman los dos."""
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            raise QuoteServiceError("invalid_uuid", f"UUID inválido: {value}", 400)

    def _client_or_404(self, client_uuid: UUID) -> WhatsAppClient:
        client = (
            self.db.query(WhatsAppClient)
            .filter(WhatsAppClient.uuid == self._as_uuid(client_uuid))
            .first()
        )
        if client is None:
            raise QuoteServiceError("client_not_found", "Cliente no encontrado", 404)
        return client

    def _pending_amount(self, op: WhatsAppOperation) -> float:
        value = op.amount if op.amount is not None else op.from_amount
        return round(
            float(value or 0) - op.delivered_amount - float(op.uncovered_amount or 0), 2
        )

    def deliver(
        self,
        client_uuid: UUID,
        items: list[dict],
        note: Optional[str],
        actor: Optional[User],
    ) -> dict:
        """
        Marca como entregado, en efectivo y sin comprobante, un lote de operaciones.

        **O todas o ninguna.** Una entrega a medias es peor que un error: deja dinero
        marcado sin que nadie sepa cuánto, así que cualquier problema levanta y no se
        guarda nada.

        Lo que escribe es `uncovered_amount`, que es el hueco ENTERO de la operación y no un
        incremento: lo entregado se SUMA a lo que ya hubiera. Antes de tocarlo se guarda el
        valor previo en el lote, que es lo único que permite deshacer de verdad.
        """
        client = self._client_or_404(client_uuid)
        if not items:
            raise QuoteServiceError("empty_delivery", "No hay operaciones que entregar", 400)

        delivery = WhatsAppPendingDelivery(
            client_id=client.id,
            note=(note or None),
            created_by_user_id=actor.id if actor else None,
        )
        self.db.add(delivery)

        seen: set = set()
        for item in items:
            op_uuid = self._as_uuid(item.get("operation_uuid"))
            if op_uuid in seen:
                raise QuoteServiceError(
                    "duplicate_operation",
                    f"La operación {op_uuid} viene dos veces en el mismo lote",
                    400,
                )
            seen.add(op_uuid)

            op = (
                self.db.query(WhatsAppOperation)
                .filter(WhatsAppOperation.uuid == op_uuid)
                .first()
            )
            if op is None:
                raise QuoteServiceError("operation_not_found", f"Operación {op_uuid} no encontrada", 404)
            if op.client_id != client.id:
                raise QuoteServiceError(
                    "operation_client_mismatch",
                    f"La operación {op_uuid} no es de este cliente",
                    400,
                )
            if op.status == WhatsAppOperationStatus.CANCELLED:
                raise QuoteServiceError(
                    "operation_cancelled",
                    f"La operación {op_uuid} está cancelada",
                    409,
                )
            # Entregarle a alguien que no sabemos quién es no es entregar. La regla vive
            # acá y no sólo en el front: el front la usa para no ofrecerlas.
            if not op.beneficiary_alias and op.beneficiary_account_id is None:
                raise QuoteServiceError(
                    "operation_without_beneficiary",
                    f"La operación {op_uuid} no tiene beneficiario: falta el dato",
                    409,
                )

            pending = self._pending_amount(op)
            if pending <= EPSILON:
                raise QuoteServiceError(
                    "nothing_pending",
                    f"La operación {op_uuid} no tiene nada por entregar",
                    409,
                )

            raw = item.get("amount")
            amount = round(float(raw), 2) if raw is not None else pending
            if amount <= 0:
                raise QuoteServiceError(
                    "invalid_amount", f"El monto de {op_uuid} debe ser > 0", 400
                )
            if amount > pending + EPSILON:
                raise QuoteServiceError(
                    "amount_exceeds_pending",
                    f"La operación {op_uuid} sólo debe {pending}, no se puede entregar {amount}",
                    400,
                )

            delivery.items.append(
                WhatsAppPendingDeliveryItem(
                    whatsapp_operation_id=op.id,
                    amount=amount,
                    previous_uncovered=op.uncovered_amount,
                    previous_uncovered_reason=op.uncovered_reason,
                )
            )
            op.uncovered_amount = round(float(op.uncovered_amount or 0) + amount, 2)
            op.uncovered_reason = DELIVERY_REASON

        self.db.commit()
        self.db.refresh(delivery)
        return delivery.dict()

    def undo(self, client_uuid: UUID, delivery_uuid: UUID, actor: Optional[User]) -> dict:
        """
        Devuelve las operaciones del lote a como estaban, sin borrar el rastro.

        Repone el hueco previo en vez de restar lo entregado: si entre medias alguien tocó
        la cobertura a mano, restar dejaría un número inventado. Y deshacer no borra el
        lote — queda quién marcó, quién deshizo y cuándo, que es lo que hace auditable un
        error. Sin límite de tiempo: si se descubre mañana, se deshace mañana.
        """
        client = self._client_or_404(client_uuid)
        delivery = (
            self.db.query(WhatsAppPendingDelivery)
            .filter(WhatsAppPendingDelivery.uuid == self._as_uuid(delivery_uuid))
            .first()
        )
        if delivery is None or delivery.client_id != client.id:
            raise QuoteServiceError("delivery_not_found", "Entrega no encontrada", 404)
        if delivery.undone_at is not None:
            raise QuoteServiceError("already_undone", "Esa entrega ya se deshizo", 409)

        for item in delivery.items:
            op = item.operation
            if op is None:
                continue
            op.uncovered_amount = item.previous_uncovered
            op.uncovered_reason = item.previous_uncovered_reason

        delivery.undone_at = datetime.now(timezone.utc)
        delivery.undone_by_user_id = actor.id if actor else None
        self.db.commit()
        self.db.refresh(delivery)
        return delivery.dict()

    def history(self, client_uuid: UUID, limit: int = 50) -> list[dict]:
        """Las entregas del cliente, de la más nueva a la más vieja."""
        client = self._client_or_404(client_uuid)
        rows = (
            self.db.query(WhatsAppPendingDelivery)
            .filter(WhatsAppPendingDelivery.client_id == client.id)
            .options(selectinload(WhatsAppPendingDelivery.items))
            .order_by(WhatsAppPendingDelivery.created_at.desc())
            .limit(limit)
            .all()
        )
        return [row.dict() for row in rows]
