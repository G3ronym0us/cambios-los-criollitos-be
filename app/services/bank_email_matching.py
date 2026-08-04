"""
Reglas de la confirmación por correo: qué notificación confirma qué pago, cuándo se le
insiste al operador y qué dice cada aviso.

Puro y con el reloj inyectado: es la parte que puede equivocarse, así que tiene que
poder probarse sin BD, sin red y sin esperar una hora.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

#: Cuánto hacia atrás se aceptan correos respecto del comprobante. Cubre que el reenvío
#: al grupo ocurra mucho después de que el cliente pagó.
LOOKBACK_HOURS = 12

#: Minutos desde el reenvío en los que se le insiste al operador si no aparece el correo.
#: La búsqueda corre cada minuto igual; esto es solo cuándo se avisa.
ESCALATION_MINUTES = [5, 15, 30, 60]


@dataclass
class NotificationCandidate:
    id: int
    amount: Decimal
    received_at: datetime
    mailbox_label: str
    sender_name: str
    bank: str


def _fmt(amount: Decimal) -> str:
    """30.00 → '30,00' (formato venezolano, como el resto de los avisos del bot)."""
    return f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def pick_email_confirmation(
    candidates: list[NotificationCandidate],
    *,
    amount: Decimal,
    payment_created_at: datetime,
    now: datetime,
) -> tuple[Optional[NotificationCandidate], int]:
    """
    Elige el correo que confirma un pago. Devuelve (elegido, cuántos había en ventana).

    `candidates` ya viene filtrado por SQL a notificaciones SIN consumir. Acá se aplican
    monto exacto y ventana; con varios se toma el más antiguo y el contador deja que el
    llamador avise de la ambigüedad — una confirmación equivocada haría entregar Bs de más.
    """
    floor = payment_created_at - timedelta(hours=LOOKBACK_HOURS)
    in_window = [
        c for c in candidates
        if c.amount == amount and floor <= c.received_at <= now
    ]
    if not in_window:
        return None, 0
    in_window.sort(key=lambda c: c.received_at)
    return in_window[0], len(in_window)


def schedule_next(step: int, requested_at: datetime) -> Optional[datetime]:
    """Cuándo toca el aviso del escalón `step` (0-based). None si ya no hay más."""
    if step < 0 or step >= len(ESCALATION_MINUTES):
        return None
    return requested_at + timedelta(minutes=ESCALATION_MINUTES[step])


def is_final_step(step: int) -> bool:
    return step >= len(ESCALATION_MINUTES) - 1


def build_confirmed_message(
    candidate: NotificationCandidate,
    *,
    amount: Decimal,
    minutes_elapsed: int,
    ambiguity_count: int,
) -> str:
    bank = "Bank of America" if candidate.bank == "BANK_OF_AMERICA" else candidate.bank
    when = candidate.received_at.strftime("%d/%m %H:%M")
    lines = [
        f"✅ *Pago confirmado por correo* — ${_fmt(amount)}",
        f"👤 A nombre de *{candidate.sender_name}*",
        f"🏦 En la cuenta de *{candidate.mailbox_label}* ({bank}, {when})",
    ]
    if minutes_elapsed > 0:
        lines.append(f"⏱️ Tardó {minutes_elapsed} min en aparecer")
    if ambiguity_count > 1:
        lines.append(
            f"⚠️ Había {ambiguity_count} correos de ${_fmt(amount)} sin asignar; "
            f"tomé el de las {candidate.received_at.strftime('%H:%M')}"
        )
    return "\n".join(lines)


def build_escalation_message(step: int, *, amount: Decimal, client_phone: str) -> str:
    minutes = ESCALATION_MINUTES[step]
    label = "1 h" if minutes >= 60 else f"{minutes} min"
    if is_final_step(step):
        return (
            f"🚨 *${_fmt(amount)} SIN CONFIRMAR* ({label}) — el comprobante de "
            f"{client_phone} no apareció en ningún correo. Cierro la verificación."
        )
    icon = "⏳" if step == 0 else "⚠️"
    return (
        f"{icon} ${_fmt(amount)} sigue sin aparecer en los correos ({label}) — "
        f"comprobante de {client_phone}"
    )
