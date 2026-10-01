"""FetchEngine — the retry / rate-limit wrapper around kite historical_data.

Zerodha adapter internals: Kite interval ids and instrument tokens stay in
this package. Preserved from the original engine: 5 attempts, exponential
backoff on 429, emergency stop after MAX_CONSECUTIVE_429 consecutive 429s,
token-expiry sentinel detection. Delays come from settings (zero for tests).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from broker.common.throttle import Throttle
from broker.interfaces import RATE_LIMITED, TOKEN_EXPIRED, ProviderError

log = logging.getLogger("HistDownloadEngine")


class FetchEngine:
    def __init__(self, settings: Any, kite: Any) -> None:
        self._settings = settings
        self._kite = kite
        min_sec = getattr(settings, "min_inter_call_seconds", 0.5)
        self._throttle = Throttle(min_sec)
        self._consec_429 = 0

    def fetch(
        self, token: int, interval: str, from_dt: datetime, to_dt: datetime, chunk_num: int = 0
    ):
        """Returns list of candles, or TOKEN_EXPIRED / RATE_LIMITED sentinels."""
        max_retries = getattr(self._settings, "max_retries", 5)
        max_consecutive_429 = getattr(self._settings, "max_consecutive_429", 5)
        retry_delay_s = getattr(self._settings, "retry_delay_s", 1.0)
        for attempt in range(1, max_retries + 1):
            try:
                self._throttle.wait()
                data = self._kite.historical_data(
                    instrument_token=token,
                    from_date=from_dt,
                    to_date=to_dt,
                    interval=interval,
                    continuous=False,
                    oi=False,
                )
                self._consec_429 = 0
                return data or []

            except Exception as exc:
                msg = str(exc)
                lowered = msg.lower()
                token_dead = (
                    "TokenException" in msg
                    or "token expired" in lowered
                    or "invalid token" in lowered
                )
                if token_dead:
                    log.error(f"[Fetch] Token expired chunk={chunk_num}: {exc}")
                    return TOKEN_EXPIRED
                if "429" in msg or "too many requests" in lowered or "rate limit" in lowered:
                    self._consec_429 += 1
                    if self._consec_429 >= max_consecutive_429:
                        log.error(
                            f"[Fetch] RATE LIMIT — sync stopped "
                            f"({self._consec_429} consecutive 429 errors)."
                        )
                        return RATE_LIMITED
                    time.sleep(min(retry_delay_s * (2**attempt), 60))
                    continue
                self._consec_429 = 0
                wait = retry_delay_s * attempt
                log.warning(f"[Fetch] attempt={attempt}: {exc} — retry in {wait}s")
                if attempt < max_retries:
                    time.sleep(wait)
                else:
                    log.error(f"[Fetch] All retries exhausted chunk={chunk_num}.")
                    raise ProviderError(f"fetch failed after {attempt} attempts: {exc}") from exc
        raise ProviderError("fetch failed: retries exhausted")


__all__ = ["FetchEngine"]
