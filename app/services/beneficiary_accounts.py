"""
Helpers puros de la libreta de cuentas por cliente (beneficiarios con nombre).

Sin DB y sin FastAPI a propósito: la regla de emparejamiento de nombres y el armado del
bloque de datos son lo único con riesgo real de este feature (pagarle a la persona
equivocada), así que viven aparte y se prueban solos.
"""

import re
import unicodedata
from typing import Optional

# Prefijos de cédula venezolana. Si la identificación no trae uno, se asume V.
_ID_PREFIXES = ("V", "E", "J")

# Cuenta tapada de un comprobante: `0102****3817` (banco + últimos 4). Cada banco enmascara
# con un carácter distinto y el OCR lo lee como puede, de ahí la clase amplia; exigir 2 o más
# y cuatro dígitos a cada lado es lo que evita enganchar un monto o una fecha.
_MASKED_ACCOUNT = re.compile(r"(?<!\d)(\d{4})[ \t]*[*xX•·●#.]{2,}[ \t]*(\d{4})(?!\d)")
_DEST_LABEL = re.compile(r"destino|destinatario|receptor|beneficiari[oa]", re.IGNORECASE)
_ACCOUNT_20 = re.compile(r"(?<!\d)(\d{20})(?!\d)")


def normalize_alias(raw: Optional[str]) -> Optional[str]:
    """Minúsculas, sin diacríticos, espacios colapsados. None si queda vacío."""
    if raw is None:
        return None
    stripped = unicodedata.normalize("NFD", raw)
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    collapsed = re.sub(r"\s+", " ", stripped).strip().lower()
    return collapsed or None


def alias_matches(query_norm: str, alias_norm: Optional[str]) -> bool:
    """
    Tolerante pero por palabra completa: coincide si los tokens de la consulta son
    subconjunto de los del alias, o al revés. "yelitza" ↔ "yelitza bolivar" coincide en
    ambas direcciones; "yelitza perez" vs "yelitza bolivar" no.
    """
    if not query_norm or not alias_norm:
        return False
    q = set(query_norm.split())
    a = set(alias_norm.split())
    if not q or not a:
        return False
    return q.issubset(a) or a.issubset(q)


def _with_id_prefix(identification: str) -> str:
    ident = identification.strip().upper().replace("-", "").replace(".", "")
    if ident[:1] in _ID_PREFIXES:
        return ident
    return f"V{ident}"


def build_payment_block(
    account_number: Optional[str],
    identification: Optional[str],
    phone_to: Optional[str],
    bank_to: Optional[str],
) -> Optional[str]:
    """
    Bloque copiar-y-pegar a partir de los campos estructurados de un pago saliente.
    Espeja `formatPaymentInfo()` del bot (`whatsapp-bot/src/banks.ts`):

    - Transferencia: cuenta de 20 dígitos + cédula.
    - Pago móvil: código de banco + cédula + teléfono.

    None si los campos no alcanzan ninguna de las dos formas: es preferible no aprender
    nada a guardar datos con los que no se puede pagar.
    """
    ident = identification.strip() if identification else None
    if account_number and ident:
        return f"{account_number.strip()}\n{_with_id_prefix(ident)}"
    if bank_to and ident and phone_to:
        return f"{bank_to.strip()}\n{_with_id_prefix(ident)}\n{phone_to.strip()}"
    return None


def extract_masked_destination(raw_text: Optional[str]) -> Optional[tuple[str, str]]:
    """
    Banco y últimos 4 dígitos de la cuenta DESTINO cuando el comprobante la tapa
    (`Destino: 0102****3817`). Con eso no se puede pagar, pero sí reconocer una cuenta ya
    guardada.

    Sólo se acepta la máscara que está en la misma línea que la etiqueta de destino. El
    comprobante trae dos cuentas tapadas —origen y destino— y si el OCR parte la tabla en
    columnas ("Origen:\\nDestino:\\n0102****6476\\n0102****3817") cualquier regla que mire
    la línea siguiente devuelve la del origen, que es la cuenta del propio cliente. Ante esa
    duda no se devuelve nada: perder el vínculo cuesta menos que atribuirlo mal.
    """
    if not raw_text:
        return None
    for line in raw_text.splitlines():
        if not _DEST_LABEL.search(line):
            continue
        match = _MASKED_ACCOUNT.search(line)
        if match:
            return match.group(1), match.group(2)
    return None


def masked_matches_account(masked: tuple[str, str], payment_info: Optional[str]) -> bool:
    """
    ¿El bloque guardado contiene la cuenta que el comprobante dejó tapada? Se comparan banco
    y últimos 4 dígitos, que es todo lo que se ve. El número de 20 dígitos se busca en todo
    el bloque, no sólo en la primera línea: los bloques que mandó el cliente por texto traen
    banco y nombre mezclados en cualquier orden.
    """
    if not payment_info:
        return False
    bank, last4 = masked
    return any(
        account.startswith(bank) and account.endswith(last4)
        for account in _ACCOUNT_20.findall(payment_info)
    )
