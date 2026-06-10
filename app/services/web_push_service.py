import json
from typing import List, Optional

from pywebpush import webpush, WebPushException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.push_subscription import PushSubscription
from app.repositories.push_subscription_repository import PushSubscriptionRepository


def is_configured() -> bool:
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)


def send_to_subscriptions(db: Session, subscriptions: List[PushSubscription],
                          title: str, body: str, url: str = "/admin/alerts") -> int:
    """Envía una notificación push a las suscripciones dadas.

    Las suscripciones muertas (404/410 del push service) se eliminan.
    Devuelve la cantidad de envíos exitosos. Síncrono: usar asyncio.to_thread
    desde contextos async.
    """
    if not is_configured():
        return 0

    payload = json.dumps({"title": title, "body": body, "url": url})
    repo = PushSubscriptionRepository(db)
    sent = 0
    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_CLAIMS_SUB},
                ttl=3600,
            )
            sent += 1
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                # Suscripción expirada/revocada por el navegador
                repo.delete_by_endpoint(sub.endpoint)
                print(f"🗑️ Push subscription eliminada (HTTP {status}): {sub.endpoint[:60]}")
            else:
                print(f"⚠️ Web push falló (HTTP {status}): {e}")
        except Exception as e:
            print(f"⚠️ Web push falló: {e}")
    return sent


def send_to_all(db: Session, title: str, body: str, url: str = "/admin/alerts") -> int:
    """Envía una notificación push a todos los dispositivos registrados."""
    repo = PushSubscriptionRepository(db)
    return send_to_subscriptions(db, repo.get_all(), title, body, url)
