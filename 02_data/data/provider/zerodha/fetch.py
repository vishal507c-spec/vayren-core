"""FetchEngine — the retry / rate-limit wrapper around kite historical_data.

Zerodha adapter internals: Kite interval ids and instrument tokens stay in
this package. Preserved from the original engine: 5 attempts, exponential
backoff on 429, emergency stop after MAX_CONSECUTIVE_429 consecutive 429s,
token-expiry sentinel detection. Delays come from ``DownloadSettings`` (zero
for tests).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from data.provider.contract import RATE_LIMITED, TOKEN_EXPIRED
from data.throttle import Throttle

log = logging.getLogger("HistDownloadEngine")


class FetchEngine:
    def __init__(self, settings, kite) -> None:
        self._settings = settings
        self._kite = kite
        self._throttle = Throttle(settings.min_inter_call_seconds)
        self._consec_429 = 0

    def fetch(
        self, token: int, interval: str, from_dt: datetime, to_dt: datetime, chunk_num: int = 0
    ):
        """Returns list of candles, or TOKEN_EXPIRED / RATE_LIMITED sentinels."""
        for attempt in range(1, self._settings.max_retries + 1):
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
                if "TokenException" in msg or "invalid" in msg.lower():
                    log.error(f"[Fetch] Token expired chunk={chunk_num}: {exc}")
                    return TOKEN_EXPIRED
                if "429" in msg or "rate" in msg.lower():
                    self._consec_429 += 1
                    if self._consec_429 >= self._settings.max_consecutive_429:
                        log.error(
                            f"[Fetch] RATE LIMIT — sync stopped "
                            f"({self._consec_429} consecutive 429 errors)."
                        )
                        return RATE_LIMITED
                    time.sleep(self._settings.retry_delay_s * (2**attempt))
                    continue
                self._consec_429 = 0
                wait = self._settings.retry_delay_s * attempt
                log.warning(f"[Fetch] attempt={attempt}: {exc} — retry in {wait}s")
                if attempt < self._settings.max_retries:
                    time.sleep(wait)
                else:
                    log.error(f"[Fetch] All retries exhausted chunk={chunk_num}.")
                    return []
        return []
