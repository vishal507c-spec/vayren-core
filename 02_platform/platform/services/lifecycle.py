from logging import getLogger

from platform.models.engine import Engine
from platform.models.mode import Mode

logger = getLogger(__name__)


class LifecycleManager:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def start(self) -> None:
        logger.info("Starting engine in %s mode", self._engine.mode)
        self._engine.start()

    def stop(self) -> None:
        logger.info("Stopping engine")
        self._engine.stop()

    def switch_mode(self, mode: Mode) -> None:
        was_running = self._engine.is_running
        if was_running:
            self.stop()
        self._engine._mode = mode
        logger.info("Switched to %s mode", mode)
        if was_running:
            self.start()
