"""
Celery task que refresca las tasas BCV (USD/VES y EUR/VES) cada 5 minutos.
Se programa desde app/celery_app.py beat_schedule.
"""

import asyncio
from typing import Dict, Optional

from app.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.bcv_service import fetch_all_bcv_rates


@celery_app.task(name="app.tasks.bcv_tasks.refresh_bcv_rate")
def refresh_bcv_rate():
    """Fetch + persist de las tasas BCV. Idempotente; no falla si la API externa cae."""
    async def _run() -> Dict[str, Optional[float]]:
        db = SessionLocal()
        try:
            return await fetch_all_bcv_rates(db)
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
