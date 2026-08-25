"""
Celery task que le manda al operador, una vez al día, lo que el analizador no pudo resolver.
Se programa desde app/celery_app.py beat_schedule.

Si no hay nada pendiente no manda nada: un resumen que llega todos los días diciendo "cero"
se vuelve invisible en una semana, y entonces el día que traiga algo tampoco se lee.
"""

import asyncio

from app.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.analysis_digest_service import AnalysisDigestService
from app.services.bot_notifier import notify_operator


@celery_app.task(name="app.tasks.analysis_digest_tasks.send_analysis_digest")
def send_analysis_digest(hours: int = 24):
    async def _run() -> str:
        db = SessionLocal()
        try:
            text = AnalysisDigestService(db).build_message(hours)
            if text is None:
                return "sin pendientes"
            ok = await notify_operator(text)
            return "enviado" if ok else "el bot no aceptó el aviso"
        finally:
            db.close()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_run())
        print(f"[Digest] {result}")
        return result
    finally:
        loop.close()
