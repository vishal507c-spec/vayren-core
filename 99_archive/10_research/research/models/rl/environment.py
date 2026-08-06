from typing import Any


class TradingEnvironment:
    def __init__(self, symbol: str = "", initial_capital: float = 100000.0) -> None:
        self._symbol = symbol
        self._capital = initial_capital
        self._position = 0
        self._step = 0

    def reset(self) -> Any:
        self._position = 0
        self._step = 0
        return {"capital": self._capital, "position": 0, "price": 0}

    def step(self, action: int) -> tuple[Any, float, bool]:
        self._step += 1
        reward = 0.0
        done = self._step >= 100
        return {"capital": self._capital, "position": self._position, "step": self._step}, reward, done
