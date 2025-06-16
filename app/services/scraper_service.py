import requests
import asyncio
import aiohttp
import json
import time
from typing import Optional, Dict, List
from datetime import datetime
from app.enums.currency_enun import Currency
from app.models.exchange_rate import ExchangeRate

class BinanceP2PScraperService:
    def __init__(self):
        self.session = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://p2p.binance.com',
            'Referer': 'https://p2p.binance.com/',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Linux"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Content-Type': 'application/json',
        }

    async def initialize(self):
        """Inicializar sesión HTTP"""
        try:
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=30)
            )
            print("✅ Scraper HTTP inicializado correctamente")
            return True
        except Exception as e:
            print(f"❌ Error inicializando scraper HTTP: {e}")
            return False

    async def _get_p2p_data(self, fiat: Currency, crypto: Currency = Currency.USDT, trade_type: str = 'BUY', payment_method: List[str] = [], amount: Optional[float] = None):
        """Obtener datos de P2P usando la API interna de Binance"""
        
        # URL de la API interna de Binance P2P
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        
        # Payload para la API
        payload = {
            "page": 1,
            "rows": 10,
            "payTypes": payment_method,
            "asset": crypto.value,
            "tradeType": trade_type,
            "fiat": fiat.value,
            "publisherType": None,
            "merchantCheck": False,
            "countries": [],
        }
        
        # Agregar monto si se especifica
        if amount is not None:
            payload["transAmount"] = amount
        
        try:
            if not self.session:
                await self.initialize()
            
            print(f"🔍 Consultando API Binance P2P: {fiat.value} {trade_type} {payment_method or 'ALL'}")
            
            async with self.session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('success') and data.get('data'):
                        ads = data['data']
                        
                        if ads:
                            # Tomar el primer anuncio (mejor precio)
                            first_ad = ads[0]
                            price = float(first_ad['adv']['price'])
                            
                            # Información adicional del anuncio
                            merchant_name = first_ad['advertiser']['nickName']
                            completion_rate = first_ad['advertiser']['monthFinishRate']
                            orders_count = first_ad['advertiser']['monthOrderCount']
                            
                            print(f"✅ Precio Binance obtenido para {fiat.value} {trade_type}: {price}")
                            print(f"   📊 Comerciante: {merchant_name}")
                            print(f"   ⭐ Tasa completación mensual: {completion_rate}")
                            print(f"   📦 Órdenes del mes: {orders_count}")
                            
                            return price
                        else:
                            print(f"⚠️ No se encontraron anuncios para {fiat.value} {trade_type}")
                            return None
                    else:
                        print(f"❌ Respuesta API Binance inválida: {data}")
                        return None
                else:
                    print(f"❌ Error HTTP Binance {response.status}")
                    response_text = await response.text()
                    print(f"   Respuesta: {response_text[:200]}...")
                    return None
                    
        except Exception as e:
            print(f"❌ Error consultando API Binance P2P: {e}")
            return None

    async def get_offers(self, fiat: Currency, crypto: Currency, payment_method: List[str], side: str = 'buy', amount: Optional[float] = None):
        """Obtener ofertas P2P de Binance"""
        
        # Mapear side a trade_type
        trade_type = 'BUY' if side.lower() == 'buy' else 'SELL'
        
        # Obtener precio
        price = await self._get_p2p_data(fiat, crypto, trade_type, payment_method, amount)
        
        return price

    async def get_all_rates(self) -> List[ExchangeRate]:
        """Obtener todas las tasas de cambio usando solo Binance P2P"""
        
        if not self.session:
            success = await self.initialize()
            if not success:
                print("❌ No se pudo inicializar el scraper HTTP")
                return []
        
        try:
            print("🔄 Obteniendo tasas de Binance P2P via API...")
            
            # Crear tareas para obtener todos los precios en paralelo
            tasks = []
            
            # Precios VES (Bolívares Venezolanos)
            tasks.append(self.get_offers(Currency.VES, Currency.USDT, ['BANK', 'SpecificBank'], 'buy', 20000))
            tasks.append(self.get_offers(Currency.VES, Currency.USDT, ['BANK', 'SpecificBank'], 'sell', 20000))
            
            # Precios COP (Pesos Colombianos)
            tasks.append(self.get_offers(Currency.COP, Currency.USDT, ['BancolombiaSA'], 'buy', 500000))
            tasks.append(self.get_offers(Currency.COP, Currency.USDT, ['BancolombiaSA'], 'sell', 500000))
            
            # Precios BRL (Reales Brasileños)
            tasks.append(self.get_offers(Currency.BRL, Currency.USDT, ['PIX'], 'buy', 500))
            tasks.append(self.get_offers(Currency.BRL, Currency.USDT, ['PIX'], 'sell', 500))
            
            # Ejecutar todas las tareas en paralelo
            print("⏳ Ejecutando consultas en paralelo...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Procesar resultados
            ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell = results

            # Función para validar precios
            def safe_price(price):
                if isinstance(price, Exception):
                    print(f"⚠️ Excepción capturada: {price}")
                    return None
                return price if isinstance(price, (int, float)) and price is not None and price > 0 else None
            
            # Validar todos los precios
            ves_buy = safe_price(ves_buy)
            ves_sell = safe_price(ves_sell)
            cop_buy = safe_price(cop_buy)
            cop_sell = safe_price(cop_sell)
            brl_buy = safe_price(brl_buy)
            brl_sell = safe_price(brl_sell)

            print(f"📊 Precios base obtenidos:")
            print(f"   💰 VES/USDT: Buy={ves_buy}, Sell={ves_sell}")
            print(f"   💰 COP/USDT: Buy={cop_buy}, Sell={cop_sell}")
            print(f"   💰 BRL/USDT: Buy={brl_buy}, Sell={brl_sell}")

            rates: List[ExchangeRate] = []

            # === TASAS PRINCIPALES CON USDT ===
            if ves_buy:
                rates.append(ExchangeRate.create_safe(Currency.VES, Currency.USDT, ves_buy))
            if ves_sell:
                rates.append(ExchangeRate.create_safe(Currency.USDT, Currency.VES, ves_sell))
            if cop_buy:
                rates.append(ExchangeRate.create_safe(Currency.COP, Currency.USDT, cop_buy))
            if cop_sell:
                rates.append(ExchangeRate.create_safe(Currency.USDT, Currency.COP, cop_sell))
            if brl_buy:
                rates.append(ExchangeRate.create_safe(Currency.BRL, Currency.USDT, brl_buy))
            if brl_sell:
                rates.append(ExchangeRate.create_safe(Currency.USDT, Currency.BRL, brl_sell))

            # === TASAS DERIVADAS CON ZELLE ===
            print("💳 Calculando tasas derivadas con Zelle...")
            if ves_buy:
                rates.append(ExchangeRate.create_safe(Currency.VES, Currency.ZELLE, ves_buy, 5, inverse_percentage=True))
            if ves_sell:
                rates.append(ExchangeRate.create_safe(Currency.ZELLE, Currency.VES, ves_sell, 10))
            if cop_buy:
                rates.append(ExchangeRate.create_safe(Currency.COP, Currency.ZELLE, cop_buy, 10))
            if cop_sell:
                rates.append(ExchangeRate.create_safe(Currency.ZELLE, Currency.COP, cop_sell, 10))
            if brl_buy:
                rates.append(ExchangeRate.create_safe(Currency.BRL, Currency.ZELLE, brl_buy, 10))
            if brl_sell:
                rates.append(ExchangeRate.create_safe(Currency.ZELLE, Currency.BRL, brl_sell, 10))

            # === TASAS DERIVADAS CON PAYPAL ===
            print("💳 Calculando tasas derivadas con PayPal...")
            if ves_buy:
                rates.append(ExchangeRate.create_safe(Currency.VES, Currency.PAYPAL, ves_buy, 8, inverse_percentage=True))
            if ves_sell:
                rates.append(ExchangeRate.create_safe(Currency.PAYPAL, Currency.VES, ves_sell, 13))
            if cop_buy:
                rates.append(ExchangeRate.create_safe(Currency.COP, Currency.PAYPAL, cop_buy, 13))
            if cop_sell:
                rates.append(ExchangeRate.create_safe(Currency.PAYPAL, Currency.COP, cop_sell, 13))
            if brl_buy:
                rates.append(ExchangeRate.create_safe(Currency.BRL, Currency.PAYPAL, brl_buy, 13))
            if brl_sell:
                rates.append(ExchangeRate.create_safe(Currency.PAYPAL, Currency.BRL, brl_sell, 13))

            # === TASAS CRUZADAS ENTRE FIAT ===
            print("🔄 Calculando tasas cruzadas entre monedas fiat...")
            if all(x is not None for x in [ves_buy, ves_sell, cop_buy, cop_sell, brl_buy, brl_sell]):
                # VES <-> COP
                ves_to_cop = cop_sell / ves_buy if cop_sell and ves_buy else None
                cop_to_ves = ves_sell / cop_buy if ves_sell and cop_buy else None
                
                # VES <-> BRL
                ves_to_brl = brl_sell / ves_buy if brl_sell and ves_buy else None
                brl_to_ves = ves_sell / brl_buy if ves_sell and brl_buy else None
                
                # COP <-> BRL
                cop_to_brl = brl_sell / cop_buy if brl_sell and cop_buy else None
                brl_to_cop = cop_sell / brl_buy if cop_sell and brl_buy else None

                if ves_to_cop:
                    rates.append(ExchangeRate.create_safe(Currency.VES, Currency.COP, ves_to_cop, 8))
                if cop_to_ves:
                    rates.append(ExchangeRate.create_safe(Currency.COP, Currency.VES, cop_to_ves, 8))
                if ves_to_brl:
                    rates.append(ExchangeRate.create_safe(Currency.VES, Currency.BRL, ves_to_brl, 6))
                if brl_to_ves:
                    rates.append(ExchangeRate.create_safe(Currency.BRL, Currency.VES, brl_to_ves, 6))
                if cop_to_brl:
                    rates.append(ExchangeRate.create_safe(Currency.COP, Currency.BRL, cop_to_brl, 8))
                if brl_to_cop:
                    rates.append(ExchangeRate.create_safe(Currency.BRL, Currency.COP, brl_to_cop, 8))
            
            # Filtrar valores válidos
            valid_rates = [rate for rate in rates if rate is not None]
            valid_count = len(valid_rates)
            
            if valid_count > 0:
                print(f"✅ {valid_count} tasas calculadas exitosamente")
                print(f"📈 Resumen de tasas:")
                print(f"   🔹 USDT: {sum(1 for r in valid_rates if 'USDT' in [r.from_currency.value, r.to_currency.value])} tasas")
                print(f"   🔹 Zelle: {sum(1 for r in valid_rates if 'ZELLE' in [r.from_currency.value, r.to_currency.value])} tasas")
                print(f"   🔹 PayPal: {sum(1 for r in valid_rates if 'PAYPAL' in [r.from_currency.value, r.to_currency.value])} tasas")
                print(f"   🔹 Cruzadas: {sum(1 for r in valid_rates if all(c in ['VES', 'COP', 'BRL'] for c in [r.from_currency.value, r.to_currency.value]))} tasas")
                
                return valid_rates
            else:
                print("❌ No se pudieron obtener tasas válidas")
                return []
            
        except Exception as e:
            print(f"❌ Error obteniendo tasas: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def close(self):
        """Cerrar sesión HTTP"""
        if self.session:
            try:
                await self.session.close()
                print("🔒 Sesión HTTP cerrada correctamente")
            except Exception as e:
                print(f"❌ Error cerrando sesión: {e}")

    def __del__(self):
        """Destructor"""
        if hasattr(self, 'session') and self.session:
            try:
                asyncio.create_task(self.session.close())
            except:
                pass


# Versión síncrona como fallback
class BinanceP2PSyncScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Origin': 'https://p2p.binance.com',
            'Referer': 'https://p2p.binance.com/',
            'Content-Type': 'application/json',
        })

    def get_p2p_price_sync(self, fiat: str, crypto: str = 'USDT', trade_type: str = 'BUY', payment_method: str = None):
        """Versión síncrona para obtener precios"""
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        
        payload = {
            "page": 1,
            "rows": 5,
            "payTypes": [payment_method] if payment_method else [],
            "asset": crypto,
            "tradeType": trade_type,
            "fiat": fiat,
            "publisherType": None,
            "merchantCheck": False,
            "countries": []
        }
        
        try:
            response = self.session.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success') and data.get('data'):
                    ads = data['data']
                    if ads:
                        price = float(ads[0]['adv']['price'])
                        print(f"✅ Precio sync {fiat} {trade_type}: {price}")
                        return price
                        
            print(f"⚠️ No se pudo obtener precio sync para {fiat} {trade_type}")
            return None
            
        except Exception as e:
            print(f"❌ Error sync: {e}")
            return None

    def get_all_rates_sync(self):
        """Versión síncrona de get_all_rates"""
        try:
            print("🔄 Obteniendo tasas sync...")
            
            result = {
                'VES': {
                    'buy': self.get_p2p_price_sync('VES', 'USDT', 'BUY', 'Mercantil'),
                    'sell': self.get_p2p_price_sync('VES', 'USDT', 'SELL', 'Mercantil')
                },
                'COP': {
                    'buy': self.get_p2p_price_sync('COP', 'USDT', 'BUY', 'Bancolombia'),
                    'sell': self.get_p2p_price_sync('COP', 'USDT', 'SELL', 'Bancolombia')
                },
                'BRL': {
                    'buy': self.get_p2p_price_sync('BRL', 'USDT', 'BUY', 'PIX'),
                    'sell': self.get_p2p_price_sync('BRL', 'USDT', 'SELL', 'PIX')
                }
            }
            
            print(f"✅ Tasas sync obtenidas: {result}")
            return result
            
        except Exception as e:
            print(f"❌ Error obteniendo tasas sync: {e}")
            return {}