"""
Confirmación de Zelle contra los correos de los bancos: ingesta.

La ingesta no sabe nada de operaciones ni de comprobantes — solo convierte correos en
filas. La resolución (qué correo confirma qué pago) va aparte, en este mismo módulo pero
en métodos distintos, y usa las reglas puras de bank_email_matching.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.core.config import MailboxConfig
from app.models.bank_email import (
    BankEmailNotification,
    BankEmailVerification,
    BankEmailVerificationStatus,
)
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.services.bank_email_imap import fetch_recent_headers
from app.services.bank_email_matching import (
    NotificationCandidate,
    build_confirmed_message,
    build_escalation_message,
    is_final_step,
    pick_email_confirmation,
    schedule_next,
)
from app.services.bank_email_parsers import (
    RawEmailHeaders,
    authentication_ok,
    find_template,
    parse_bank_email,
)


@dataclass
class RejectedEmail:
    """Un correo que parecía un pago pero no pasó autenticación."""

    message_id: str
    text: str


class BankEmailService:
    def __init__(self, db: Session):
        self.db = db

    def ingest_headers(
        self, headers: list[RawEmailHeaders], mailbox_label: str
    ) -> tuple[int, list[RejectedEmail]]:
        """
        Guarda las notificaciones de pago que vengan en `headers`.

        Devuelve (cuántas se insertaron, rechazos para avisar). Los rechazos son los
        correos que PARECÍAN pagos pero no pasaron autenticación: callarlos dejaría al
        operador esperando confirmaciones que nunca van a llegar.
        """
        inserted = 0
        warnings: list[RejectedEmail] = []

        for raw in headers:
            parsed = parse_bank_email(raw, mailbox_label)
            if parsed is None:
                continue

            template = find_template(raw.from_addr)
            if template is None or not authentication_ok(raw.auth_results, template.auth_domain):
                warnings.append(
                    RejectedEmail(
                        message_id=parsed.message_id,
                        text=(
                            f"⚠️ Correo de pago descartado por autenticación en la cuenta de "
                            f"{mailbox_label} (de {raw.from_addr}): {raw.subject}"
                        ),
                    )
                )
                continue

            exists = (
                self.db.query(BankEmailNotification.id)
                .filter(BankEmailNotification.message_id == parsed.message_id)
                .first()
            )
            if exists:
                continue

            self.db.add(
                BankEmailNotification(
                    message_id=parsed.message_id,
                    mailbox_label=parsed.mailbox_label,
                    mailbox_email=parsed.mailbox_email,
                    bank=parsed.bank,
                    sender_name=parsed.sender_name,
                    amount=parsed.amount,
                    currency=parsed.currency,
                    received_at=parsed.received_at,
                    subject=parsed.subject,
                    auth_result=parsed.auth_result,
                )
            )
            inserted += 1

        if inserted or warnings:
            self.db.commit()
        return inserted, warnings

    def ingest_mailbox(self, box: MailboxConfig) -> tuple[int, list[RejectedEmail]]:
        """Lee un buzón y lo ingiere. Propaga MailboxUnavailable a propósito."""
        headers = fetch_recent_headers(box)
        return self.ingest_headers(headers, mailbox_label=box.label)

    # ---------- Verificación ----------

    def request_verification(self, payment_id: int, *, now: datetime) -> dict:
        """
        Arranca (o devuelve) la verificación por correo de un pago entrante.

        Idempotente: reenviar dos veces la misma captura no consume un segundo correo.
        """
        payment = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(WhatsAppIncomingPayment.id == payment_id)
            .first()
        )
        if payment is None or payment.amount is None:
            return {"status": "skipped", "message": None}

        existing = (
            self.db.query(BankEmailVerification)
            .filter(BankEmailVerification.incoming_payment_id == payment_id)
            .first()
        )
        if existing is not None and existing.status != BankEmailVerificationStatus.PENDING:
            return {
                "status": existing.status.value.lower(),
                "message": self._confirmed_message_for(existing, now=now),
            }

        amount = Decimal(str(payment.amount))
        verification = existing or BankEmailVerification(
            incoming_payment_id=payment_id,
            amount=amount,
            status=BankEmailVerificationStatus.PENDING,
            requested_at=now,
            escalation_step=0,
            next_notify_at=schedule_next(0, now),
        )
        if existing is None:
            self.db.add(verification)

        message = self._try_confirm(verification, payment, now=now)
        self.db.commit()

        if message is not None:
            return {"status": "confirmed", "message": message}
        return {"status": "pending", "message": None}

    def resolve_pending(self, *, now: datetime, notify: Callable[[str], bool]) -> list[str]:
        """
        Reevalúa las pendientes contra los correos ingeridos.

        El aviso se manda ANTES de commitear: si no sale, se revierte y la vuelta
        siguiente lo reintenta idéntico. Confirmar una verificación cuyo aviso nunca
        llegó es perder el producto entero de la feature.
        """
        delivered: list[str] = []
        pendings = (
            self.db.query(BankEmailVerification)
            .filter(BankEmailVerification.status == BankEmailVerificationStatus.PENDING)
            .all()
        )
        for verification in pendings:
            payment = (
                self.db.query(WhatsAppIncomingPayment)
                .filter(WhatsAppIncomingPayment.id == verification.incoming_payment_id)
                .first()
            )
            if payment is None:
                continue
            message = self._try_confirm(verification, payment, now=now)
            if message is None:
                continue
            if notify(message):
                self.db.commit()
                delivered.append(message)
            else:
                self.db.rollback()
        return delivered

    def escalate_pending(self, *, now: datetime, notify: Callable[[str], bool]) -> list[str]:
        """
        Manda el aviso del escalón que toque y avanza. Cierra en el último.

        Igual que `resolve_pending`: el escalón solo avanza si el aviso salió.
        """
        delivered: list[str] = []
        pendings = (
            self.db.query(BankEmailVerification)
            .filter(
                BankEmailVerification.status == BankEmailVerificationStatus.PENDING,
                BankEmailVerification.next_notify_at.isnot(None),
                BankEmailVerification.next_notify_at <= now,
            )
            .all()
        )
        for verification in pendings:
            # El buzón caído congela: nunca se declara "no confirmado" sin haber podido mirar.
            if verification.frozen_until is not None and verification.frozen_until > now:
                continue

            payment = (
                self.db.query(WhatsAppIncomingPayment)
                .filter(WhatsAppIncomingPayment.id == verification.incoming_payment_id)
                .first()
            )
            phone = payment.client_phone if payment else "?"
            step = verification.escalation_step
            message = build_escalation_message(
                step, amount=verification.amount, client_phone=phone
            )
            if not notify(message):
                self.db.rollback()
                continue

            if is_final_step(step):
                verification.status = BankEmailVerificationStatus.NOT_FOUND
                verification.next_notify_at = None
                verification.resolved_at = now
            else:
                verification.escalation_step = step + 1
                verification.next_notify_at = schedule_next(step + 1, verification.requested_at)

            self.db.commit()
            delivered.append(message)

        return delivered

    def freeze_pending(self, *, now: datetime, minutes: int) -> int:
        """Congela la escalera de todas las pendientes. Devuelve cuántas congeló."""
        pendings = (
            self.db.query(BankEmailVerification)
            .filter(BankEmailVerification.status == BankEmailVerificationStatus.PENDING)
            .all()
        )
        for verification in pendings:
            verification.frozen_until = now + timedelta(minutes=minutes)
        if pendings:
            self.db.commit()
        return len(pendings)

    # ---------- Internos ----------

    def _try_confirm(
        self, verification: BankEmailVerification, payment: WhatsAppIncomingPayment, *, now: datetime
    ) -> Optional[str]:
        """Busca un correo que confirme el pago. Si lo hay, lo consume y devuelve el aviso."""
        rows = (
            self.db.query(BankEmailNotification)
            .filter(BankEmailNotification.consumed_by_payment_id.is_(None))
            .with_for_update()
            .all()
        )
        candidates = [
            NotificationCandidate(
                id=r.id,
                amount=r.amount,
                received_at=r.received_at,
                mailbox_label=r.mailbox_label,
                sender_name=r.sender_name or "",
                bank=r.bank,
            )
            for r in rows
        ]
        created_at = payment.created_at or verification.requested_at
        chosen, count = pick_email_confirmation(
            candidates,
            amount=verification.amount,
            payment_created_at=created_at,
            now=now,
        )
        if chosen is None:
            return None

        row = next(r for r in rows if r.id == chosen.id)
        row.consumed_by_payment_id = payment.id

        verification.status = BankEmailVerificationStatus.CONFIRMED
        verification.matched_notification_id = chosen.id
        verification.next_notify_at = None
        verification.resolved_at = now

        elapsed = int((now - verification.requested_at).total_seconds() // 60)
        return build_confirmed_message(
            chosen, amount=verification.amount, minutes_elapsed=elapsed, ambiguity_count=count
        )

    def _confirmed_message_for(
        self, verification: BankEmailVerification, *, now: datetime
    ) -> Optional[str]:
        """Rearma el aviso de una verificación ya confirmada, sin consumir nada."""
        if verification.matched_notification_id is None:
            return None
        row = (
            self.db.query(BankEmailNotification)
            .filter(BankEmailNotification.id == verification.matched_notification_id)
            .first()
        )
        if row is None:
            return None
        candidate = NotificationCandidate(
            id=row.id, amount=row.amount, received_at=row.received_at,
            mailbox_label=row.mailbox_label, sender_name=row.sender_name or "", bank=row.bank,
        )
        elapsed = int(((verification.resolved_at or now) - verification.requested_at).total_seconds() // 60)
        return build_confirmed_message(
            candidate, amount=verification.amount, minutes_elapsed=elapsed, ambiguity_count=1
        )
