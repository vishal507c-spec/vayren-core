from typing import Any


class RLAgent:
    def __init__(self, name: str = "default") -> None:
        self._name = name

    def act(self, state: Any) -> int:
        return 0

    def learn(self, state: Any, action: int, reward: float, next_state: Any, done: bool) -> None:
        pass
