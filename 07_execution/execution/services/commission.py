class CommissionCalculator:
    def __init__(self, rate: float = 0.0, min_commission: float = 0.0, per_share: float = 0.0) -> None:
        self._rate = rate
        self._min = min_commission
        self._per_share = per_share

    def calculate(self, quantity: int, price: float) -> float:
        commission = (quantity * price * self._rate) + (quantity * self._per_share)
        return max(commission, self._min)

    @classmethod
    def zero(cls) -> "CommissionCalculator":
        return cls()

    @classmethod
    def fixed_per_trade(cls, amount: float) -> "CommissionCalculator":
        return cls(rate=0.0, min_commission=amount)
