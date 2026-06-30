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
                    rate_value = result['rate']


                    print(f"{from_currency} -> {to_currency} - {rate_value}")

                    # Crear tasas principales
                    pair_symbol = f"{from_currency}-{to_currency}"
                    currency_pair = self.currency_pair_repo.get_by_symbol(pair_symbol)

                    if currency_pair:
                        # El precio P2P siempre viene como FIAT por USDT. En un BUY
                        # (from=FIAT, to=USDT) la conversión from->to debe DIVIDIR por
                        # la tasa, así que marcamos inverse_percentage para que los
                        # consumidores (front/bot) usen amount/rate en vez de amount*rate.
                        is_fiat_to_crypto = result.get('type') == 'BUY'
                        rate = ExchangeRate.create_safe(
                            currency_pair_id=currency_pair.id,
                            from_currency=from_currency,
                            to_currency=to_currency,
                            rate=rate_value,
                            inverse_percentage=is_fiat_to_crypto
                        )
                        if rate:
                            rates.append(rate)
                            base_rates[f"{from_currency}_{to_currency}"] = rate.rate  # Store the numeric value, not the object
                    else:
                        print(f"⚠️ Par {pair_symbol} no encontrado en la base de datos")

            # Calcular tasas derivadas basadas en los pares obtenidos
            self._calculate_dynamic_derived_rates(rates, base_rates)

            # Calcular tasas cruzadas entre fiats
            self._calculate_cross_rates(rates, base_rates)

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
        
        # obtener pares con base_rates
        pairs = self.currency_pair_repo.get_pairs_with_base_rates()
        print(f"📝 Pares con base_rates: {pairs}")

        for pair in pairs:
            symbol = pair.base_pair.pair_symbol
            symbol = symbol.replace('-', '_')
            base_rate = effective_rates.get(symbol)
            if base_rate:
                rate = ExchangeRate.create_safe(
                    currency_pair_id=pair.id,
                    from_currency=pair.from_currency.symbol,
                    to_currency=pair.to_currency.symbol,
                    rate=base_rate,  # base_rate is already a float value
                    percentage=float(pair.derived_percentage) if pair.derived_percentage else None,
                    inverse_percentage=pair.use_inverse_percentage
                )
                if rate:
                    rates.append(rate)
            else:
                print(f"❌ No se encontró base rate para {symbol}")

    def _get_latest_manual_rates(self) -> dict:
        """Obtener las tasas manuales más recientes de la base de datos"""
        try:
            from sqlalchemy import and_, desc
            
            # Obtener todas las tasas manuales activas más recientes por par de monedas
            query = self.db_session.query(ExchangeRate).filter(
                and_(
                    ExchangeRate.is_manual == True,
                    ExchangeRate.is_active == True,
                )
            ).distinct(
                ExchangeRate.from_currency,
                ExchangeRate.to_currency
            ).order_by(
                ExchangeRate.from_currency,
                ExchangeRate.to_currency,
                desc(ExchangeRate.created_at)
            )
            
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

    def _calculate_cross_rates(self, rates: List[ExchangeRate], base_rates: dict):
        """
        Calcular tasas cruzadas entre dos FIATs usando USDT como intermediario.

        Para un par VES→COP, buscamos:
        - VES→USDT (primera tasa base)
        - USDT→COP (segunda tasa base)
        - Fórmula: VES→COP = (VES→USDT) / (USDT→COP)

        Args:
            rates: Lista de tasas donde se agregarán las tasas cruzadas
            base_rates: Diccionario con tasas base obtenidas
        """
        print("🔄 Calculando tasas cruzadas FIAT-FIAT...")

        # Obtener pares configurados como CROSS
        cross_pairs = self.currency_pair_repo.get_cross_rate_pairs()

        if not cross_pairs:
            print("⚠️ No hay pares cruzados configurados")
            return

        print(f"🎯 Encontrados {len(cross_pairs)} pares cruzados: {[p.pair_symbol for p in cross_pairs]}")

        # Obtener tasas manuales para priorizar
        manual_rates = self._get_latest_manual_rates()
        effective_rates = self._merge_rates_with_manual_priority(base_rates, manual_rates)

        # Moneda intermediaria
        intermediate = 'USDT'
        cross_rates_count = 0

        for pair in cross_pairs:
            from_fiat = pair.from_currency.symbol
            to_fiat = pair.to_currency.symbol

            # Para calcular from_fiat → to_fiat necesitamos:
            # 1. from_fiat → USDT
            # 2. USDT → to_fiat
            # Fórmula: from_fiat → to_fiat = (from_fiat → USDT) / (USDT → to_fiat)

            key_from_to_intermediate = f"{from_fiat}_{intermediate}"
            key_intermediate_to_to = f"{intermediate}_{to_fiat}"

            rate_from_to_intermediate = effective_rates.get(key_from_to_intermediate)
            rate_intermediate_to_to = effective_rates.get(key_intermediate_to_to)

            print(f"🔍 Buscando tasas para {from_fiat}→{to_fiat}:")
            print(f"   - {key_from_to_intermediate}: {rate_from_to_intermediate}")
            print(f"   - {key_intermediate_to_to}: {rate_intermediate_to_to}")

            # Si tenemos ambas tasas base, calculamos la cruzada
            if rate_from_to_intermediate and rate_intermediate_to_to:
                # Fórmula: from_fiat → to_fiat = (from_fiat → USDT) / (USDT → to_fiat)
                cross_rate = rate_from_to_intermediate / rate_intermediate_to_to

                # Crear ExchangeRate (create_safe aplica el percentage automáticamente)
                rate_obj = ExchangeRate.create_safe(
                    currency_pair_id=pair.id,
                    from_currency=from_fiat,
                    to_currency=to_fiat,
                    rate=cross_rate,
                    percentage=float(pair.derived_percentage) if pair.derived_percentage else None,
                    inverse_percentage=pair.use_inverse_percentage
                )

                if rate_obj:
                    rates.append(rate_obj)
                    cross_rates_count += 1
                    print(f"✅ Tasa cruzada: {from_fiat}→{to_fiat} = {rate_from_to_intermediate} / {rate_intermediate_to_to} = {rate_obj.rate:.6f}")
            else:
                # Mostrar qué tasas faltan
                missing = []
                if not rate_from_to_intermediate:
                    missing.append(key_from_to_intermediate)
                if not rate_intermediate_to_to:
                    missing.append(key_intermediate_to_to)

                print(f"⚠️ No se puede calcular {from_fiat}→{to_fiat}: faltan {', '.join(missing)}")

        print(f"✅ {cross_rates_count} tasas cruzadas calculadas")

    async def close(self):
        """Cerrar sesión HTTP"""
        if self.session:
            try:
                await self.session.close()
                print("🔒 Binance scraper cerrado")
            except Exception as e:
                print(f"❌ Error cerrando Binance scraper: {e}")
