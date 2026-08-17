"""Global API throttle — minimum interval between broker API calls.

Preserved from the original engine (``_Throttle``): sequential requests only,
at most one call per ``min_seconds``.
"""

from __future__ import annotations

import time


class Throttle:
    """Enforces a minimum spacing between API calls."""

    def __init__(self, min_seconds: float = 0.5) -> None:
        self._min_seconds = min_seconds
        self._last: float = 0.0

    def wait(self) -> None:
        gap = self._min_seconds - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()
