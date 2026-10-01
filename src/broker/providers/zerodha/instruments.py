"""InstrumentResolver — NSE symbol → instrument token map (preserved)."""

from __future__ import annotations

import logging
from typing import Any

from broker.common.throttle import Throttle

log = logging.getLogger("HistDownloadEngine")


class InstrumentResolver:
    def __init__(self, settings: Any, kite: Any) -> None:
        self._settings = settings
        self._kite = kite
        min_sec = getattr(settings, "min_inter_call_seconds", 0.5)
        self._throttle = Throttle(min_sec)
        self._map: dict[str, int] = {}

    def fetch(self) -> None:
        exchange = getattr(self._settings, "exchange", "NSE")
        log.info(f"Fetching {exchange} instruments …")
        self._throttle.wait()
        for inst in self._kite.instruments(exchange):
            sym = inst.get("tradingsymbol", "").strip().upper()
            tok = inst.get("instrument_token")
            if sym and tok:
                self._map[sym] = int(tok)
        log.info(f"Instrument map: {len(self._map)} symbols.")

    def map(self) -> dict[str, int]:
        return dict(self._map)


__all__ = ["InstrumentResolver"]
