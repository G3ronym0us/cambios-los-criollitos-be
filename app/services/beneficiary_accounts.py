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
