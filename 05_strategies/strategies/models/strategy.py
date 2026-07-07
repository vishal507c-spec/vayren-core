from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class Strategy(ABC):
    """Base class for all trading strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def should_enter(self, context: Any) -> bool:
        ...

    @abstractmethod
    def should_exit(self, context: Any) -> bool:
        ...

    @abstractmethod
    def generate_orders(self, context: Any) -> list[Any]:
        ...

    def on_start(self) -> None:
        ...

    def on_stop(self) -> None:
        ...


@dataclass
class StrategyMetadata:
    name: str
    version: str = "1.0.0"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
