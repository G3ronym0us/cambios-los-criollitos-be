import aiohttp
import asyncio
from typing import List, Optional
from datetime import datetime
from .base_scraper import BaseScraper
from app.enums.currency_enun import Currency
from app.models.exchange_rate import ExchangeRate

class BinanceP2PScraper(BaseScraper):
    def __init__(self):
        self.session = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8,es;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://p2p.binance.com',
            'Referer': 'https://p2p.binance.com/',
            'Content-Type': 'application/json',
            'X-Forwarded-For': '177.67.82.22',  # IP brasileño
            'CF-IPCountry': 'BR',
        }

    @property
    def source_name(self) -> str:
        return "binance_p2p"

    async def initialize(self) -> bool:
        """Inicializar sesión HTTP"""
        try:
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=30)
            )
            print("✅ Binance P2P Scraper inicializado")
            return True
        except Exception as e:
            print(f"❌ Error inicializando Binance scraper: {e}")
            return False

    async def _get_p2p_data(self, fiat: Currency, crypto: Currency, trade_type: str, payment_method: List[str], amount: Optional[float] = None):
        """Obtener datos de P2P de Binance"""
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        
        payload = {
            "page": 1,
            "rows": 20,
            "payTypes": payment_method,
            "asset": crypto.value,
            "tradeType": trade_type,
            "fiat": fiat.value,
            "publisherType": None,
            "merchantCheck": False,
            "countries": ["BR"] if fiat.value == 'BRL' else ["VE"] if fiat.value == 'VES' else ["CO"] if fiat.value == 'COP' else [],
            "proMerchantAds": False,
            "filterType": "all",
        }
        
        if amount:
            payload["transAmount"] = amount

        try:
            if not self.session:
                await self.initialize()
            
            async with self.session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('success') and data.get('data'):
                        ads = data['data']
                        
                        # Filtrar anuncios válidos
                        valid_ads = [ad for ad in ads if float(ad['adv']['tradableQuantity']) > 0]
                        
                        if valid_ads:
                            first_ad = valid_ads[0]
                            price = float(first_ad['adv']['price'])
                            return price
                        
            return None
        except Exception as e:
            print(f"❌ Error consultando Binance P2P: {e}")
            return None

    async def get_rates(self) -> List[ExchangeRate]:
        """Obtener todas las tasas de Binance"""
        if not self.session:
            success = await self.initialize()
            if not success:
                return []

        try:
            print("🔄 Obteniendo tasas de Binance P2P...")
            
            tasks = []
            
            # VES
            tasks.append(self._get_p2p_data(Currency.VES, Currency.USDT, 'BUY', ['BANK', 'SpecificBank'], 20000))
            tasks.append(self._get_p2p_data(Currency.VES, Currency.USDT, 'SELL', ['BANK', 'SpecificBank'], 20000))
            
            # COP
            tasks.append(self._get_p2p_data(Currency.COP, Currency.USDT, 'BUY', ['BancolombiaSA'], 500000))
            tasks.append(self._get_p2p_data(Currency.COP, Currency.USDT, 'SELL', ['BancolombiaSA'], 500000))
            
            # BRL
            tasks.append(self._get_p2p_data(Currency.BRL, Currency.USDT, 'BUY', ['PIX'], 500))
            tasks.append(self._get_p2p_data(Currency.BRL, Currency.USDT, 'SELL', ['PIX'], 500))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell = results

            rates = []
            timestamp = datetime.utcnow()

            # Crear tasas principales con USDT
            if ves_buy: rates.append(ExchangeRate(from_currency='VES', to_currency='USDT', rate=ves_buy, source=self.source_name))
            if ves_sell: rates.append(ExchangeRate(from_currency='USDT', to_currency='VES', rate=ves_sell, source=self.source_name))
            if cop_buy: rates.append(ExchangeRate(from_currency='COP', to_currency='USDT', rate=cop_buy, source=self.source_name))
            if cop_sell: rates.append(ExchangeRate(from_currency='USDT', to_currency='COP', rate=cop_sell, source=self.source_name))
            if brl_buy: rates.append(ExchangeRate(from_currency='BRL', to_currency='USDT', rate=brl_buy, source=self.source_name))
            if brl_sell: rates.append(ExchangeRate(from_currency='USDT', to_currency='BRL', rate=brl_sell, source=self.source_name))

            # Calcular tasas derivadas
            self._calculate_derived_rates(rates, ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell)

            print(f"✅ {len(rates)} tasas obtenidas de Binance")
            return rates

        except Exception as e:
            print(f"❌ Error obteniendo tasas de Binance: {e}")
            return []

    def _calculate_derived_rates(self, rates: List[ExchangeRate], ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell):
        """Calcular tasas derivadas (Zelle, PayPal, cruzadas)"""
        
        # Tasas con Zelle (con márgenes)
        if ves_buy: rates.append(ExchangeRate(from_currency='VES', to_currency='ZELLE', rate=ves_buy * 0.95, source=f"{self.source_name}_derived"))
        if ves_sell: rates.append(ExchangeRate(from_currency='ZELLE', to_currency='VES', rate=ves_sell * 1.10, source=f"{self.source_name}_derived"))
        if cop_buy: rates.append(ExchangeRate(from_currency='COP', to_currency='ZELLE', rate=cop_buy * 0.90, source=f"{self.source_name}_derived"))
        if cop_sell: rates.append(ExchangeRate(from_currency='ZELLE', to_currency='COP', rate=cop_sell * 1.10, source=f"{self.source_name}_derived"))
        if brl_buy: rates.append(ExchangeRate(from_currency='BRL', to_currency='ZELLE', rate=brl_buy * 0.90, source=f"{self.source_name}_derived"))
        if brl_sell: rates.append(ExchangeRate(from_currency='ZELLE', to_currency='BRL', rate=brl_sell * 1.10, source=f"{self.source_name}_derived"))

        # Tasas con PayPal (márgenes más altos)
        if ves_buy: rates.append(ExchangeRate(from_currency='VES', to_currency='PAYPAL', rate=ves_buy * 0.92, source=f"{self.source_name}_derived"))
        if ves_sell: rates.append(ExchangeRate(from_currency='PAYPAL', to_currency='VES', rate=ves_sell * 1.13, source=f"{self.source_name}_derived"))
        if cop_buy: rates.append(ExchangeRate(from_currency='COP', to_currency='PAYPAL', rate=cop_buy * 0.87, source=f"{self.source_name}_derived"))
        if cop_sell: rates.append(ExchangeRate(from_currency='PAYPAL', to_currency='COP', rate=cop_sell * 1.13, source=f"{self.source_name}_derived"))
        if brl_buy: rates.append(ExchangeRate(from_currency='BRL', to_currency='PAYPAL', rate=brl_buy * 0.87, source=f"{self.source_name}_derived"))
        if brl_sell: rates.append(ExchangeRate(from_currency='PAYPAL', to_currency='BRL', rate=brl_sell * 1.13, source=f"{self.source_name}_derived"))

        # Tasas cruzadas directas
        if all(x is not None for x in [ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell]):
            rates.append(ExchangeRate(from_currency='VES', to_currency='COP', rate=cop_sell / ves_buy, source=f"{self.source_name}_cross"))
            rates.append(ExchangeRate(from_currency='COP', to_currency='VES', rate=ves_sell / cop_buy, source=f"{self.source_name}_cross"))
            rates.append(ExchangeRate(from_currency='VES', to_currency='BRL', rate=brl_sell / ves_buy, source=f"{self.source_name}_cross"))
            rates.append(ExchangeRate(from_currency='BRL', to_currency='VES', rate=ves_sell / brl_buy, source=f"{self.source_name}_cross"))
            rates.append(ExchangeRate(from_currency='COP', to_currency='BRL', rate=brl_sell / cop_buy, source=f"{self.source_name}_cross"))
            rates.append(ExchangeRate(from_currency='BRL', to_currency='COP', rate=cop_sell / brl_buy, source=f"{self.source_name}_cross"))

    async def close(self):
        """Cerrar sesión HTTP"""
        if self.session:
            try:
                await self.session.close()
                print("🔒 Binance scraper cerrado")
            except Exception as e:
                print(f"❌ Error cerrando Binance scraper: {e}")
