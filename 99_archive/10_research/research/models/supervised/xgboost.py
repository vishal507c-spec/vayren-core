from typing import Any, Optional


class XGBoostModel:
    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self._params = params or {}
        self._model: Any = None

    def train(self, X: Any, y: Any) -> None:
        pass

    def predict(self, X: Any) -> Any:
        return []

    def save(self, path: str) -> None:
        pass

    def load(self, path: str) -> None:
        pass
