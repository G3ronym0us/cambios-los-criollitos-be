from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.tasks.scraping_tasks import manual_scrape
from app.services.scraping_service import ScrapingService
from app.repositories.exchange_rate_repository import ExchangeRateRepository
from app.core.dependencies import get_moderator_user
from app.models.user import User

router = APIRouter()

# H-4.4: none of these three routes had any auth dependency at all — they hang off the
# host root (mounted with no prefix in app/main.py) and are reachable by anyone with
# network access to the API, unauthenticated. Confirmed nothing in the live frontend or
# the WhatsApp bot calls them (the only frontend reference, src/components/Dashboard.tsx,
# hits hardcoded http://localhost:8000/api/scrape — a different, dead path from an early
# prototype, not the real /scrape/* routes). Triggering a real Binance scrape and reading
# task/rate internals are staff operations, not the public rate calculator (that's what
# the intentionally-public GET /rates already serves) — gate all three at MODERATOR+.


@router.post("/scrape/manual")
async def manual_scraping(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Ejecutar scraping manual"""
    # Ejecutar como tarea de background
    task = manual_scrape.delay()

    return {
        "message": "Scraping iniciado",
        "task_id": task.id,
        "status": "processing"
    }


@router.get("/scrape/status/{task_id}")
async def get_scraping_status(
    task_id: str,
    current_user: User = Depends(get_moderator_user),
):
    """Obtener estado de una tarea de scraping"""
    from app.celery_app import celery_app
    task = celery_app.AsyncResult(task_id)

    return {
        "task_id": task_id,
        "status": task.status,
        "result": task.result if task.ready() else None
    }


@router.get("/scrape/latest-rates")
async def get_latest_rates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Obtener las tasas más recientes"""
    repo = ExchangeRateRepository(db)
    rates = repo.get_active_rates()

    return {
        "total_rates": len(rates),
        "rates": [
            {
                "from_currency": rate.from_currency,
                "to_currency": rate.to_currency,
                "rate": rate.rate,
                "source": rate.source,
                "created_at": rate.created_at
            }
            for rate in rates[:50]  # Limitar a 50 para la respuesta
        ]
    }
