from datetime import datetime, timezone


class Clock:
    """Clock that adapts to runtime mode (real or simulated)."""

    def __init__(self, simulated: bool = False) -> None:
        self._simulated = simulated
        self._current_time: datetime | None = None

    @property
    def now(self) -> datetime:
        if self._simulated and self._current_time:
            return self._current_time
        return datetime.now(timezone.utc)

    def set_time(self, dt: datetime) -> None:
        self._current_time = dt

    def advance(self, seconds: float) -> None:
        if self._current_time:
            self._current_time = self._current_time.replace(tzinfo=timezone.utc)
            import timedelta
            self._current_time += timedelta(seconds=seconds)

    @property
    def is_simulated(self) -> bool:
        return self._simulated
