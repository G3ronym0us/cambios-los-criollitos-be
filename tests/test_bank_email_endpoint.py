"""
Endpoint que el bot llama al reenviar una captura al grupo
(POST /whatsapp/payments/incoming/{id}/verify-by-email).

Se prueba el servicio a través del schema de respuesta; la autenticación del bot ya está
cubierta por el resto de la suite.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.bank_email import BankEmailNotification
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.schemas.whatsapp import EmailVerificationResponse
from app.services.bank_email_service import BankEmailService

NOW = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)


def test_respuesta_confirmada_serializa(db):
    payment = WhatsAppIncomingPayment(
        client_phone="584121234567", provider="zelle", amount=30.0,
        currency="ZELLE", raw_text="x", created_at=NOW - timedelta(minutes=5),
    )
    db.add(payment)
    db.add(BankEmailNotification(
        message_id="<m1@boa>", mailbox_label="Jean", mailbox_email="azocarjean98@gmail.com",
        bank="BANK_OF_AMERICA", sender_name="Carlos R Barrientos", amount=Decimal("30.00"),
        currency="USD", received_at=NOW - timedelta(minutes=3), subject="s",
    ))
    db.commit()
    db.refresh(payment)

    result = BankEmailService(db).request_verification(payment.id, now=NOW)
    response = EmailVerificationResponse(**result)

    assert response.status == "confirmed"
    assert "Jean" in response.message


def test_respuesta_pendiente_serializa(db):
    payment = WhatsAppIncomingPayment(
        client_phone="584121234567", provider="zelle", amount=77.0,
        currency="ZELLE", raw_text="x", created_at=NOW,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    response = EmailVerificationResponse(**BankEmailService(db).request_verification(payment.id, now=NOW))

    assert response.status == "pending"
    assert response.message is None
