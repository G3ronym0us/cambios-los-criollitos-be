"""
Mandarle un mensaje de WhatsApp al operador a través del bot.

El bot expone /api/notify en su dashboard (BOT_NOTIFY_URL, típicamente :3457) protegido
con X-Bot-Token. Nunca propaga excepciones: un aviso que no sale no puede tumbar al que
lo estaba mandando.
"""

import aiohttp

from app.core.config import settings

TIMEOUT_SECONDS = 5


async def notify_operator(text: str) -> bool:
    """Devuelve True si el bot aceptó el mensaje."""
    if not settings.BOT_NOTIFY_URL or not settings.BOT_API_KEY:
        return False

    url = f"{settings.BOT_NOTIFY_URL.rstrip('/')}/api/notify"
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={"text": text},
                headers={"X-Bot-Token": settings.BOT_API_KEY},
            ) as resp:
                if resp.status != 200:
                    print(f"⚠️ Notificación al bot falló: HTTP {resp.status}")
                    return False
                return True
    except Exception as e:
        print(f"⚠️ Notificación al bot falló: {e}")
        return False
