"""InstrumentResolver — NSE symbol → instrument token map (preserved)."""

from __future__ import annotations

import logging

from data.throttle import Throttle

log = logging.getLogger("HistDownloadEngine")


class InstrumentResolver:
    def __init__(self, settings, kite) -> None:
        self._settings = settings
        self._kite = kite
        self._throttle = Throttle(settings.min_inter_call_seconds)
        self._map: dict[str, int] = {}

    def fetch(self) -> None:
        log.info(f"Fetching {self._settings.exchange} instruments …")
        self._throttle.wait()
        for inst in self._kite.instruments(self._settings.exchange):
            sym = inst.get("tradingsymbol", "").strip().upper()
            tok = inst.get("instrument_token")
            if sym and tok:
                self._map[sym] = int(tok)
        log.info(f"Instrument map: {len(self._map)} symbols.")

    def map(self) -> dict[str, int]:
        return dict(self._map)
