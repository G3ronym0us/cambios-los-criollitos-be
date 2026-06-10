import asyncio

import aiohttp
from sqlalchemy.orm import Session
from typing import List
from app.repositories.rate_alert_repository import RateAlertRepository
from app.core.redis_pubsub import publish_alert
from app.core.config import settings
from app.services import web_push_service


class AlertService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = RateAlertRepository(db)

    async def process_divergences(self, divergences: List[dict]) -> None:
        payloads = []
        for data in divergences:
            alert = self.repo.create(data)
            payload = {
                "uuid": str(alert.uuid),
                "currency_pair_id": alert.currency_pair_id,
                "from_currency": alert.from_currency,
                "to_currency": alert.to_currency,
                "manual_rate": alert.manual_rate,
                "automatic_rate": alert.automatic_rate,
                "diff_percentage": alert.diff_percentage,
                "created_at": alert.created_at.isoformat() if alert.created_at else None,
            }
            await publish_alert(payload)
            payloads.append(payload)

        if payloads:
            await self._notify_operator(payloads)
            await self._send_web_push(payloads)

    async def _notify_operator(self, payloads: List[dict]) -> None:
        """Envía un WhatsApp al operador vía el bot con todas las divergencias del ciclo."""
        if not settings.BOT_NOTIFY_URL or not settings.BOT_API_KEY:
            return

        lines = ["⚠️ *Divergencia de tasas detectada*"]
        for p in payloads:
            lines.append(
                f"\n*{p['from_currency']} → {p['to_currency']}*: "
                f"manual {p['manual_rate']} vs Binance {p['automatic_rate']} "
                f"({p['diff_percentage']}%)"
            )
        text = "\n".join(lines)

        url = f"{settings.BOT_NOTIFY_URL.rstrip('/')}/api/notify"
        await self._post_to_bot(url, text)

    async def _send_web_push(self, payloads: List[dict]) -> None:
        """Notificación push a los dispositivos registrados (PWA/navegador)."""
        if not web_push_service.is_configured():
            return

        if len(payloads) == 1:
            p = payloads[0]
            body = (
                f"{p['from_currency']} → {p['to_currency']}: "
                f"manual {p['manual_rate']} vs Binance {p['automatic_rate']} "
                f"({p['diff_percentage']}%)"
            )
        else:
            pairs = ", ".join(f"{p['from_currency']}→{p['to_currency']}" for p in payloads)
            body = f"{len(payloads)} pares divergentes: {pairs}"

        try:
            # pywebpush es síncrono; no bloquear el event loop
            sent = await asyncio.to_thread(
                web_push_service.send_to_all,
                self.db,
                "⚠️ Divergencia de tasas",
                body,
            )
            if sent:
                print(f"📲 Web push enviado a {sent} dispositivo(s)")
        except Exception as e:
            print(f"⚠️ Web push falló: {e}")

    async def _post_to_bot(self, url: str, text: str) -> None:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    json={"text": text},
                    headers={"X-Bot-Token": settings.BOT_API_KEY},
                ) as resp:
                    if resp.status != 200:
                        print(f"⚠️ Notificación al bot falló: HTTP {resp.status}")
        except Exception as e:
            print(f"⚠️ Notificación al bot falló: {e}")
