"""
Servicio de depósitos PENDIENTES detectados por el bot cuando un gestor del fondo
(FundGroupMember.is_fund_manager) sube un comprobante al grupo de WhatsApp. El operador
los confirma/rechaza desde `/admin/funds`. Al confirmar se materializa un FundMovement
DEPOSIT (reusa FundRepository.create_movement); el balance del fondo solo cuenta movimientos.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.fund import (
    FundGroup,
    FundGroupMember,
    FundMovementType,
    FundPendingDeposit,
    FundPendingDepositStatus,
)
from app.models.user import User
from app.repositories.fund_repository import FundRepository
from app.services.whatsapp_quote_service import QuoteServiceError


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
    ) -> dict:
        row = self._get_or_404(uuid)
        if row.status != FundPendingDepositStatus.PENDING:
            raise QuoteServiceError("already_resolved", f"El depósito ya está {row.status.value}", 409)

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
