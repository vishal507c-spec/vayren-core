from datetime import datetime, timezone
from typing import Self


class Timestamp:
    """UTC timestamp wrapper with exchange-compatible formatting."""

    _FORMAT_ISO = "%Y-%m-%dT%H:%M:%S.%fZ"
    _FORMAT_EXCHANGE = "%Y-%m-%d %H:%M:%S"

    def __init__(self, dt: datetime | None = None) -> None:
        if dt is None:
            dt = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        self._dt = dt.astimezone(timezone.utc)

    @classmethod
    def now(cls) -> Self:
        return cls(datetime.now(timezone.utc))

    @classmethod
    def from_iso(cls, value: str) -> Self:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return cls(dt)

    @classmethod
    def from_epoch(cls, epoch: float) -> Self:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return cls(dt)

    @property
    def dt(self) -> datetime:
        return self._dt

    @property
    def epoch(self) -> float:
        return self._dt.timestamp()

    @property
    def iso(self) -> str:
        return self._dt.strftime(self._FORMAT_ISO)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Timestamp):
            return NotImplemented
        return self._dt == other._dt

    def __lt__(self, other: Self) -> bool:
        return self._dt < other._dt

    def __le__(self, other: Self) -> bool:
        return self._dt <= other._dt

    def __gt__(self, other: Self) -> bool:
        return self._dt > other._dt

    def __ge__(self, other: Self) -> bool:
        return self._dt >= other._dt

    def __sub__(self, other: Self) -> float:
        return (self._dt - other._dt).total_seconds()

    def __str__(self) -> str:
        return self._dt.strftime(self._FORMAT_EXCHANGE)

    def __repr__(self) -> str:
        return f"Timestamp('{self.iso}')"
