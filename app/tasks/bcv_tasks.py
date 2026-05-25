"""
Celery task que refresca la tasa BCV (USD/VES) cada 5 minutos.
Se programa desde app/celery_app.py beat_schedule.
"""

import asyncio
from app.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.bcv_service import fetch_bcv_rate


@celery_app.task(name="app.tasks.bcv_tasks.refresh_bcv_rate")
def refresh_bcv_rate():
    """Fetch + persist BCV rate. Idempotente; no falla si la API externa cae."""
    async def _run() -> float | None:
        db = SessionLocal()
        try:
            return await fetch_bcv_rate(db)
        finally:
            db.close()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_run())
        print(f"[BCV] refresh result: {result}")
        return result
    finally:
        loop.close()
