from typing import Any, TypeVar

T = TypeVar("T")


class Container:
    """Simple dependency injection container."""

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}

    def register(self, name: str, instance: Any) -> None:
        self._instances[name] = instance

    def get(self, name: str) -> Any:
        if name not in self._instances:
            msg = f"Service not registered: {name}"
            raise KeyError(msg)
        return self._instances[name]

    def has(self, name: str) -> bool:
        return name in self._instances

    def clear(self) -> None:
        self._instances.clear()
