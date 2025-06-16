from dataclasses import dataclass
from typing import Optional
from app.enums.currency_enun import Currency

@dataclass
class ExchangeRate:
    """
    Clase que representa una tasa de cambio entre dos monedas.
    
    Attributes:
        from_currency (Currency): Moneda de origen
        to_currency (Currency): Moneda de destino
        value (float): Valor de la tasa de cambio
    """
    from_currency: Currency
    to_currency: Currency
    value: float
    inverse: bool = False

    def __post_init__(self):
        """
        Validación post-inicialización para asegurar que el valor sea positivo.
        """
        if self.value <= 0:
            raise ValueError("El valor de la tasa de cambio debe ser positivo")

    @classmethod
    def create_safe(
        cls,
        from_currency: Currency,
        to_currency: Currency,
        value: Optional[float],
        percentage: Optional[float] = None,
        inverse_percentage: Optional[bool] = False,
        min_value: Optional[float] = None
    ) -> Optional['ExchangeRate']:
        """
        Crea una instancia de ExchangeRate de manera segura, retornando None si el valor es None o inválido.
        
        Args:
            from_currency (Currency): Moneda de origen
            to_currency (Currency): Moneda de destino
            value (Optional[float]): Valor de la tasa de cambio
            percentage (Optional[float]): Porcentaje de la tasa de cambio
        Returns:
            Optional[ExchangeRate]: Instancia de ExchangeRate o None si el valor es inválido
        """
        if value is None or not isinstance(value, (int, float)) or value <= 0:
            return None
        if percentage is not None:
            if inverse_percentage:
                value = value * (1 + (percentage / 100))
            else:
                value = value * (1 - (percentage / 100))
        return cls(from_currency=from_currency, to_currency=to_currency, value=value, inverse=inverse_percentage)

    def to_dict(self) -> dict:
        """
        Convierte la instancia a un diccionario.
        
        Returns:
            dict: Diccionario con los datos de la tasa de cambio
        """
        return {
            "from": self.from_currency.value,
            "to": self.to_currency.value,
            "value": self.value,
            "inverse": self.inverse
        } 