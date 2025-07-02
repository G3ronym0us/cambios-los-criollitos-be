import asyncio
from celery import Celery
from app.services.scraping_service import ScrapingService
from app.core.config import settings

# Configurar Celery
celery_app = Celery(
    "scraping_tasks",
    broker=settings.celery_broker_url_computed,
    backend=settings.celery_result_backend_computed
)

# Configurar tareas periódicas
celery_app.conf.beat_schedule = {
    'scrape-rates-every-1-hour': {
        'task': 'app.tasks.scraping_tasks.scrape_exchange_rates',
        'schedule': 3600.0,  # 1 hora en segundos
    },
}

celery_app.conf.timezone = 'UTC'

@celery_app.task
def scrape_exchange_rates():
    """Tarea de Celery para scraping de tasas de cambio"""
    print("🔄 Ejecutando tarea de scraping programada...")
    
    async def run_scraping():
        service = ScrapingService()
        try:
            success = await service.scrape_all_rates()
            await service.close_all_scrapers()
            return success
        except Exception as e:
            print(f"❌ Error en tarea de scraping: {e}")
            await service.close_all_scrapers()
            return False
    
    # Ejecutar la función async
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(run_scraping())
        return result
    finally:
        loop.close()

@celery_app.task
def manual_scrape():
    """Tarea manual de scraping"""
    return scrape_exchange_rates()
