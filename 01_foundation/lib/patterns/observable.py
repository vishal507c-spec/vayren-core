from abc import ABC, abstractmethod
from typing import Any


class Observer(ABC):
    """Observer interface for event-based communication."""

    @abstractmethod
    def update(self, event: Any) -> None:
        ...


class Observable:
    """Subject in the Observer pattern.

    Usage:
        observable = Observable()
        observable.add_observer(my_observer)
        observable.notify(event_data)
    """

    def __init__(self) -> None:
        self._observers: list[Observer] = []

    def add_observer(self, observer: Observer) -> None:
        if observer not in self._observers:
            self._observers.append(observer)

    def remove_observer(self, observer: Observer) -> None:
        self._observers.remove(observer)

    def notify(self, event: Any) -> None:
        for observer in self._observers:
            observer.update(event)
