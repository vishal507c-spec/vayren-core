from enum import Enum
from typing import Optional

from platform.models.mode import Mode


class Engine:
    """Runtime engine that coordinates domain execution."""

    def __init__(self, mode: Mode = Mode.BACKTEST) -> None:
        self._mode = mode
        self._running = False

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    @property
    def is_live(self) -> bool:
        return self._mode == Mode.LIVE

    @property
    def is_paper(self) -> bool:
        return self._mode == Mode.PAPER

    @property
    def is_backtest(self) -> bool:
        return self._mode == Mode.BACKTEST
