from enum import Enum

class PairType(Enum):
    """
    Tipo de par de monedas según su método de cálculo.
    """
    BASE = "base"           # Tasa obtenida directamente de Binance (FIAT-CRYPTO)
    DERIVED = "derived"     # Tasa derivada de una base con porcentaje (ej: Zelle, PayPal)
    CROSS = "cross"         # Tasa cruzada entre dos FIATs usando USDT como intermediario

    @classmethod
    def get_all_values(cls) -> list[str]:
        """Retorna una lista con todos los valores de tipos de pares."""
        return [pair_type.value for pair_type in cls]

    @classmethod
    def is_valid(cls, pair_type: str) -> bool:
        """
        Verifica si un tipo de par es válido.

        Args:
            pair_type: Tipo de par a verificar

        Returns:
            bool: True si el tipo es válido, False en caso contrario
        """
        return pair_type.lower() in cls.get_all_values()
