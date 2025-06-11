from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.scraper_service import BinanceP2PScraperService

class CoinType:
    def __init__(
        self, 
        name: str, 
        acronym: str, 
        from_price: float, 
        to_price: float, 
        is_searchable: bool = False, 
        base_coin: Optional['CoinType'] = None, 
        payment_method: Optional[str] = None, 
        amount: Optional[float] = None, 
        scraper: Optional['BinanceP2PScraperService'] = None
    ):
        self.name = name
        self.acronym = acronym
        self.from_price = from_price
        self.to_price = to_price
        self.is_searchable = is_searchable
        self.payment_method = payment_method
        self.base_coin = base_coin
        self.amount = amount
        
        if base_coin:
            self.set_price(base_coin.from_price, base_coin.to_price)
        if is_searchable and scraper:
            self.search_price(scraper)

    def set_price(self, from_price: float, to_price: float):
        self.from_price = from_price
        self.to_price = to_price

    def search_price(self, scraper: 'BinanceP2PScraperService'):
        """Esta función será llamada por el servicio de scraping"""
        pass  # Se implementará en el servicio

    def print_price(self):
        print(f'{self.name} - {self.acronym} - {self.from_price} - {self.to_price}')