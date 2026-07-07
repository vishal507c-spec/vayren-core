from typing import Optional

from strategies.models.strategy import Strategy


class StrategyRegistry:
    """Registry for managing available strategies."""

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, name: str, strategy: Strategy) -> None:
        self._strategies[name] = strategy

    def get(self, name: str) -> Optional[Strategy]:
        return self._strategies.get(name)

    def list(self) -> list[str]:
        return list(self._strategies.keys())

    def remove(self, name: str) -> None:
        self._strategies.pop(name, None)

    def __len__(self) -> int:
        return len(self._strategies)

    def __iter__(self):
        return iter(self._strategies.values())
