"""
Parseo y autenticidad de los correos de notificación de los bancos
(app/services/bank_email_parsers.py).

Puro, sin BD ni red: corre en cualquier lado.

Los asuntos son reales, tomados de los buzones de las cuentas alquiladas. Si un banco
cambia el formato del asunto, el caso nuevo se agrega AQUÍ antes de tocar el regex.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.bank_email_parsers import (
    RawEmailHeaders,
    authentication_ok,
    find_template,
    is_forwarded,
    parse_bank_email,
)

NOW = datetime(2026, 8, 4, 18, 1, tzinfo=timezone.utc)

BOA_AUTH = "mx.google.com; dkim=pass header.i=@bankofamerica.com; spf=pass; dmarc=pass"
FNBT_AUTH = "mx.google.com; dkim=neutral; spf=pass smtp.mailfrom=1stnb.com; dmarc=pass"


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


# ---------- find_template ----------

def test_encuentra_plantilla_de_bank_of_america():
    t = find_template("customerservice@ealerts.bankofamerica.com")
    assert t is not None and t.bank == "BANK_OF_AMERICA"


def test_encuentra_plantilla_ignorando_mayusculas():
    # FNBT manda desde "CustServ@1stnb.com"; el From llega con capitalización variable.
    t = find_template("custserv@1stnb.com")
    assert t is not None and t.bank == "FNBT"


def test_remitente_parecido_pero_falso_no_tiene_plantilla():
    # Guion en vez de punto: dominio distinto, controlado por el atacante.
    assert find_template("customerservice@ealerts-bankofamerica.com") is None


def test_remitente_desconocido_no_tiene_plantilla():
    assert find_template("noreply@paypal.com") is None


# ---------- is_forwarded ----------

@pytest.mark.parametrize("subject", [
    "Fwd: Carlos R Barrientos le envió $30.00",
    "RV: Carlos R Barrientos le envió $30.00",
    "Fw: Carlos R Barrientos le envió $30.00",
    "fwd: carlos le envió $30.00",
])
def test_detecta_reenviados(subject):
    assert is_forwarded(subject) is True


def test_asunto_normal_no_es_reenviado():
    assert is_forwarded("Carlos R Barrientos le envió $30.00") is False


# ---------- authentication_ok ----------

def test_acepta_dkim_pass_del_dominio_esperado():
    assert authentication_ok(BOA_AUTH, "bankofamerica.com") is True


def test_acepta_spf_pass_cuando_dkim_no_pasa():
    # FNBT-FCB puede no tener DKIM alineado; con SPF del dominio correcto alcanza.
    assert authentication_ok(FNBT_AUTH, "1stnb.com") is True


def test_rechaza_dkim_pass_de_otro_dominio():
    fake = "mx.google.com; dkim=pass header.i=@atacante.com; spf=pass smtp.mailfrom=atacante.com"
    assert authentication_ok(fake, "bankofamerica.com") is False


def test_rechaza_cuando_todo_falla():
    fail = "mx.google.com; dkim=fail; spf=softfail; dmarc=fail"
    assert authentication_ok(fail, "bankofamerica.com") is False


def test_rechaza_cabecera_vacia():
    assert authentication_ok("", "bankofamerica.com") is False


# ---------- parse_bank_email ----------

def test_parsea_boa_en_espanol():
    parsed = parse_bank_email(raw(), mailbox_label="Jean")
    assert parsed is not None
    assert parsed.bank == "BANK_OF_AMERICA"
    assert parsed.sender_name == "Carlos R Barrientos"
    assert parsed.amount == Decimal("30.00")
    assert parsed.currency == "USD"
    assert parsed.mailbox_label == "Jean"
    assert parsed.mailbox_email == "azocarjean98@gmail.com"


def test_parsea_boa_en_ingles():
    parsed = parse_bank_email(raw(subject="Carlos R Barrientos sent you $30.00"), mailbox_label="Jean")
    assert parsed is not None and parsed.amount == Decimal("30.00")


def test_parsea_fnbt():
    parsed = parse_bank_email(
        raw(
            subject="Notification - Aristides Bravo sent you $107.00.",
            from_addr="CustServ@1stnb.com",
            to_addr="mmendozaperez53@gmail.com",
            auth_results=FNBT_AUTH,
        ),
        mailbox_label="Mariana",
    )
    assert parsed is not None
    assert parsed.bank == "FNBT"
    assert parsed.sender_name == "Aristides Bravo"
    assert parsed.amount == Decimal("107.00")


def test_parsea_monto_con_separador_de_miles():
    parsed = parse_bank_email(raw(subject="Carlos R Barrientos le envió $1,970.00"), mailbox_label="Jean")
    assert parsed is not None and parsed.amount == Decimal("1970.00")


def test_no_parsea_reenviado():
    assert parse_bank_email(raw(subject="Fwd: Carlos R Barrientos le envió $30.00"), mailbox_label="Jean") is None


def test_no_parsea_remitente_sin_plantilla():
    assert parse_bank_email(raw(from_addr="noreply@paypal.com"), mailbox_label="Jean") is None


def test_no_parsea_asunto_que_no_es_pago():
    # Los bancos mandan mucho correo que no es una notificación de Zelle.
    assert parse_bank_email(raw(subject="Your monthly statement is ready"), mailbox_label="Jean") is None


def test_parse_no_valida_autenticidad():
    # La autenticidad se chequea aparte (authentication_ok) para que el servicio de
    # ingesta pueda avisar "descartado por autenticación" en vez de callar.
    parsed = parse_bank_email(raw(auth_results="dkim=fail; spf=fail"), mailbox_label="Jean")
    assert parsed is not None
