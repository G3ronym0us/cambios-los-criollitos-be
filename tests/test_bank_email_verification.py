"""
Verificación de un pago contra los correos: confirmación inmediata, reintentos y escalera
(app/services/bank_email_service.py).

Integración contra Postgres. El reloj entra siempre por parámetro `now`, así que la
escalera de una hora se prueba en milisegundos.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.bank_email import (
    BankEmailNotification,
    BankEmailVerification,
    BankEmailVerificationStatus,
)
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.services.bank_email_service import BankEmailService

NOW = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)


class Notifier:
    """Notificador de mentira. `ok=False` simula el bot caído."""

    def __init__(self, ok: bool = True):
        self.sent: list[str] = []
        self.ok = ok

    def __call__(self, text: str) -> bool:
        if self.ok:
            self.sent.append(text)
        return self.ok


@pytest.fixture
def payment(db):
    p = WhatsAppIncomingPayment(
        client_phone="584121234567",
        provider="zelle",
        amount=30.0,
        currency="ZELLE",
        raw_text="captura",
        created_at=NOW - timedelta(minutes=10),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def add_notification(db, *, amount="30.00", minutes_ago=5, label="Jean", message_id="<m1@boa>"):
    n = BankEmailNotification(
        message_id=message_id,
        mailbox_label=label,
        mailbox_email="azocarjean98@gmail.com",
        bank="BANK_OF_AMERICA",
        sender_name="Carlos R Barrientos",
        amount=Decimal(amount),
        currency="USD",
        received_at=NOW - timedelta(minutes=minutes_ago),
        subject="Carlos R Barrientos le envió $30.00",
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


# ---------- request_verification ----------

def test_confirma_al_instante_si_el_correo_ya_esta(db, payment):
    notification = add_notification(db)

    result = BankEmailService(db).request_verification(payment.id, now=NOW)

    assert result["status"] == "confirmed"
    assert "Carlos R Barrientos" in result["message"]
    assert "Jean" in result["message"]

    db.refresh(notification)
    assert notification.consumed_by_payment_id == payment.id

    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.CONFIRMED
    assert v.matched_notification_id == notification.id


def test_queda_pendiente_si_no_hay_correo(db, payment):
    result = BankEmailService(db).request_verification(payment.id, now=NOW)

    assert result["status"] == "pending"
    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.PENDING
    assert v.escalation_step == 0
    assert v.next_notify_at == NOW + timedelta(minutes=5)


def test_no_verifica_un_pago_sin_monto(db):
    p = WhatsAppIncomingPayment(
        client_phone="584121234567", provider="zelle", amount=None,
        currency="ZELLE", raw_text="captura",
    )
    db.add(p)
    db.commit()

    result = BankEmailService(db).request_verification(p.id, now=NOW)

    assert result["status"] == "skipped"
    assert db.query(BankEmailVerification).count() == 0


def test_reenviar_dos_veces_no_consume_otro_correo(db, payment):
    add_notification(db)
    add_notification(db, message_id="<m2@boa>", minutes_ago=3)
    service = BankEmailService(db)

    first = service.request_verification(payment.id, now=NOW)
    second = service.request_verification(payment.id, now=NOW + timedelta(minutes=1))

    assert first["status"] == "confirmed"
    assert second["status"] == "confirmed"
    consumed = db.query(BankEmailNotification).filter(
        BankEmailNotification.consumed_by_payment_id.isnot(None)
    ).count()
    assert consumed == 1


def test_dos_pagos_del_mismo_monto_no_comparten_correo(db, payment):
    add_notification(db)
    otro = WhatsAppIncomingPayment(
        client_phone="584129999999", provider="zelle", amount=30.0,
        currency="ZELLE", raw_text="captura2", created_at=NOW - timedelta(minutes=5),
    )
    db.add(otro)
    db.commit()
    db.refresh(otro)
    service = BankEmailService(db)

    assert service.request_verification(payment.id, now=NOW)["status"] == "confirmed"
    assert service.request_verification(otro.id, now=NOW)["status"] == "pending"


def test_avisa_cuando_habia_dos_correos_candidatos(db, payment):
    add_notification(db, minutes_ago=8, message_id="<m1@boa>")
    add_notification(db, minutes_ago=4, message_id="<m2@boa>")

    result = BankEmailService(db).request_verification(payment.id, now=NOW)

    assert result["status"] == "confirmed"
    assert "2 correos" in result["message"]


# ---------- resolve_pending ----------

def test_resolve_confirma_cuando_llega_el_correo(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    add_notification(db, minutes_ago=0)
    notifier = Notifier()

    messages = service.resolve_pending(now=NOW + timedelta(minutes=3), notify=notifier)

    assert len(messages) == 1
    assert "confirmado" in messages[0].lower()
    assert notifier.sent == messages
    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.CONFIRMED


def test_resolve_no_dice_nada_si_sigue_sin_correo(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    assert service.resolve_pending(now=NOW + timedelta(minutes=3), notify=Notifier()) == []


def test_si_el_aviso_no_sale_la_confirmacion_se_reintenta(db, payment):
    # Bot caído: no se puede dar por confirmada una verificación cuyo aviso nunca llegó,
    # porque el aviso es el producto entero de esta feature.
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    add_notification(db, minutes_ago=0)

    caido = Notifier(ok=False)
    assert service.resolve_pending(now=NOW + timedelta(minutes=3), notify=caido) == []

    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.PENDING
    assert db.query(BankEmailNotification).one().consumed_by_payment_id is None

    # La vuelta siguiente, con el bot de vuelta, confirma igual.
    bueno = Notifier()
    messages = service.resolve_pending(now=NOW + timedelta(minutes=4), notify=bueno)
    assert len(messages) == 1
    assert db.query(BankEmailVerification).one().status == BankEmailVerificationStatus.CONFIRMED


# ---------- escalate_pending ----------

def test_no_avisa_antes_del_primer_escalon(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    assert service.escalate_pending(now=NOW + timedelta(minutes=4), notify=Notifier()) == []


def test_avisa_a_los_cinco_minutos_y_avanza(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    messages = service.escalate_pending(now=NOW + timedelta(minutes=5), notify=Notifier())

    assert len(messages) == 1 and "5 min" in messages[0]
    v = db.query(BankEmailVerification).one()
    assert v.escalation_step == 1
    assert v.next_notify_at == NOW + timedelta(minutes=15)
    assert v.status == BankEmailVerificationStatus.PENDING


def test_si_el_aviso_no_sale_el_escalon_no_avanza(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    assert service.escalate_pending(now=NOW + timedelta(minutes=5), notify=Notifier(ok=False)) == []

    v = db.query(BankEmailVerification).one()
    assert v.escalation_step == 0
    assert v.next_notify_at == NOW + timedelta(minutes=5)


def test_a_la_hora_cierra_la_verificacion(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    for minutes in (5, 15, 30):
        service.escalate_pending(now=NOW + timedelta(minutes=minutes), notify=Notifier())

    messages = service.escalate_pending(now=NOW + timedelta(minutes=60), notify=Notifier())

    assert len(messages) == 1 and "SIN CONFIRMAR" in messages[0]
    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.NOT_FOUND
    assert v.resolved_at is not None


def test_no_sigue_avisando_despues_de_cerrar(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    for minutes in (5, 15, 30, 60):
        service.escalate_pending(now=NOW + timedelta(minutes=minutes), notify=Notifier())

    assert service.escalate_pending(now=NOW + timedelta(minutes=120), notify=Notifier()) == []


# ---------- congelado ----------

def test_buzon_caido_congela_la_escalera(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    frozen = service.freeze_pending(now=NOW, minutes=10)

    assert frozen == 1
    # Con el buzón caído, a los 5 min NO se acusa al pago de no existir.
    assert service.escalate_pending(now=NOW + timedelta(minutes=5), notify=Notifier()) == []


def test_al_descongelarse_vuelve_a_avisar(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    service.freeze_pending(now=NOW, minutes=10)

    messages = service.escalate_pending(now=NOW + timedelta(minutes=11), notify=Notifier())

    assert len(messages) == 1
