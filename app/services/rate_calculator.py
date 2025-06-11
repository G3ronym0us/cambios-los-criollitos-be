from typing import Dict, List, Optional
from datetime import datetime
import logging

# Importar modelos
try:
    # Intenta import relativo primero
    from ..models.usuario import Usuario
    from ..models.coin_type import CoinType
    from ..models.changes import Changes, CalculateType, PercentageBase
except ImportError:
    # Fallback a import absoluto
    from models.usuario import Usuario
    from models.coin_type import CoinType
    from models.changes import Changes, CalculateType, PercentageBase

class RateCalculatorService:
    """
    Servicio para calcular y gestionar las tasas de cambio
    Centraliza toda la lógica de cálculo de tu sistema actual
    """
    
    def __init__(self):
        self.users: Dict[str, Usuario] = {}
        self.coins: Dict[str, CoinType] = {}
        self.changes: List[Changes] = []
        self.last_calculation: Optional[datetime] = None
        self.logger = logging.getLogger(__name__)
        
    def initialize_system(self):
        """Inicializar usuarios, monedas y relaciones de cambio"""
        self._create_users()
        self._create_coins()
        self._setup_changes()
        self.logger.info("Sistema de cálculo de tasas inicializado")
        
    def _create_users(self):
        """Crear todos los usuarios del sistema"""
        self.users = {
            "diohandres": Usuario("Diohandres", "+584249428608"),
            "nelson": Usuario("Nelson", "+584124640125"),
            "jean": Usuario("Jean", "+16204195618"),
            "dionis": Usuario("Dionis", "+573123146340"),
            "diodimar": Usuario("Diodimar", "+584267948636")
        }
        
    def _create_coins(self):
        """Crear todas las monedas del sistema"""
        self.coins = {
            "USDT": CoinType("Tether", "USDT", 1, 1),
            "VES": CoinType("Bolivar Venezolano", "VES", 0, 0, True, None, 'Mercantil', 10000),
            "COP": CoinType("Peso Colombiano", "COP", 0, 0, True, None, 'BancolombiaSA', 100000),
            "BRL": CoinType("Real Brasileño", "BRL", 0, 0, True, None, 'Pix', 100),
            "ZELLE": CoinType("Zelle", "Zelle", 0, 0, False),
            "PAYPAL": CoinType("Paypal", "Paypal", 0, 0, False)
        }
        
    def _setup_changes(self):
        """Configurar todas las relaciones de cambio"""
        # Referencias rápidas
        diohandres = self.users["diohandres"]
        nelson = self.users["nelson"]
        jean = self.users["jean"]
        dionis = self.users["dionis"]
        diodimar = self.users["diodimar"]
        
        ves = self.coins["VES"]
        cop = self.coins["COP"]
        brl = self.coins["BRL"]
        zelle = self.coins["ZELLE"]
        paypal = self.coins["PAYPAL"]
        
        # Establecer precios base para zelle y paypal basados en VES
        zelle.set_price(ves.from_price, ves.to_price)
        paypal.set_price(ves.from_price, ves.to_price)
        
        # Crear cambios principales
        zelle_to_ves = Changes(
            zelle, ves, 
            [PercentageBase(diohandres, 3.5), PercentageBase(jean, 3.5)], 
            2, CalculateType.MULTIPLY, system_percentage=3
        )
        
        paypal_to_ves = Changes(
            paypal, ves, 
            [PercentageBase(diohandres, 5), PercentageBase(jean, 5)], 
            2, CalculateType.MULTIPLY, system_percentage=3
        )
        
        cop_to_ves = Changes(
            cop, ves, 
            [PercentageBase(diohandres, 4), PercentageBase(dionis, 4)], 
            2, CalculateType.DIVIDE
        )
        
        ves_to_cop = Changes(
            ves, cop, 
            [PercentageBase(diohandres, 4), PercentageBase(dionis, 4)], 
            2, CalculateType.DIVIDE, inverse=True
        )
        
        brl_to_ves = Changes(
            brl, ves, 
            [PercentageBase(diohandres, 3), PercentageBase(diodimar, 3)], 
            2, CalculateType.CROSS
        )
        
        ves_to_brl = Changes(
            ves, brl, 
            [PercentageBase(diohandres, 3), PercentageBase(diodimar, 3)], 
            2, CalculateType.CROSS, inverse=True
        )
        
        zelle_to_brl = Changes(
            zelle, brl, 
            [PercentageBase(diohandres, 2.5), PercentageBase(jean, 2.5), PercentageBase(diodimar, 2)], 
            2, CalculateType.MULTIPLY, system_percentage=1
        )
        
        # Guardar cambios para referencia
        self.changes = [zelle_to_ves, paypal_to_ves, cop_to_ves, ves_to_cop, brl_to_ves, ves_to_brl, zelle_to_brl]
        
        # Asignar cambios a usuarios
        self._assign_changes_to_users(zelle_to_ves, paypal_to_ves, cop_to_ves, ves_to_cop, brl_to_ves, ves_to_brl, zelle_to_brl)
        
    def _assign_changes_to_users(self, zelle_to_ves, paypal_to_ves, cop_to_ves, ves_to_cop, brl_to_ves, ves_to_brl, zelle_to_brl):
        """Asignar cambios específicos a cada usuario"""
        
        # Diohandres - Todos los cambios
        diohandres = self.users["diohandres"]
        diohandres.add_change(zelle_to_ves)
        diohandres.add_change(paypal_to_ves)
        diohandres.add_change(cop_to_ves)
        diohandres.add_change(ves_to_cop)
        diohandres.add_change(brl_to_ves)
        diohandres.add_change(ves_to_brl)
        diohandres.add_change(zelle_to_brl)

        # Nelson - Zelle, Paypal y BRL
        nelson = self.users["nelson"]
        nelson.add_change(zelle_to_ves)
        nelson.add_change(paypal_to_ves)
        nelson.add_change(brl_to_ves)
        nelson.add_change(ves_to_brl)

        # Jean - Todos excepto zelle_to_brl
        jean = self.users["jean"]
        jean.add_change(zelle_to_ves)
        jean.add_change(paypal_to_ves)
        jean.add_change(cop_to_ves)
        jean.add_change(ves_to_cop)
        jean.add_change(brl_to_ves)
        jean.add_change(ves_to_brl)

        # Diodimar - BRL, Paypal y Zelle
        diodimar = self.users["diodimar"]
        diodimar.add_change(brl_to_ves)
        diodimar.add_change(ves_to_brl)
        diodimar.add_change(paypal_to_ves)
        diodimar.add_change(zelle_to_ves)

        # Dionis - Solo COP
        dionis = self.users["dionis"]
        dionis.add_change(cop_to_ves)
        dionis.add_change(ves_to_cop)
        
    def update_coin_prices(self, prices_data: Dict[str, Dict[str, float]]):
        """
        Actualizar precios de monedas desde datos de scraping
        
        Args:
            prices_data: {"VES": {"buy": 600.15, "sell": 1439.91}, ...}
        """
        for currency, prices in prices_data.items():
            if currency in self.coins and 'buy' in prices and 'sell' in prices:
                self.coins[currency].set_price(prices['buy'], prices['sell'])
                self.logger.info(f"Actualizado {currency}: compra={prices['buy']}, venta={prices['sell']}")
        
        # Actualizar precios dependientes (zelle y paypal siguen a VES)
        if 'VES' in prices_data:
            ves = self.coins['VES']
            self.coins['ZELLE'].set_price(ves.from_price, ves.to_price)
            self.coins['PAYPAL'].set_price(ves.from_price, ves.to_price)
            
        # Recalcular todas las tasas
        self.recalculate_all_rates()
        
    def recalculate_all_rates(self):
        """Recalcular todas las tasas de cambio"""
        for change in self.changes:
            change.calculate_price()
        
        self.last_calculation = datetime.now()
        self.logger.info("Todas las tasas recalculadas")
        
    def get_user_rates(self, user_id: str) -> Optional[Usuario]:
        """Obtener las tasas de un usuario específico"""
        return self.users.get(user_id)
        
    def get_all_users_rates(self) -> Dict[str, Usuario]:
        """Obtener las tasas de todos los usuarios"""
        return self.users
        
    def convert_currency(self, user_id: str, from_currency: str, to_currency: str, amount: float) -> Optional[Dict]:
        """
        Convertir cantidad de una moneda a otra para un usuario específico
        
        Returns:
            Dict con resultado de conversión o None si no es posible
        """
        user = self.users.get(user_id)
        if not user:
            return None
            
        # Buscar el cambio correspondiente
        for change in user.changes:
            if (change.from_coin.name == from_currency and 
                change.to_coin.name == to_currency):
                
                if change.rate is None:
                    return None
                    
                converted_amount = amount * change.rate
                return {
                    "original_amount": amount,
                    "converted_amount": round(converted_amount, 2),
                    "rate": change.rate,
                    "from_currency": from_currency,
                    "to_currency": to_currency,
                    "user": user.name,
                    "percentage": change.total_percentage,
                    "calculation_time": self.last_calculation
                }
        
        return None
        
    def get_available_currencies_for_user(self, user_id: str) -> List[str]:
        """Obtener lista de monedas disponibles para un usuario"""
        user = self.users.get(user_id)
        if not user:
            return []
            
        currencies = set()
        for change in user.changes:
            currencies.add(change.from_coin.name)
            currencies.add(change.to_coin.name)
            
        return list(currencies)
        
    def get_rate_between_currencies(self, user_id: str, from_currency: str, to_currency: str) -> Optional[float]:
        """Obtener tasa específica entre dos monedas para un usuario"""
        user = self.users.get(user_id)
        if not user:
            return None
            
        for change in user.changes:
            if (change.from_coin.name == from_currency and 
                change.to_coin.name == to_currency):
                return change.rate
                
        return None
        
    def get_system_summary(self) -> Dict:
        """Obtener resumen del estado del sistema"""
        total_rates = sum(len(user.changes) for user in self.users.values())
        
        currency_prices = {}
        for coin_name, coin in self.coins.items():
            if coin.is_searchable:
                currency_prices[coin_name] = {
                    "buy": coin.from_price,
                    "sell": coin.to_price
                }
        
        return {
            "users_count": len(self.users),
            "total_rates": total_rates,
            "currency_prices": currency_prices,
            "last_calculation": self.last_calculation,
            "available_currencies": list(self.coins.keys())
        }
        
    def add_user_percentage(self, user_id: str, from_currency: str, to_currency: str, percentage: float):
        """Agregar porcentaje personalizado a un cambio específico"""
        user = self.users.get(user_id)
        if not user:
            return False
            
        for change in user.changes:
            if (change.from_coin.name == from_currency and 
                change.to_coin.name == to_currency):
                change.add_percentage_base(user, percentage)
                change.calculate_price()
                return True
                
        return False
        
    def print_all_rates(self):
        """Imprimir todas las tasas (para debugging)"""
        print("\n" + "="*50)
        print("RESUMEN DE TASAS DE CAMBIO")
        print("="*50)
        
        # Precios base de monedas
        print("\nPrecios de compra de USDT en Binance P2P:")
        for coin_name, coin in self.coins.items():
            if coin.is_searchable:
                print(f"{coin.name}: {coin.from_price}")
        
        print("\nPrecios de venta de USDT en Binance P2P:")
        for coin_name, coin in self.coins.items():
            if coin.is_searchable:
                print(f"{coin.name}: {coin.to_price}")
        
        # Tasas por usuario
        for user_id, user in self.users.items():
            print(f"\n{'='*30}")
            print(f"Tasas para {user.name}:")
            print(f"{'='*30}")
            for change in user.changes:
                print(f"Cambio: {change.from_coin.name} a {change.to_coin.name} - {change.rate} - ({change.total_percentage}%)")
            
        print(f"\nÚltima actualización: {self.last_calculation}")
        print("="*50)