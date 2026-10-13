"""Operator-triggered candle backfill for the configured universe.

The readiness gate asks a real question — does the store hold bars for the
instruments this strategy trades — and on a fresh host the honest answer is
no: nothing in the product ever populated it, so every symbol reported
NOT FOUND. This service fills it with what the venue actually reported, and
reports exactly what it did per symbol.

It is deliberately NOT automatic. Fetching history is network work against
a live account, so it runs only when an operator asks for it, and it never
runs while a strategy session is live (a store rewrite under a running
strategy would change the bars an in-flight decision is standing on).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from data.candle_store import write_candles

logger = logging.getLogger(__name__)

#: Provider resolutions this service will ask for, keyed by our timeframe label.
RESOLUTION_BY_TIMEFRAME = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "20m": "20",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "D",
}


def _bare(symbol: str) -> str:
    """``NSE:KAYNES`` -> ``KAYNES`` — the store's per-file key.

    Must stay identical to the reader's rule
    (``live_trading_service._clean_symbol``): strip the venue prefix, trim,
    upper-case. If the two ever drift, bars are written to a filename the
    reader never discovers.
    """
    return str(symbol or "").split(":")[-1].strip().upper()


def sync_candles(
    data_dir: str,
    symbols: list[str],
    broker_manager: Any,
    *,
    timeframe: str = "30m",
    days: int = 60,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Backfill real candles for ``symbols``; returns what actually happened.

    One row per symbol in ``results``: how many bars were stored, how many
    repeated bars the venue sent, how many bars were rejected as invalid, and
    the first/last stored stamp. A symbol that could not be fetched carries
    its own reason — the run never reports success for work it did not do.
    """
    wanted = [_bare(s) for s in symbols if _bare(s)]
    report: dict[str, Any] = {
        "requested": list(wanted),
        "timeframe": timeframe,
        "resolution": RESOLUTION_BY_TIMEFRAME.get(str(timeframe).lower(), ""),
        "written_total": 0,
        "results": [],
        "detail": "",
    }
    if not wanted:
        report["detail"] = "no instruments configured"
        return report
    if not report["resolution"]:
        report["detail"] = f"unsupported timeframe {timeframe!r}"
        return report
    if broker_manager is None:
        report["detail"] = "no broker manager — cannot reach the venue"
        return report

    snapshot = broker_manager.snapshot() or {}
    broker_id = str(snapshot.get("active_broker") or "" or "")
    if not broker_id or broker_id == "paper":
        broker_id = "fyers"
    adapter = broker_manager.read_only_adapter(broker_id)
    if adapter is None:
        report["detail"] = f"no authenticated session for {broker_id}"
        return report

    now = float(now_epoch if now_epoch is not None else time.time())
    start = now - float(days) * 86400.0
    for symbol in wanted:
        try:
            rows = adapter.history(symbol, report["resolution"], int(start), int(now))
        except Exception as exc:  # noqa: BLE001
            report["results"].append(
                {
                    "symbol": symbol,
                    "status": "FAILED",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        written = write_candles(data_dir, symbol, list(rows))
        report["written_total"] += written.written
        report["results"].append(
            {
                "symbol": symbol,
                "status": "STORED" if written.written else "NO_DATA",
                "written": written.written,
                "duplicates": written.duplicates,
                "rejected": written.rejected,
                "first": written.first,
                "last": written.last,
                "path": written.path,
                "detail": "" if written.written else "venue returned no candles",
            }
        )

    stored = sum(1 for r in report["results"] if r["status"] == "STORED")
    report["detail"] = f"{stored}/{len(wanted)} instruments populated from the venue"
    return report


__all__ = ["RESOLUTION_BY_TIMEFRAME", "sync_candles"]
