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
    FundMovement,
    FundMovementType,
    FundPendingDeposit,
    FundPendingDepositOrigin,
    FundPendingDepositStatus,
)
from app.models.user import User
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
from app.repositories.fund_repository import FundRepository
from app.services.fund_channel import resolve_fund_channel
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
        manager_phone: Optional[str] = None,
        detected_phone: Optional[str] = None,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        provider: Optional[str] = None,
        reference: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> dict:
        group = self._resolve_group(group_jid, group_uuid, manager_phone)
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
        #
        # Dos casos se ven idénticos en la base —un entrante suelto que coincide— y sólo los
        # separa QUIÉN lo afirmó:
        #   * origen GROUP/MANUAL: nadie afirmó nada. El gestor reenvió al grupo el Zelle de un
        #     cliente y el bot lo detectó; ese dinero va a ser la pata de un cambio aunque
        #     todavía no tenga operación. Se frena, como siempre.
        #   * origen RECEIPT: el operador abrió UN comprobante concreto y dijo que ése es el
        #     depósito. Ahí no hay nada que adivinar y pedirle que fuerce sobra.
        # Lo que ya está contado —tiene operación o ya movió el fondo— frena en los dos casos:
        # ninguna afirmación puede justificar contar el mismo dinero dos veces.
        ya_contado = (
            row.source_incoming_payment_id is not None
            and self._incoming_is_already_counted(row.source_incoming_payment_id)
        )
        afirmado_sobre_el_comprobante = row.origin == FundPendingDepositOrigin.RECEIPT
        if (
            row.source_incoming_payment_id is not None
            and not override_duplicate
            and (ya_contado or not afirmado_sobre_el_comprobante)
        ):
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

    def _incoming_is_already_counted(self, incoming_id: int) -> bool:
        """
        ¿Ese entrante ya está contado en algún sitio? Lo está si cuelga de una operación o si
        ya movió un fondo. Suelto en la bandeja no cuenta como contado: es dinero que entró y
        que nadie ha atribuido todavía.
        """
        row = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(WhatsAppIncomingPayment.id == incoming_id)
            .first()
        )
        if row is None:
            return False
        if row.whatsapp_operation_id is not None:
            return True
        return (
            self.db.query(FundMovement)
            .filter(FundMovement.incoming_payment_id == incoming_id)
            .first()
            is not None
        )

    def suggest_for_receipt(self, table: str, payment_id: int) -> dict:
        """
        Qué fondo y qué gestor proponer para el depósito de este comprobante.

        Nada se adivina: las dos salen de datos que ya existen.
          * **El fondo** sale del CANAL — la conversación donde llegó el comprobante—, con la
            misma `resolve_fund_channel` del reenvío contable. Un chat de cliente no resuelve a
            ningún fondo y entonces no se propone nada.
          * **El gestor** sale de QUIÉN LO MANDÓ. En un grupo WhatsApp da el autor y el bot ya
            lo guarda; en un chat directo no hay autor aparte, pero la dirección lo dice: un
            entrante lo mandó el dueño del chat, un saliente lo mandó el operador.

        Las dos son propuestas, no candados: el pago 4928 llegó por el chat de Dionis y su
        depósito es de Diohandres.
        """
        payment = self._get_payment_or_404(table, payment_id)
        canal = payment.client_phone or ""
        grupo = None
        try:
            grupo = resolve_fund_channel(
                self.db,
                group_jid=canal if canal.endswith("@g.us") else None,
                manager_phone=None if canal.endswith("@g.us") else canal,
            )
        except QuoteServiceError:
            grupo = None

        gestor = None
        if grupo is not None:
            miembros = [m for m in grupo.members if m.user is not None]
            if table == "incoming" and not canal.endswith("@g.us"):
                # Lo mandó el dueño del chat.
                gestor = next((m.user for m in miembros if m.whatsapp_phone == canal), None)
            elif table == "outgoing" and not canal.endswith("@g.us"):
                # Lo mandó el operador: el gestor cuyo teléfono NO es el de este chat.
                otros = [m.user for m in miembros if m.whatsapp_phone != canal and m.is_fund_manager]
                gestor = otros[0] if len(otros) == 1 else None

        return {
            "payment_id": payment.id,
            "table": table,
            "amount": payment.amount,
            "currency": payment.currency,
            "provider": payment.provider,
            "reference": payment.reference,
            "fund_group_uuid": grupo.uuid if grupo else None,
            "fund_group_name": grupo.name if grupo else None,
            "fund_currency": grupo.currency if grupo else None,
            "user_uuid": gestor.uuid if gestor else None,
            "username": gestor.username if gestor else None,
            "members": [
                {"user_uuid": m.user.uuid, "username": m.user.username}
                for m in (grupo.members if grupo else [])
                if m.user is not None
            ],
        }

    def create_from_receipt(
        self,
        table: str,
        payment_id: int,
        group_uuid: UUID,
        user_uuid: UUID,
        created_by_user_id: int,
    ) -> dict:
        """
        El comprobante ES el depósito: queda PENDING con monto, moneda y referencia sacados de
        él, y él enganchado como evidencia. Se confirma en Fondos como cualquier otro.
        """
        payment = self._get_payment_or_404(table, payment_id)
        if not payment.amount or payment.amount <= 0:
            raise QuoteServiceError(
                "missing_fields",
                "El comprobante no tiene monto legible: corrígelo en Pagos antes de registrarlo",
                400,
            )
        group = self._resolve_group(None, group_uuid)
        user = self.db.query(User).filter(User.uuid == str(user_uuid)).first()
        if user is None:
            raise QuoteServiceError("user_not_found", "Gestor no encontrado", 404)

        row = FundPendingDeposit(
            group_id=group.id,
            detected_user_id=user.id,
            amount=payment.amount,
            currency=(payment.currency or group.currency or "").upper() or None,
            provider=payment.provider,
            reference=payment.reference,
            raw_text=payment.raw_text,
            status=FundPendingDepositStatus.PENDING,
            origin=FundPendingDepositOrigin.RECEIPT,
            created_by_user_id=created_by_user_id,
            source_incoming_payment_id=(
                payment.id if table == "incoming"
                else self._find_duplicate_incoming(payment.amount, payment.currency, payment.reference)
            ),
            source_outgoing_payment_id=payment.id if table == "outgoing" else None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row.dict()

    def _get_payment_or_404(self, table: str, payment_id: int):
        model = WhatsAppIncomingPayment if table == "incoming" else WhatsAppOutgoingPayment
        row = self.db.query(model).filter(model.id == payment_id).first()
        if row is None:
            raise QuoteServiceError("payment_not_found", f"Comprobante {payment_id} no encontrado", 404)
        return row

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

    def _resolve_group(
        self,
        group_jid: Optional[str],
        group_uuid: Optional[UUID],
        manager_phone: Optional[str] = None,
    ) -> FundGroup:
        """El fondo al que pertenece un depósito. La regla vive en `fund_channel`."""
        return resolve_fund_channel(self.db, group_jid, group_uuid, manager_phone)
