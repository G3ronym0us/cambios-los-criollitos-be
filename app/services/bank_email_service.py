"""
Confirmación de Zelle contra los correos de los bancos: ingesta.

La ingesta no sabe nada de operaciones ni de comprobantes — solo convierte correos en
filas. La resolución (qué correo confirma qué pago) va aparte, en este mismo módulo pero
en métodos distintos, y usa las reglas puras de bank_email_matching.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import MailboxConfig
from app.models.bank_email import BankEmailNotification
from app.services.bank_email_imap import fetch_recent_headers
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
