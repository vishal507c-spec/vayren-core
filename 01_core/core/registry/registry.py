from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Generic named registry for services and handlers.

    Usage:
        services = Registry[object]()
        services.register("market_loader", loader)
        loader = services.get("market_loader")
    """

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T) -> None:
        if name in self._items:
            msg = f"Item already registered: {name}"
            raise ValueError(msg)
        self._items[name] = item

    def get(self, name: str) -> T:
        if name not in self._items:
            msg = f"Item not found: {name}"
            raise KeyError(msg)
        return self._items[name]

    def list(self) -> list[str]:
        return list(self._items.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Any:
        return iter(self._items.items())
