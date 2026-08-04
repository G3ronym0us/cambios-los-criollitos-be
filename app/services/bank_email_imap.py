"""
Único lugar del backend que habla IMAP.

Se trae SOLO cabeceras y con BODY.PEEK: `PEEK` es obligatorio porque no marca los correos
como leídos — el operador sigue viendo su bandeja igual que siempre.

Todo lo demás (parseo del asunto, autenticidad, matching) vive en módulos puros que se
prueban sin red. Acá solo hay I/O y conversión de cabeceras.
"""

import email
import imaplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr
from typing import Optional

from app.core.config import MailboxConfig
from app.services.bank_email_parsers import RawEmailHeaders

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_TIMEOUT_SECONDS = 20


class MailboxUnavailable(Exception):
    """No se pudo leer el buzón (credenciales, red, Gmail caído)."""


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def headers_from_message(msg, fallback_to: str) -> Optional[RawEmailHeaders]:
    """
    Convierte un mensaje ya parseado en RawEmailHeaders, o None si le falta lo mínimo.

    Sin Message-ID no hay idempotencia posible, y sin Date no hay ventana: en ambos casos
    se prefiere descartar el correo a ingerir basura.
    """
    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        return None

    raw_date = msg.get("Date")
    if not raw_date:
        return None
    try:
        received_at = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return None
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    received_at = received_at.astimezone(timezone.utc)

    _, from_addr = parseaddr(msg.get("From") or "")
    _, to_addr = parseaddr(msg.get("To") or "")

    return RawEmailHeaders(
        message_id=message_id,
        subject=_decode(msg.get("Subject")),
        from_addr=from_addr.lower(),
        to_addr=(to_addr or fallback_to).lower(),
        received_at=received_at,
        auth_results=_decode(msg.get("Authentication-Results")),
    )


def fetch_recent_headers(box: MailboxConfig, *, since_days: int = 1) -> list[RawEmailHeaders]:
    """
    Cabeceras de los correos recientes de INBOX. Nunca toca Spam ni marca como leído.

    Lanza MailboxUnavailable si el buzón no se pudo leer: el llamador tiene que poder
    distinguir "no llegó el correo" de "no pude mirar".
    """
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
        conn.login(box.email, box.password)
        conn.select("INBOX", readonly=True)

        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            raise MailboxUnavailable(f"SEARCH devolvió {status} en {box.label}")

        results: list[RawEmailHeaders] = []
        for num in (data[0] or b"").split():
            status, payload = conn.fetch(num, "(BODY.PEEK[HEADER])")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            msg = email.message_from_bytes(payload[0][1])
            headers = headers_from_message(msg, fallback_to=box.email)
            if headers is not None:
                results.append(headers)
        return results
    except MailboxUnavailable:
        raise
    except Exception as e:
        raise MailboxUnavailable(f"No se pudo leer el buzón de {box.label}: {e}") from e
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn.logout()
            except Exception:
                pass
