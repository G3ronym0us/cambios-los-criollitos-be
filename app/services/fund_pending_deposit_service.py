"""
Servicio de depósitos PENDIENTES detectados por el bot cuando un gestor del fondo
(FundGroupMember.is_fund_manager) sube un comprobante al grupo de WhatsApp. El operador
los confirma/rechaza desde `/admin/funds`. Al confirmar se materializa un FundMovement
DEPOSIT (reusa FundRepository.create_movement); el balance del fondo solo cuenta movimientos.

Esta tabla es el ÚNICO camino a un FundMovement DEPOSIT (ver `FundPendingDeposit`): el alta
manual de movimientos rechaza DEPOSIT, y lo que el bot no detecta se carga aquí con
`create_manual`.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.fund import (
    FundGroup,
    FundGroupMember,
    FundMovementType,
    FundPendingDeposit,
    FundPendingDepositOrigin,
    FundPendingDepositStatus,
)
from app.models.user import User
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.repositories.fund_repository import FundRepository
from app.services.whatsapp_quote_service import QuoteServiceError

# Ventana para el match por monto: sin referencia, dos comprobantes del mismo monto y moneda
# separados por más de día y medio son casi siempre dinero distinto (montos redondos se
# repiten). Con referencia el match no necesita ventana.
DUPLICATE_WINDOW = timedelta(hours=36)


class FundPendingDepositService:
    def __init__(self, db: Session):
        self.db = db
        self.fund_repo = FundRepository(db)

    # ---------- Crear (bot) ----------

    def create_pending(
        self,
        group_jid: Optional[str] = None,
        group_uuid: Optional[UUID] = None,
        detected_phone: Optional[str] = None,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        provider: Optional[str] = None,
        reference: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> dict:
        group = self._resolve_group(group_jid, group_uuid)
        detected_user_id = None
        if detected_phone:
            member = (
                self.db.query(FundGroupMember)
                .filter(
                    FundGroupMember.group_id == group.id,
                    FundGroupMember.whatsapp_phone == detected_phone,
                )
                .first()
            )
            if member:
                detected_user_id = member.user_id

        row = FundPendingDeposit(
            group_id=group.id,
            detected_user_id=detected_user_id,
            amount=amount,
            currency=(currency.upper() if currency else None),
            provider=provider,
            reference=reference,
            raw_text=raw_text,
            status=FundPendingDepositStatus.PENDING,
            origin=FundPendingDepositOrigin.GROUP,
            source_incoming_payment_id=self._find_duplicate_incoming(amount, currency, reference),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row.dict()

    def create_manual(
        self,
        group_uuid: UUID,
        user_uuid: UUID,
        amount: float,
        currency: str,
        created_by_user_id: int,
        provider: Optional[str] = None,
        reference: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Alta manual de un pendiente (el bot no detectó el comprobante, o el gestor repuso el
        fondo sin postearlo). Entra por la misma puerta: queda PENDING y se confirma igual.
        """
        group = self._resolve_group(None, group_uuid)
        user = self.db.query(User).filter(User.uuid == str(user_uuid)).first()
        if user is None:
            raise QuoteServiceError("user_not_found", "Usuario depositante no encontrado", 404)
        if not amount or amount <= 0:
            raise QuoteServiceError("invalid_amount", "El monto debe ser > 0", 400)
        if not currency:
            raise QuoteServiceError("missing_fields", "Falta la moneda del depósito", 400)

        row = FundPendingDeposit(
            group_id=group.id,
            detected_user_id=user.id,
            amount=amount,
            currency=currency.upper(),
            provider=provider,
            reference=reference,
            raw_text=notes,
            status=FundPendingDepositStatus.PENDING,
            origin=FundPendingDepositOrigin.MANUAL,
            created_by_user_id=created_by_user_id,
            source_incoming_payment_id=self._find_duplicate_incoming(amount, currency, reference),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row.dict()

    # ---------- Listar / confirmar / rechazar (front, JWT) ----------

    def list_pending(self, status: Optional[str] = "PENDING") -> list[dict]:
        q = self.db.query(FundPendingDeposit)
        if status and status != "ALL":
            try:
                q = q.filter(FundPendingDeposit.status == FundPendingDepositStatus(status.upper()))
            except ValueError:
                raise QuoteServiceError("invalid_status", f"Status inválido: {status}", 400)
        rows = q.order_by(FundPendingDeposit.created_at.desc()).limit(500).all()
        return [r.dict() for r in rows]

    def _get_or_404(self, uuid: UUID) -> FundPendingDeposit:
        row = self.db.query(FundPendingDeposit).filter(FundPendingDeposit.uuid == str(uuid)).first()
        if row is None:
            raise QuoteServiceError("not_found", f"Depósito pendiente {uuid} no encontrado", 404)
        return row

    def confirm(
        self,
        uuid: UUID,
        deposit_method: str,
        recorded_by_user_id: int,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        user_uuid: Optional[UUID] = None,
        reference: Optional[str] = None,
        notes: Optional[str] = None,
        override_duplicate: bool = False,
    ) -> dict:
        row = self._get_or_404(uuid)
        if row.status != FundPendingDepositStatus.PENDING:
            raise QuoteServiceError("already_resolved", f"El depósito ya está {row.status.value}", 409)

        # Duplicado de un entrante: ese dinero ya entró al fondo como pata USD de un cambio.
        # Se puede forzar (el operador ve el comprobante y decide) pero nunca por defecto.
        if row.source_incoming_payment_id is not None and not override_duplicate:
            src = row.source_incoming_payment
            raise QuoteServiceError(
                "duplicate_of_incoming",
                f"Este comprobante coincide con el pago entrante #{row.source_incoming_payment_id}"
                + (f" de {src.client_phone}" if src else "")
                + ": ya está contabilizado como pago del cliente. Recházalo o confírmalo forzado "
                  "si de verdad es una reposición aparte.",
                409,
            )

        final_amount = amount if amount is not None else row.amount
        final_currency = (currency or row.currency)
        if not final_amount or final_amount <= 0 or not final_currency:
            raise QuoteServiceError(
                "missing_fields", "Faltan monto/moneda para confirmar el depósito", 400
            )

        # Depositante: el usuario indicado, o el gestor detectado.
        depositor_id = row.detected_user_id
        if user_uuid is not None:
            user = self.db.query(User).filter(User.uuid == str(user_uuid)).first()
            if user is None:
                raise QuoteServiceError("user_not_found", "Usuario depositante no encontrado", 404)
            depositor_id = user.id
        if depositor_id is None:
            raise QuoteServiceError(
                "missing_depositor", "No se pudo determinar el depositante (envía user_uuid)", 400
            )

        movement = self.fund_repo.create_movement(
            group_id=row.group_id,
            user_id=depositor_id,
            movement_type=FundMovementType.DEPOSIT,
            amount=final_amount,
            currency=final_currency,
            movement_date=datetime.now(timezone.utc),
            reference=reference or row.reference,
            notes=notes,
            recorded_by_user_id=recorded_by_user_id,
            deposit_method=deposit_method.upper(),
        )

        row.status = FundPendingDepositStatus.CONFIRMED
        row.confirmed_movement_id = movement.id
        row.resolved_by_user_id = recorded_by_user_id
        row.resolved_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return row.dict()

    def reject(self, uuid: UUID, resolved_by_user_id: int) -> dict:
        row = self._get_or_404(uuid)
        if row.status != FundPendingDepositStatus.PENDING:
            raise QuoteServiceError("already_resolved", f"El depósito ya está {row.status.value}", 409)
        row.status = FundPendingDepositStatus.REJECTED
        row.resolved_by_user_id = resolved_by_user_id
        row.resolved_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return row.dict()

    # ---------- Helpers ----------

    def _find_duplicate_incoming(
        self,
        amount: Optional[float],
        currency: Optional[str],
        reference: Optional[str],
    ) -> Optional[int]:
        """
        Busca el pago ENTRANTE que este comprobante estaría duplicando. El caso real: el gestor
        reenvía al grupo el Zelle de un cliente — dinero que ya entró al fondo por la operación,
        no una reposición. La referencia es la señal fuerte; sin ella, monto exacto + ventana.
        """
        q = self.db.query(WhatsAppIncomingPayment)
        if reference:
            match = q.filter(WhatsAppIncomingPayment.reference == reference).first()
            if match is not None:
                return match.id
        if amount is None or amount <= 0:
            return None
        since = datetime.now(timezone.utc) - DUPLICATE_WINDOW
        q = q.filter(
            WhatsAppIncomingPayment.amount == amount,
            WhatsAppIncomingPayment.created_at >= since,
        )
        if currency:
            q = q.filter(WhatsAppIncomingPayment.currency == currency.upper())
        match = q.order_by(WhatsAppIncomingPayment.created_at.desc()).first()
        return match.id if match else None

    def _resolve_group(self, group_jid: Optional[str], group_uuid: Optional[UUID]) -> FundGroup:
        group = None
        if group_uuid is not None:
            group = self.db.query(FundGroup).filter(FundGroup.uuid == str(group_uuid)).first()
        elif group_jid:
            group = self.db.query(FundGroup).filter(FundGroup.whatsapp_group_jid == group_jid).first()
        if group is None:
            raise QuoteServiceError(
                "fund_group_not_found", f"Fondo para {group_uuid or group_jid} no encontrado", 404
            )
        return group
