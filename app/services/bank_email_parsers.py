"""
Parseo de las notificaciones de pago que mandan los bancos de las cuentas alquiladas.

Todo sale del ASUNTO y de las cabeceras: los dos bancos ponen nombre y monto ahí
("Carlos R Barrientos le envió $30.00"), y el cuerpo HTML de estos correos cambia sin
aviso. Menos superficie, menos roturas.

Agregar un banco = agregar un `BankTemplate` a TEMPLATES. Nada más.

Puro: sin BD, sin red. Lo que se pueda romper se prueba en tests/test_bank_email_parsers.py.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass
class RawEmailHeaders:
    """Lo que devuelve la capa IMAP: cabeceras crudas, sin interpretar."""

    message_id: str
    subject: str
    from_addr: str
    to_addr: str
    received_at: datetime
    auth_results: str


@dataclass
class BankTemplate:
    #: Identificador interno del banco (se guarda en la columna `bank`).
    bank: str
    #: Dirección exacta desde la que escribe el banco. Es la lista blanca.
    from_address: str
    #: Dominio que tiene que haber pasado DKIM o SPF para creerle al correo.
    auth_domain: str
    #: Debe capturar los grupos `name` y `amount`.
    subject_regex: str


@dataclass
class ParsedBankEmail:
    message_id: str
    mailbox_label: str
    mailbox_email: str
    bank: str
    sender_name: str
    amount: Decimal
    currency: str
    received_at: datetime
    subject: str
    auth_result: str


TEMPLATES: list[BankTemplate] = [
    BankTemplate(
        bank="BANK_OF_AMERICA",
        from_address="customerservice@ealerts.bankofamerica.com",
        auth_domain="bankofamerica.com",
        # El mismo buzón recibe el aviso en español o inglés según la config de la cuenta.
        subject_regex=r"^(?P<name>.+?)\s+(?:le envió|sent you)\s+\$(?P<amount>[\d,]+\.\d{2})",
    ),
    BankTemplate(
        bank="FNBT",
        from_address="custserv@1stnb.com",
        auth_domain="1stnb.com",
        subject_regex=r"^Notification\s*-\s*(?P<name>.+?)\s+sent you\s+\$(?P<amount>[\d,]+\.\d{2})",
    ),
]

#: Prefijos que delatan que alguien reenvió el correo en vez de que lo escribiera el banco.
_FORWARD_PREFIXES = ("fwd:", "fw:", "rv:")


def find_template(from_addr: str) -> Optional[BankTemplate]:
    """Plantilla del banco por dirección exacta del remitente (case-insensitive)."""
    addr = (from_addr or "").strip().lower()
    for template in TEMPLATES:
        if addr == template.from_address.lower():
            return template
    return None


def is_forwarded(subject: str) -> bool:
    return (subject or "").strip().lower().startswith(_FORWARD_PREFIXES)


def authentication_ok(auth_results: str, auth_domain: str) -> bool:
    """
    ¿Gmail verificó que este correo salió del banco?

    Se acepta DKIM o SPF, no se exigen los dos: el banco chico (FNBT-FCB) puede no tener
    DKIM alineado, y exigirlo dejaría esa cuenta sin confirmar EN SILENCIO, que es la
    peor falla posible aquí. En ambos casos el dominio que pasó tiene que ser el del banco.
    """
    text = (auth_results or "").lower()
    domain = (auth_domain or "").lower()
    if not text or not domain:
        return False

    dkim = re.search(r"dkim=pass[^;]*header\.i=@([\w.-]+)", text)
    if dkim and dkim.group(1).endswith(domain):
        return True

    spf = re.search(r"spf=pass[^;]*smtp\.mailfrom=([\w.@-]+)", text)
    if spf and spf.group(1).split("@")[-1].endswith(domain):
        return True

    return False


def parse_bank_email(raw: RawEmailHeaders, mailbox_label: str) -> Optional[ParsedBankEmail]:
    """
    Convierte cabeceras crudas en una notificación de pago, o None si no lo es.

    NO valida autenticidad a propósito: eso lo decide el llamador con `authentication_ok`,
    para poder avisar "descartado por autenticación" en vez de tragárselo.
    """
    template = find_template(raw.from_addr)
    if template is None:
        return None
    if is_forwarded(raw.subject):
        return None

    match = re.match(template.subject_regex, (raw.subject or "").strip())
    if match is None:
        return None

    amount = Decimal(match.group("amount").replace(",", ""))
    return ParsedBankEmail(
        message_id=raw.message_id,
        mailbox_label=mailbox_label,
        mailbox_email=raw.to_addr,
        bank=template.bank,
        sender_name=match.group("name").strip(),
        amount=amount,
        currency="USD",
        received_at=raw.received_at,
        subject=raw.subject,
        auth_result=raw.auth_results,
    )
