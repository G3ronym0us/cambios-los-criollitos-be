from abc import ABC, abstractmethod
from typing import List, Optional
from app.models.exchange_rate import ExchangeRate
from app.enums.currency_enun import Currency

class BaseScraper(ABC):
    """Clase base para todos los scrapers"""
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Inicializar el scraper"""
        pass
    
    @abstractmethod
    async def get_rates(self) -> List[ExchangeRate]:
        """Obtener tasas de cambio"""
        pass
    
    @abstractmethod
    async def close(self):
        """Cerrar recursos del scraper"""
        pass
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Nombre de la fuente del scraper"""
        pass
