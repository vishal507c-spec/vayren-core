from typing import Any, Optional


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, Any] = {}

    def register(self, name: str, model: Any) -> None:
        self._models[name] = model

    def get(self, name: str) -> Optional[Any]:
        return self._models.get(name)

    def list(self) -> list[str]:
        return list(self._models.keys())
