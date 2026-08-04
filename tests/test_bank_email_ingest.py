"""
Ingesta de correos a bank_email_notifications (app/services/bank_email_service.py).

Integración contra Postgres: usa las fixtures de conftest.py y se salta solo si no hay
Postgres local en :5433.
"""

from datetime import datetime, timezone
from decimal import Decimal

from app.models.bank_email import BankEmailNotification
from app.services.bank_email_parsers import RawEmailHeaders
from app.services.bank_email_service import BankEmailService

NOW = datetime(2026, 8, 4, 18, 1, tzinfo=timezone.utc)
BOA_AUTH = "mx.google.com; dkim=pass header.i=@bankofamerica.com; spf=pass"


def raw(**kw) -> RawEmailHeaders:
    base = dict(
        message_id="<abc@bankofamerica.com>",
        subject="Carlos R Barrientos le envió $30.00",
        from_addr="customerservice@ealerts.bankofamerica.com",
        to_addr="azocarjean98@gmail.com",
        received_at=NOW,
        auth_results=BOA_AUTH,
    )
    base.update(kw)
    return RawEmailHeaders(**base)


def test_inserta_una_notificacion(db):
    inserted, warnings = BankEmailService(db).ingest_headers([raw()], mailbox_label="Jean")

    assert inserted == 1
    assert warnings == []
    row = db.query(BankEmailNotification).one()
    assert row.amount == Decimal("30.00")
    assert row.sender_name == "Carlos R Barrientos"
    assert row.mailbox_label == "Jean"
    assert row.consumed_by_payment_id is None


def test_no_duplica_el_mismo_message_id(db):
    service = BankEmailService(db)
    service.ingest_headers([raw()], mailbox_label="Jean")
    inserted, _ = service.ingest_headers([raw()], mailbox_label="Jean")

    assert inserted == 0
    assert db.query(BankEmailNotification).count() == 1


def test_ignora_correos_que_no_son_notificaciones(db):
    inserted, warnings = BankEmailService(db).ingest_headers(
        [raw(subject="Your monthly statement is ready")], mailbox_label="Jean"
    )
    assert inserted == 0
    assert warnings == []  # no es un pago: ni se avisa
    assert db.query(BankEmailNotification).count() == 0


def test_descarta_y_avisa_cuando_falla_la_autenticacion(db):
    inserted, warnings = BankEmailService(db).ingest_headers(
        [raw(auth_results="dkim=fail; spf=softfail")], mailbox_label="Jean"
    )
    assert inserted == 0
    assert db.query(BankEmailNotification).count() == 0
    assert len(warnings) == 1
    assert "autenticación" in warnings[0].text
    # El message_id viaja con el aviso para poder no repetirlo cada minuto.
    assert warnings[0].message_id == "<abc@bankofamerica.com>"


def test_descarta_reenviados_sin_avisar(db):
    inserted, warnings = BankEmailService(db).ingest_headers(
        [raw(subject="Fwd: Carlos R Barrientos le envió $30.00")], mailbox_label="Jean"
    )
    assert inserted == 0
    assert warnings == []


def test_ingiere_varios_de_una(db):
    inserted, _ = BankEmailService(db).ingest_headers(
        [
            raw(),
            raw(message_id="<def@1stnb.com>", subject="Notification - Aristides Bravo sent you $107.00.",
                from_addr="custserv@1stnb.com",
                auth_results="mx.google.com; spf=pass smtp.mailfrom=1stnb.com"),
        ],
        mailbox_label="Mariana",
    )
    assert inserted == 2
