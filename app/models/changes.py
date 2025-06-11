from enum import Enum
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .coin_type import CoinType
    from .usuario import Usuario

class CalculateType(Enum):
    MULTIPLY = 'multiply'  # Para casos como zelle_to_ves y paypal_to_ves donde se multiplica por un factor
    DIVIDE = 'divide'    # Para casos como cop_to_ves donde se divide
    CROSS = 'cross'     # Para casos como brl_to_ves donde hay cálculo cruzado con otra moneda

class PercentageBase:
    def __init__(self, user: 'Usuario', percentage: float):
        self.user = user
        self.percentage = percentage

class Changes:
    def __init__(
        self, 
        from_coin: 'CoinType', 
        to_coin: 'CoinType', 
        percentage_base: List[PercentageBase], 
        gift_percentage: float, 
        calculate_type: CalculateType, 
        inverse: bool = False, 
        system_percentage: float = 0
    ):
        self.rate: float = None
        self.from_coin = from_coin
        self.to_coin = to_coin
        self.percentage_base = percentage_base
        self.gift_percentage = gift_percentage
        self.calculate_type = calculate_type
        self.total_percentage = 0
        self.system_percentage = system_percentage
        self.inverse = inverse
        self.calculate_total_percentage()
        self.calculate_price()

    def calculate_total_percentage(self):
        self.total_percentage = self.system_percentage
        for percentage_base in self.percentage_base:
            self.total_percentage += percentage_base.percentage

    def add_percentage_base(self, user: 'Usuario', percentage: float):
        self.percentage_base.append(PercentageBase(user, percentage))
        self.calculate_total_percentage()

    def calculate_price(self):
        match self.calculate_type:
            case CalculateType.MULTIPLY:
                self.rate = round(self.to_coin.to_price * (1 - self.total_percentage / 100), 2)
            case CalculateType.DIVIDE:
                if self.inverse:
                    self.rate = round(self.to_coin.to_price / self.from_coin.from_price * (1 - self.total_percentage / 100), 2)
                else:
                    self.rate = round(self.from_coin.from_price / self.to_coin.to_price / (1 - self.total_percentage / 100), 2)
            case CalculateType.CROSS:
                if self.inverse:
                    self.rate = round(self.from_coin.from_price / self.to_coin.to_price / (1 - self.total_percentage / 100), 2)
                else:
                    self.rate = round(self.to_coin.to_price / self.from_coin.from_price * (1 - self.total_percentage / 100), 2)
            case _:
                print(f"No se puede calcular el precio para el tipo de cálculo: {self.calculate_type}")
                self.rate = None