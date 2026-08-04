"""
Conversión de un mensaje de correo crudo a RawEmailHeaders
(app/services/bank_email_imap.py).

La conexión IMAP en sí no se testea: se prueba a mano con `python -m app.cli.check_mailboxes`.
Lo que sí se prueba es el parseo de cabeceras, que es donde están los detalles feos
(fechas con zona, asuntos codificados en MIME, Message-ID ausente).
"""

from datetime import timezone
from email.message import EmailMessage

from app.services.bank_email_imap import headers_from_message


def make(**kw) -> EmailMessage:
    msg = EmailMessage()
    msg["Message-ID"] = kw.get("message_id", "<abc123@bankofamerica.com>")
    msg["Subject"] = kw.get("subject", "Carlos R Barrientos le envió $30.00")
    msg["From"] = kw.get("from_", "Bank of America <customerservice@ealerts.bankofamerica.com>")
    msg["To"] = kw.get("to", "azocarjean98@gmail.com")
    msg["Date"] = kw.get("date", "Tue, 4 Aug 2026 14:01:03 -0400")
    if kw.get("auth") is not False:
        msg["Authentication-Results"] = kw.get("auth", "mx.google.com; dkim=pass header.i=@bankofamerica.com")
    return msg


def test_extrae_las_cabeceras_basicas():
    raw = headers_from_message(make(), fallback_to="azocarjean98@gmail.com")
    assert raw is not None
    assert raw.message_id == "<abc123@bankofamerica.com>"
    assert raw.subject == "Carlos R Barrientos le envió $30.00"
    assert raw.to_addr == "azocarjean98@gmail.com"


def test_extrae_solo_la_direccion_del_from():
    # El From viene como 'Nombre <dir@dominio>'; la plantilla compara contra la dirección sola.
    raw = headers_from_message(make(), fallback_to="azocarjean98@gmail.com")
    assert raw.from_addr == "customerservice@ealerts.bankofamerica.com"


def test_normaliza_la_fecha_a_utc():
    raw = headers_from_message(make(), fallback_to="x@y.com")
    assert raw.received_at.tzinfo is not None
    assert raw.received_at.utcoffset().total_seconds() == 0
    # 14:01 -0400 == 18:01 UTC
    assert raw.received_at.astimezone(timezone.utc).hour == 18


def test_decodifica_asunto_codificado_en_mime():
    encoded = "=?UTF-8?Q?Carlos_R_Barrientos_le_envi=C3=B3_=2430=2E00?="
    raw = headers_from_message(make(subject=encoded), fallback_to="x@y.com")
    assert raw.subject == "Carlos R Barrientos le envió $30.00"


def test_usa_el_buzon_como_to_si_falta_la_cabecera():
    # Gmail a veces entrega el correo sin To visible (BCC, alias).
    msg = make()
    del msg["To"]
    raw = headers_from_message(msg, fallback_to="azocarjean98@gmail.com")
    assert raw.to_addr == "azocarjean98@gmail.com"


def test_sin_authentication_results_devuelve_cadena_vacia():
    raw = headers_from_message(make(auth=False), fallback_to="x@y.com")
    assert raw.auth_results == ""


def test_sin_message_id_se_descarta():
    # Sin Message-ID no hay idempotencia posible: se prefiere perder el correo a duplicarlo.
    msg = make()
    del msg["Message-ID"]
    assert headers_from_message(msg, fallback_to="x@y.com") is None


def test_sin_fecha_se_descarta():
    msg = make()
    del msg["Date"]
    assert headers_from_message(msg, fallback_to="x@y.com") is None
