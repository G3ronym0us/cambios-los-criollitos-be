"""
Poller de los buzones de las cuentas alquiladas. Se programa desde app/celery_app.py.

Cada vuelta hace tres cosas en orden: ingerir correos nuevos, resolver las verificaciones
pendientes contra lo ingerido, y escalar las que siguen sin confirmar.

Un lock en Redis evita que dos vueltas solapadas consuman el mismo correo dos veces.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from redis import Redis

from app.celery_app import celery_app
from app.core.config import settings
from app.database.connection import SessionLocal
from app.services.bank_email_imap import MailboxUnavailable
from app.services.bank_email_service import BankEmailService
from app.services.bot_notifier import notify_operator

LOCK_KEY = "bank_email_poll_lock"

#: Muy por encima de lo que tarda una vuelta. El lock se borra en el `finally`; el TTL es
#: solo la red de seguridad para si el worker muere. Ponerlo cerca del intervalo del beat
#: es un bug: si la vuelta tarda más que el TTL (pasó con 2 buzones: 56 s contra 55 s),
#: el lock caduca sola, entra una segunda vuelta y las dos consumen los mismos correos.
LOCK_TTL_SECONDS = 600

#: Cuánto se congela la escalera cuando un buzón no se pudo leer. Un poco más que el
#: intervalo del poller, para que se descongele sola en cuanto el buzón vuelva.
FREEZE_MINUTES = 5

#: Un correo rechazado sigue en la bandeja y se relee cada vuelta. Sin esto avisaría cada
#: 60 s durante 24 h. Un día de silencio por correo es suficiente.
WARNED_TTL_SECONDS = 86400

#: Igual para el buzón caído: un aviso por hora alcanza para enterarse.
MAILBOX_ALERT_TTL_SECONDS = 3600


@celery_app.task(name="app.tasks.bank_email_tasks.poll_bank_emails")
def poll_bank_emails():
    boxes = settings.mailboxes_computed
    if not boxes:
        return "sin buzones configurados"

    redis = Redis.from_url(settings.REDIS_URL)
    # Token propio: al soltar el lock hay que comprobar que sigue siendo el nuestro, o una
    # vuelta lenta le borraría el lock a la que entró después de que el suyo caducara.
    token = uuid.uuid4().hex
    if not redis.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_SECONDS):
        return "otra vuelta en curso"

    db = SessionLocal()
    sent = 0
    ingested = 0
    try:
        service = BankEmailService(db)
        now = datetime.now(timezone.utc)

        any_failure = False
        for box in boxes:
            try:
                count, rejected = service.ingest_mailbox(box)
                ingested += count
                for item in rejected:
                    # Una sola vez por correo, aunque siga en la bandeja mañana.
                    if redis.set(f"bank_email_warned:{item.message_id}", "1",
                                 nx=True, ex=WARNED_TTL_SECONDS):
                        if _notify(item.text):
                            sent += 1
            except MailboxUnavailable as e:
                any_failure = True
                print(f"⚠️ {e}")
                if redis.set(f"bank_email_mailbox_down:{box.label}", "1",
                             nx=True, ex=MAILBOX_ALERT_TTL_SECONDS):
                    if _notify(f"🚨 No puedo leer el correo de {box.label} — revisar credenciales"):
                        sent += 1

        if any_failure:
            # Sin poder mirar, no se acusa a ningún pago de no existir.
            service.freeze_pending(now=now, minutes=FREEZE_MINUTES)

        sent += len(service.resolve_pending(now=now, notify=_notify))
        sent += len(service.escalate_pending(now=now, notify=_notify))

        return f"ingeridos={ingested} avisos={sent}"
    finally:
        db.close()
        try:
            _release_lock(redis, token)
        except Exception:
            pass


#: Borra el lock solo si sigue siendo el nuestro (compare-and-delete atómico).
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _release_lock(redis: Redis, token: str) -> None:
    redis.eval(_RELEASE_LOCK_LUA, 1, LOCK_KEY, token)


def _notify(text: str) -> bool:
    """notify_operator es async; el worker de Celery es síncrono."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(notify_operator(text))
    finally:
        loop.close()
