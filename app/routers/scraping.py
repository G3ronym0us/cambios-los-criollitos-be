from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.tasks.scraping_tasks import manual_scrape
from app.services.scraping_service import ScrapingService
from app.repositories.exchange_rate_repository import ExchangeRateRepository

router = APIRouter()

@router.post("/scrape/manual")
async def manual_scraping(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Ejecutar scraping manual"""
    # Ejecutar como tarea de background
    task = manual_scrape.delay()
    
    return {
        "message": "Scraping iniciado",
        "task_id": task.id,
        "status": "processing"
    }

@router.get("/scrape/status/{task_id}")
async def get_scraping_status(task_id: str):
    """Obtener estado de una tarea de scraping"""
    from app.tasks.scraping_tasks import celery_app
    task = celery_app.AsyncResult(task_id)
    
    return {
        "task_id": task_id,
        "status": task.status,
        "result": task.result if task.ready() else None
    }

@router.get("/scrape/latest-rates")
async def get_latest_rates(db: Session = Depends(get_db)):
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
