from decimal import Decimal, ROUND_HALF_UP
from typing import Self


class Currency:
    """Immutable currency value with precision handling."""

    def __init__(self, amount: str | int | float | Decimal, currency: str = "USD") -> None:
        self._amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self._currency = currency.upper()

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> str:
        return self._currency

    def __add__(self, other: Self) -> Self:
        self._validate_currency(other)
        return self.__class__(str(self._amount + other._amount), self._currency)

    def __sub__(self, other: Self) -> Self:
        self._validate_currency(other)
        return self.__class__(str(self._amount - other._amount), self._currency)

    def __mul__(self, factor: int | float | Decimal) -> Self:
        result = (self._amount * Decimal(str(factor))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self.__class__(str(result), self._currency)

    def __truediv__(self, factor: int | float | Decimal) -> Self:
        result = (self._amount / Decimal(str(factor))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self.__class__(str(result), self._currency)

    def __neg__(self) -> Self:
        return self.__class__(str(-self._amount), self._currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Currency):
            return NotImplemented
        return self._amount == other._amount and self._currency == other._currency

    def __lt__(self, other: Self) -> bool:
        self._validate_currency(other)
        return self._amount < other._amount

    def __le__(self, other: Self) -> bool:
        self._validate_currency(other)
        return self._amount <= other._amount

    def __gt__(self, other: Self) -> bool:
        self._validate_currency(other)
        return self._amount > other._amount

    def __ge__(self, other: Self) -> bool:
        self._validate_currency(other)
        return self._amount >= other._amount

    def __str__(self) -> str:
        return f"{self._currency} {self._amount:,.2f}"

    def __repr__(self) -> str:
        return f"Currency({str(self._amount)}, '{self._currency}')"

    def _validate_currency(self, other: Self) -> None:
        if self._currency != other._currency:
            msg = f"Currency mismatch: {self._currency} != {other._currency}"
            raise ValueError(msg)
