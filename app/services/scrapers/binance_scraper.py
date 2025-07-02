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
            'Content-Type': 'application/json'
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
            "countries": [],
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

            print(f"VES_BUY: {ves_buy}")
            print(f"VES_SELL: {ves_sell}")
            print(f"COP_BUY: {cop_buy}")
            print(f"COP_SELL: {cop_sell}")
            print(f"BRL_BUY: {brl_buy}")
            print(f"BRL_SELL: {brl_sell}")

            rates = []
            timestamp = datetime.utcnow()

            # Crear tasas principales con USDT
            if ves_buy: rates.append(ExchangeRate.create_safe(from_currency='VES', to_currency='USDT', rate=ves_buy, source=self.source_name))
            if ves_sell: rates.append(ExchangeRate.create_safe(from_currency='USDT', to_currency='VES', rate=ves_sell, source=self.source_name))
            if cop_buy: rates.append(ExchangeRate.create_safe(from_currency='COP', to_currency='USDT', rate=cop_buy, source=self.source_name))
            if cop_sell: rates.append(ExchangeRate.create_safe(from_currency='USDT', to_currency='COP', rate=cop_sell, source=self.source_name))
            if brl_buy: rates.append(ExchangeRate.create_safe(from_currency='BRL', to_currency='USDT', rate=brl_buy, source=self.source_name))
            if brl_sell: rates.append(ExchangeRate.create_safe(from_currency='USDT', to_currency='BRL', rate=brl_sell, source=self.source_name))
            # Calcular tasas derivadas
            self._calculate_derived_rates(rates, ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell)

            print(f"✅ {len(rates)} tasas obtenidas de Binance")
            return rates

        except Exception as e:
            print(f"❌ Error obteniendo tasas de Binance: {e}")
            return []

    def _calculate_derived_rates(self, rates: List[ExchangeRate], ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell):
        """Calcular tasas derivadas (Zelle, PayPal, cruzadas)"""
        
        # Tasas con Zelle (con márgenes) - usando create_safe
        if ves_buy: 
            rate = ExchangeRate.create_safe('VES', 'ZELLE', ves_buy, source=f"{self.source_name}_derived", percentage=5, inverse_percentage=True)
            if rate: rates.append(rate)
        if ves_sell: 
            rate = ExchangeRate.create_safe('ZELLE', 'VES', ves_sell, source=f"{self.source_name}_derived", percentage=9)
            if rate: rates.append(rate)
        if cop_buy: 
            rate = ExchangeRate.create_safe('COP', 'ZELLE', cop_buy, source=f"{self.source_name}_derived", percentage=5, inverse_percentage=True)
            if rate: rates.append(rate)
        if cop_sell: 
            rate = ExchangeRate.create_safe('ZELLE', 'COP', cop_sell, source=f"{self.source_name}_derived", percentage=9)
            if rate: rates.append(rate)
        if brl_buy: 
            rate = ExchangeRate.create_safe('BRL', 'ZELLE', brl_buy, source=f"{self.source_name}_derived", percentage=5, inverse_percentage=True)
            if rate: rates.append(rate)
        if brl_sell: 
            rate = ExchangeRate.create_safe('ZELLE', 'BRL', brl_sell, source=f"{self.source_name}_derived", percentage=9)
            if rate: rates.append(rate)

        # Tasas con PayPal (márgenes más altos) - usando create_safe
        if ves_buy: 
            rate = ExchangeRate.create_safe('VES', 'PAYPAL', ves_buy, source=f"{self.source_name}_derived", percentage=5, inverse_percentage=True)
            if rate: rates.append(rate)
        if ves_sell: 
            rate = ExchangeRate.create_safe('PAYPAL', 'VES', ves_sell, source=f"{self.source_name}_derived", percentage=12)
            if rate: rates.append(rate)
        if cop_buy: 
            rate = ExchangeRate.create_safe('COP', 'PAYPAL', cop_buy, source=f"{self.source_name}_derived", percentage=5, inverse_percentage=True)
            if rate: rates.append(rate)
        if cop_sell: 
            rate = ExchangeRate.create_safe('PAYPAL', 'COP', cop_sell, source=f"{self.source_name}_derived", percentage=12)
            if rate: rates.append(rate)
        if brl_buy: 
            rate = ExchangeRate.create_safe('BRL', 'PAYPAL', brl_buy, source=f"{self.source_name}_derived", percentage=5, inverse_percentage=True)
            if rate: rates.append(rate)
        if brl_sell: 
            rate = ExchangeRate.create_safe('PAYPAL', 'BRL', brl_sell, source=f"{self.source_name}_derived", percentage=12)
            if rate: rates.append(rate)

        # Tasas cruzadas directas - usando create_safe
        if ves_buy and cop_sell:
            rate1 = ExchangeRate.create_safe('VES', 'COP', cop_sell / ves_buy, source=f"{self.source_name}_cross", percentage=8)
            if rate1: rates.append(rate1)
        if ves_sell and cop_buy:
            rate2 = ExchangeRate.create_safe('COP', 'VES', ves_sell / cop_buy, source=f"{self.source_name}_cross", percentage=8)
            if rate2: rates.append(rate2)
        if ves_buy and brl_sell:
            rate1 = ExchangeRate.create_safe('VES', 'BRL', brl_sell / ves_buy, source=f"{self.source_name}_cross", percentage=6)
            if rate1: rates.append(rate1)
        if ves_sell and brl_buy:
            rate2 = ExchangeRate.create_safe('BRL', 'VES', ves_sell / brl_buy, source=f"{self.source_name}_cross", percentage=6)
            if rate2: rates.append(rate2)
        if cop_buy and brl_sell:
            rate1 = ExchangeRate.create_safe('COP', 'BRL', brl_sell / cop_buy, source=f"{self.source_name}_cross", percentage=8)
            if rate1: rates.append(rate1)
        if cop_sell and brl_buy:
            rate2 = ExchangeRate.create_safe('BRL', 'COP', cop_sell / brl_buy, source=f"{self.source_name}_cross", percentage=8)
            if rate2: rates.append(rate2)

    async def close(self):
        """Cerrar sesión HTTP"""
        if self.session:
            try:
                await self.session.close()
                print("🔒 Binance scraper cerrado")
            except Exception as e:
                print(f"❌ Error cerrando Binance scraper: {e}")
