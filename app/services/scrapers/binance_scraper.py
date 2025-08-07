import aiohttp
import asyncio
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from .base_scraper import BaseScraper
from app.enums.currency_enun import Currency
from app.models.exchange_rate import ExchangeRate
from app.repositories.currency_pair_repository import CurrencyPairRepository

class BinanceP2PScraper(BaseScraper):
    def __init__(self, db_session: Session):
        self.session = None
        self.db_session = db_session
        self.currency_pair_repo = CurrencyPairRepository(db_session)
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
                            return {
                                'from_currency': fiat.value if trade_type == 'BUY' else crypto.value,
                                'to_currency': crypto.value if trade_type == 'BUY' else fiat.value,
                                'rate': price,
                                'type': trade_type,
                                'payment_method': first_ad['adv']['tradeMethods'][0]
                            }
                        
            return None
        except Exception as e:
            print(f"❌ Error consultando Binance P2P: {e}")
            return None

    async def get_rates(self) -> List[ExchangeRate]:
        """Obtener tasas dinámicamente basadas en currency pairs activos con binance_tracked=True"""
        if not self.session:
            success = await self.initialize()
            if not success:
                return []

        try:
            print("🔄 Obteniendo tasas dinámicas de Binance P2P...")
            
            # Obtener pares activos que deben ser rastreados en Binance
            tracked_pairs = self.currency_pair_repo.get_binance_tracked_pairs()
            
            if not tracked_pairs:
                print("⚠️ No hay pares configurados para rastreo en Binance")
                return []

            print(f"🎯 Rastreando {len(tracked_pairs)} pares: {[pair.pair_symbol for pair in tracked_pairs]}")

            tasks = []

            # Crear tareas para cada par rastreado
            for pair in tracked_pairs:
                fiat_currency = None
                crypto_currency = None
                
                # Determinar cuál es FIAT y cuál es CRYPTO
                if pair.from_currency.currency_type.name == 'FIAT':
                    fiat_currency = Currency(pair.from_currency.symbol)
                    crypto_currency = Currency(pair.to_currency.symbol)
                    type = 'BUY'
                elif pair.to_currency.currency_type.name == 'FIAT':
                    fiat_currency = Currency(pair.to_currency.symbol)
                    crypto_currency = Currency(pair.from_currency.symbol)
                    type = 'SELL'
                else:
                    print(f"⚠️ Saltando par {pair.pair_symbol} - no es FIAT/CRYPTO")
                    continue

                banks = pair.banks_to_track or []
                amount = float(pair.amount_to_track) if pair.amount_to_track else None

                print(f"🔄 Procesando par {pair.pair_symbol} - {fiat_currency} -> {crypto_currency} - {type} - {banks} - {amount}")

                # Crear tareas para BUY y SELL
                tasks.append(self._get_p2p_data(fiat_currency, crypto_currency, type, banks, amount))

            if not tasks:
                print("⚠️ No se generaron tareas de scraping")
                return []

            # Ejecutar todas las tareas
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            rates = []
            base_rates = {}  # Para calcular tasas derivadas después

            # Procesar resultados
            for result in results:
                if result:
                    from_currency = result['from_currency']
                    to_currency = result['to_currency']
                    rate = result['rate']
                    type = result['type'] if result['type'] else 'BUY'
                

                    print(f"{from_currency} -> {to_currency} - {rate}")

                # Crear tasas principales
                
                    rate = ExchangeRate.create_safe(
                        from_currency=from_currency, 
                        to_currency=to_currency, 
                        rate=rate, 
                        source=self.source_name
                    )
                    if rate:
                        rates.append(rate)
                        base_rates[f"{from_currency}_{to_currency}_{type}"] = rate

            # Calcular tasas derivadas basadas en los pares obtenidos
            self._calculate_dynamic_derived_rates(rates, base_rates)

            print(f"✅ {len(rates)} tasas obtenidas dinámicamente de Binance")
            return rates

        except Exception as e:
            print(f"❌ Error obteniendo tasas dinámicas de Binance: {e}")
            return []

    def _calculate_dynamic_derived_rates(self, rates: List[ExchangeRate], base_rates: dict):
        """Calcular tasas derivadas dinámicamente (Zelle, PayPal, cruzadas) usando tasas manuales cuando estén disponibles"""
        
        print("🔄 Calculando tasas derivadas (priorizando tasas manuales)...")
        
        # Obtener tasas manuales más recientes de la DB para usar en derivadas
        manual_rates = self._get_latest_manual_rates()
        effective_rates = self._merge_rates_with_manual_priority(base_rates, manual_rates)
        
        print(f"📊 Usando {len(effective_rates)} tasas para derivadas ({len(manual_rates)} manuales, {len(base_rates)} automáticas)")
        
        # Separar tasas FIAT->CRYPTO y CRYPTO->FIAT para procesamiento
        fiat_to_crypto = {}
        crypto_to_fiat = {}
        
        for rate_key, rate_obj in effective_rates.items():
            rate_value = rate_obj.rate if hasattr(rate_obj, 'rate') else rate_obj
            parts = rate_key.split('_')
            
            if len(parts) >= 2:
                from_currency = parts[0]
                to_currency = parts[1]
                
                # Detectar si es FIAT->CRYPTO o CRYPTO->FIAT
                fiat_currencies = ['VES', 'COP', 'BRL', 'USD']
                crypto_currencies = ['USDT', 'BTC', 'ETH']
                
                if from_currency in fiat_currencies and to_currency in crypto_currencies:
                    # FIAT -> CRYPTO
                    fiat_to_crypto[from_currency] = rate_value
                    
                    # FIAT -> ZELLE (con margen inverso)
                    zelle_rate = ExchangeRate.create_safe(
                        from_currency, 'ZELLE', rate_value, 
                        source=f"{self.source_name}_derived", 
                        percentage=5, inverse_percentage=True
                    )
                    if zelle_rate: rates.append(zelle_rate)
                    
                    # FIAT -> PAYPAL (con margen inverso)
                    paypal_rate = ExchangeRate.create_safe(
                        from_currency, 'PAYPAL', rate_value, 
                        source=f"{self.source_name}_derived", 
                        percentage=5, inverse_percentage=True
                    )
                    if paypal_rate: rates.append(paypal_rate)
                    
                elif from_currency in crypto_currencies and to_currency in fiat_currencies:
                    # CRYPTO -> FIAT
                    crypto_to_fiat[to_currency] = rate_value
                    
                    # ZELLE -> FIAT (con margen)
                    zelle_rate = ExchangeRate.create_safe(
                        'ZELLE', to_currency, rate_value, 
                        source=f"{self.source_name}_derived", 
                        percentage=9
                    )
                    if zelle_rate: rates.append(zelle_rate)
                    
                    # PAYPAL -> FIAT (con margen mayor)
                    paypal_rate = ExchangeRate.create_safe(
                        'PAYPAL', to_currency, rate_value, 
                        source=f"{self.source_name}_derived", 
                        percentage=12
                    )
                    if paypal_rate: rates.append(paypal_rate)

        # Calcular tasas cruzadas FIAT-FIAT
        fiat_currencies = set(fiat_to_crypto.keys()) | set(crypto_to_fiat.keys())
        fiat_list = list(fiat_currencies)
        
        for i, fiat1 in enumerate(fiat_list):
            for fiat2 in fiat_list[i+1:]:
                # fiat1 -> fiat2 (usando fiat1->crypto y crypto->fiat2)
                fiat1_to_crypto = fiat_to_crypto.get(fiat1)
                crypto_to_fiat2 = crypto_to_fiat.get(fiat2)
                
                if fiat1_to_crypto and crypto_to_fiat2:
                    cross_rate = crypto_to_fiat2 / fiat1_to_crypto
                    rate = ExchangeRate.create_safe(
                        fiat1, fiat2, cross_rate, 
                        source=f"{self.source_name}_cross", 
                        percentage=8
                    )
                    if rate: rates.append(rate)
                
                # fiat2 -> fiat1 (usando fiat2->crypto y crypto->fiat1)
                fiat2_to_crypto = fiat_to_crypto.get(fiat2)
                crypto_to_fiat1 = crypto_to_fiat.get(fiat1)
                
                if fiat2_to_crypto and crypto_to_fiat1:
                    cross_rate = crypto_to_fiat1 / fiat2_to_crypto
                    rate = ExchangeRate.create_safe(
                        fiat2, fiat1, cross_rate, 
                        source=f"{self.source_name}_cross", 
                        percentage=8
                    )
                    if rate: rates.append(rate)

        print(f"✅ Tasas derivadas calculadas")

    def _get_latest_manual_rates(self) -> dict:
        """Obtener las tasas manuales más recientes de la base de datos"""
        try:
            from sqlalchemy import and_, desc
            
            # Obtener todas las tasas manuales activas más recientes por par de monedas
            query = self.db_session.query(ExchangeRate).filter(
                and_(
                    ExchangeRate.is_manual == True,
                    ExchangeRate.is_active == True
                )
            ).order_by(desc(ExchangeRate.created_at))
            
            manual_exchange_rates = query.all()

            print(f"📝 Tasas manuales obtenidas desde la DB: {manual_exchange_rates}")
            
            manual_rates = {}
            processed_pairs = set()
            
            for rate in manual_exchange_rates:
                # Crear key único para el par de monedas
                pair_key = f"{rate.from_currency}_{rate.to_currency}"
                
                # Solo tomar la más reciente para cada par (ya está ordenado por created_at desc)
                if pair_key in processed_pairs:
                    continue
                    
                processed_pairs.add(pair_key)
                
                manual_rates[pair_key] = float(rate.rate)
                print(f"📝 Tasa manual encontrada: {pair_key} = {rate.rate} (creada: {rate.created_at})")

            print(f"📝 Tasas manuales obtenidas: {manual_rates}")
                
            return manual_rates
            
        except Exception as e:
            print(f"⚠️ Error obteniendo tasas manuales: {e}")
            return {}

    def _merge_rates_with_manual_priority(self, auto_rates: dict, manual_rates: dict) -> dict:
        """Combinar tasas automáticas y manuales, priorizando las manuales"""
        effective_rates = auto_rates.copy()
        
        # Sobrescribir con tasas manuales cuando estén disponibles
        for key, value in manual_rates.items():
            if key in effective_rates:
                print(f"🔄 Reemplazando tasa automática {key}: {effective_rates[key]} → {value} (manual)")
            else:
                print(f"➕ Añadiendo tasa manual {key}: {value}")
            effective_rates[key] = value
            
        return effective_rates

    async def close(self):
        """Cerrar sesión HTTP"""
        if self.session:
            try:
                await self.session.close()
                print("🔒 Binance scraper cerrado")
            except Exception as e:
                print(f"❌ Error cerrando Binance scraper: {e}")
