from enum import Enum

class Currency(Enum):
    """
    Enumeración de las monedas disponibles en el sistema.
    """
    VES = "VES"  # Bolívar Venezolano
    COP = "COP"  # Peso Colombiano
    BRL = "BRL"  # Real Brasileño
    USDT = "USDT"  # Tether (USDT)
    ZELLE = "ZELLE"  # Zelle
    PAYPAL = "PAYPAL"  # PayPal

    @classmethod
    def get_all_values(cls) -> list[str]:
        """
        Retorna una lista con todos los valores de las monedas.
        """
        return [currency.value for currency in cls]

    @classmethod
    def is_valid(cls, currency: str) -> bool:
        """
        Verifica si una moneda es válida.
        
        Args:
            currency: Código de la moneda a verificar
            
        Returns:
            bool: True si la moneda es válida, False en caso contrario
        """
        return currency.upper() in cls.get_all_values()