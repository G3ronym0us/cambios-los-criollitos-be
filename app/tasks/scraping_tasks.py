import asyncio
from app.celery_app import celery_app
from app.services.scraping_service import ScrapingService


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
