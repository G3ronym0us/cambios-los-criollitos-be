import asyncio
from typing import List
from sqlalchemy.orm import Session
from app.services.scrapers.binance_scraper import BinanceP2PScraper
from app.repositories.exchange_rate_repository import ExchangeRateRepository
from app.database.connection import SessionLocal
from app.models.exchange_rate import ExchangeRate

class ScrapingService:
    def __init__(self):
        pass  # Scrapers will be initialized with DB session when needed

    async def scrape_all_rates(self) -> bool:
        """Ejecutar scraping de todas las fuentes y guardar en DB"""
        all_rates = []
        
        print("🚀 Iniciando scraping dinámico de tasas...")
        
        # Crear sesión de base de datos
        db = SessionLocal()
        try:
            # Inicializar scrapers con sesión de DB
            scrapers = [
                BinanceP2PScraper(db),
            ]
            
            for scraper in scrapers:
                try:
                    print(f"📡 Scraping desde: {scraper.source_name}")
                    await scraper.initialize()
                    rates = await scraper.get_rates()
                    all_rates.extend(rates)
                    await scraper.close()
                    
                except Exception as e:
                    print(f"❌ Error en scraper {scraper.source_name}: {e}")
                    continue

            if all_rates:
                # Guardar en base de datos
                repo = ExchangeRateRepository(db)
                success = repo.save_rates(all_rates)
                
                if success:
                    print(f"✅ Scraping completado: {len(all_rates)} tasas obtenidas y guardadas")
                    return True
                else:
                    print("❌ Error guardando tasas en base de datos")
                    return False
            else:
                print("⚠️ No se obtuvieron tasas válidas")
                return False
                
        finally:
            db.close()

    async def close_all_scrapers(self):
        """Cerrar todos los scrapers - deprecated since scrapers are now created per-request"""
        # Scrapers are now created and closed per scraping session
        pass
