"""Composition service for the preserved historical download engine.

It owns no download logic: it runs ``data.historical_download_engine`` on a
worker thread, feeds it the UI's symbol/interval choice through the engine's
own ``symbols.csv`` input, captures its existing log lines for the UI and
projects the exact ``download`` snapshot section the Slint console consumes.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sqlite3
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from data import download_schema_compat as schema_compat
from data import historical_download_engine as engine_module

# Display interval -> the Kite interval id the preserved engine expects in
# symbols.csv (INTERVAL_LABEL in the original file).
_DISPLAY_TO_KITE = {
    "1m": "minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "60minute",
}

_QUICK_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "MAX": 3650}


class _LogCapture(logging.Handler):
    """Forward the engine's own log lines; no engine logging is rewritten."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        super().__init__(level=logging.INFO)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink(record.getMessage())


class HistoricalDownloadService:
    def __init__(self, data_dir: str, broker_manager: Any) -> None:
        self._data_dir = str(data_dir)
        self._broker_manager = broker_manager
        self._lock = threading.RLock()
        # The MARKET watchlist's canonical store is the ONLY symbol source.
        # It starts empty and is refreshed from the store on every snapshot.
        self._universe: list[str] = []
        self._selected: list[str] = []
        self._market_store: Any = None
        self._store_file_count = -1
        self._interval_items: list[str] = list(_DISPLAY_TO_KITE)
        self._interval = "15m"
        self._from_display = ""
        self._to_display = ""
        self._busy = False
        self._chunks = 0
        self._log: deque[str] = deque(maxlen=200)
        self._status: dict[str, Any] = self._empty_status()
        # SYSTEM UI is the ONLY broker authority. No selector state is kept here.
        self._broker_id = ""
        self._broker_block = ""
        self._broker_aborted = False
        self._monitor_stop = threading.Event()
        self._cancel = threading.Event()
        self._engine: Any = None
        self._thread: threading.Thread | None = None
        self._progress: Callable[[dict[str, Any]], None] | None = None
        self._capture: _LogCapture | None = None

    # ── setup ────────────────────────────────────────────────────────────────

    def set_progress_emitter(self, emitter: Callable[[dict[str, Any]], None] | None) -> None:
        self._progress = emitter

    def refresh_universe(self, symbols: list[str]) -> list[str]:
        """Adopt the MARKET watchlist's full universe, pruning stale choices.

        Availability never implies selection: existing selections are kept only
        when they are still in the watchlist, and nothing is auto-selected.
        """
        universe = sorted({str(item).strip().upper() for item in symbols if str(item).strip()})
        with self._lock:
            self._universe = universe
            self._selected = [symbol for symbol in self._selected if symbol in set(universe)]
        return universe

    def refresh_universe_from_store(self) -> list[str]:
        """Re-read the same per-symbol candle store the MARKET watchlist uses.

        The store's discovery is cached in a single ``MarketDataService`` and
        rebuilt only when the set of ``*.db`` files changes, so a watchlist
        change is reflected without re-probing every DB on every snapshot.
        """
        try:
            count = sum(1 for _ in Path(self._data_dir).glob("*.db"))
        except OSError:
            count = -1
        if self._market_store is None or count != self._store_file_count:
            try:
                from app.services.market_data_service import MarketDataService

                self._market_store = MarketDataService(self._data_dir)
                self._store_file_count = count
            except Exception:  # noqa: BLE001
                return []
        try:
            return self.refresh_universe(self._market_store.list_symbols())
        except Exception:  # noqa: BLE001
            return []

    def _connected_broker(self) -> str:
        """Resolve the authoritative SYSTEM broker; never fall back/switch."""
        manager = self._broker_manager
        if manager is None:
            raise RuntimeError("Connect broker in SYSTEM")
        try:
            snapshot = manager.snapshot() or {}
            broker_id = str(snapshot.get("active_broker") or "").strip()
            if not broker_id or broker_id == "paper":
                raise RuntimeError("Connect broker in SYSTEM")
            if not manager.is_connected(broker_id):
                raise RuntimeError(f"Connect broker in SYSTEM — {broker_id} is disconnected")
            get_session = getattr(manager, "get_session_snapshot", None)
            if not callable(get_session):
                raise RuntimeError(
                    f"Broker {broker_id} is selected but not authenticated in SYSTEM"
                )
            raw_session = get_session(broker_id) or {}
            session = raw_session if isinstance(raw_session, dict) else {}
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("Connect broker in SYSTEM — broker state unavailable") from None
        state = str(session.get("state") or "").strip()
        auth_valid = session.get("auth_valid")
        # ``auth_valid`` is the unified session's authority (DEGRADED keeps it
        # true: REST history still works while a websocket leg recovers).
        if auth_valid is False or (
            auth_valid is None and state and state not in ("CONNECTED", "DEGRADED")
        ):
            raise RuntimeError(f"Broker {broker_id} is selected but not authenticated in SYSTEM")
        return broker_id

    def refresh_broker_state(self) -> str:
        """Re-read SYSTEM on every snapshot so a switch/disconnect is visible."""
        try:
            broker_id = self._connected_broker()
        except RuntimeError as exc:
            with self._lock:
                self._broker_id = ""
                self._broker_block = str(exc)
            return ""
        with self._lock:
            self._broker_id = broker_id
            self._broker_block = ""
        return broker_id

    def _watch_broker(self, broker_id: str) -> None:
        """Abort safely if the SYSTEM broker changes or disconnects mid-run."""
        while not self._monitor_stop.wait(1.0):
            try:
                current = self._connected_broker()
            except RuntimeError:
                current = ""
            if current != broker_id:
                with self._lock:
                    self._broker_aborted = True
                    self._broker_block = "SYSTEM broker changed or disconnected — download stopped"
                    if self._engine is not None:
                        self._engine._abort = True
                self._note("SYSTEM broker changed or disconnected — download stopping safely.")
                return

    # ── engine driver ────────────────────────────────────────────────────────

    def _write_symbols_csv(self) -> None:
        path = engine_module.SYMBOLS_CSV
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["trading_symbol", "interval"])
            writer.writeheader()
            for symbol in self._selected:
                writer.writerow(
                    {
                        "trading_symbol": symbol,
                        "interval": _DISPLAY_TO_KITE.get(self._interval, "15minute"),
                    }
                )

    def _note(self, line: str) -> None:
        with self._lock:
            self._log.append(line)
        if self._progress is not None:
            self._progress({"kind": "log", "line": line})

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._busy:
                return {"ok": False, "reason": "A download is already running."}
            if not self._selected:
                return {"ok": False, "reason": "Select at least one stock first."}
            try:
                broker_id = self._connected_broker()
            except Exception as exc:
                self._broker_block = str(exc)
                self._status = self._empty_status()
                self._status["mode"] = "failed"
                self._status["error_line"] = str(exc)
                return {"ok": False, "reason": str(exc)}
            self._broker_block = ""

            # Configuration injection only: the engine's CODE is untouched.
            # Using VAYREN's resolved data dir makes the UI and engine read the
            # same per-symbol candle DBs (legacy local default is preserved by
            # the engine when no data dir is supplied).
            engine_module.DATA_FOLDER = self._data_dir
            engine_module.LOCK_FILE = os.path.join(self._data_dir, "hist_engine.lock")
            engine_module.SYMBOLS_CSV = os.path.join(self._data_dir, "symbols.csv")
            engine_module.configure_broker_context(self._broker_manager, self._data_dir)
            # Legacy candle DBs predate the boundary columns the engine reads.
            # Repair in place (with a one-time backup) BEFORE the engine opens
            # them; the preserved engine itself is untouched.
            migration = schema_compat.ensure_download_schema(self._data_dir)
            if migration["repaired"] or migration["failed"]:
                self._note(
                    "Database compatibility: "
                    f"{len(migration['repaired'])} DB(s) migrated, "
                    f"{len(migration['failed'])} failed."
                )
            self._write_symbols_csv()

            self._cancel.clear()
            self._chunks = 0
            self._busy = True
            self._log.clear()
            self._status = self._empty_status()
            self._status.update(
                {
                    "mode": "running",
                    "run_status": "Downloading",
                    "run_symbol": ", ".join(self._selected[:6]),
                    "run_interval": self._interval,
                    "run_range": "Derived from candle DBs (10 years)",
                    "progress_note": (f"Downloading with {broker_id} connected in SYSTEM…"),
                }
            )
            engine = engine_module.HistoricalDownloadEngine()
            self._engine = engine
            self._broker_aborted = False
            self._monitor_stop.clear()
            self._capture = _LogCapture(self._on_engine_log)
            engine_module.log.addHandler(self._capture)

            def _run() -> None:
                monitor = threading.Thread(
                    target=self._watch_broker,
                    args=(broker_id,),
                    name="historical-download-broker-watch",
                    daemon=True,
                )
                monitor.start()
                try:
                    engine.run()
                    if self._broker_aborted:
                        self._finish("failed", self._broker_block)
                    else:
                        self._finish("complete", "Download complete")
                except Exception as exc:  # engine already logs its own failure
                    self._finish("failed", str(exc))
                finally:
                    self._monitor_stop.set()

            self._thread = threading.Thread(target=_run, name="historical-download", daemon=True)
            self._thread.start()
            return {"ok": True, "broker": broker_id}

    def _on_engine_log(self, line: str) -> None:
        with self._lock:
            self._log.append(line)
            if "chunk" in line.lower():
                self._chunks += 1
                expected = max(1, len(self._selected) * 20)
                self._status["progress_pct"] = min(99.0, self._chunks * 100.0 / expected)
                self._status["progress_note"] = line
        if self._progress is not None:
            self._progress({"kind": "log", "line": line})

    def _finish(self, mode: str, note: str) -> None:
        with self._lock:
            self._busy = False
            self._status["mode"] = mode
            self._status["progress_pct"] = (
                100.0 if mode == "complete" else self._status.get("progress_pct", 0.0)
            )
            self._status["progress_note"] = note
            self._status["run_status"] = note
            if mode == "failed":
                self._status["error_line"] = note
                if self._broker_aborted:
                    self._broker_block = note
            if self._capture is not None:
                engine_module.log.removeHandler(self._capture)
                self._capture = None
        if self._progress is not None:
            self._progress({"kind": "done", "mode": mode, "note": note})

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if not self._busy or self._engine is None:
                return {"ok": False, "reason": "No download is running."}
            self._engine._abort = True  # existing engine abort flag
            self._cancel.set()
            self._status["progress_note"] = "Cancellation requested…"
            return {"ok": True}

    # ── UI wires ─────────────────────────────────────────────────────────────

    def handle_wire(self, wire: str) -> dict[str, Any]:
        if wire == "dl:download":
            return self.start()
        if wire == "dl:retry":
            return self.start()
        if wire == "dl:cancel":
            return self.cancel()
        if wire == "dl:log-clear":
            with self._lock:
                self._log.clear()
            return {"ok": True}
        with self._lock:
            if wire.startswith("dl:select:"):
                symbol = wire.split(":", 2)[-1]
                if symbol in self._selected:
                    self._selected.remove(symbol)
                elif symbol in self._universe:
                    self._selected.append(symbol)
                self._selected.sort()
            elif wire == "dl:select-all":
                self._selected = list(self._universe)
            elif wire == "dl:clear-all":
                self._selected = []
            elif wire.startswith("dl:interval:"):
                try:
                    index = int(wire.rsplit(":", 1)[-1])
                    self._interval = self._interval_items[index]
                except (ValueError, IndexError):
                    pass
            elif wire.startswith("dl:quick:"):
                caption = wire.rsplit(":", 1)[-1]
                days = _QUICK_DAYS.get(caption)
                if days:
                    today = datetime.now()
                    self._from_display = (today - timedelta(days=days)).strftime("%d %b %Y")
                    self._to_display = today.strftime("%d %b %Y")
                self._note(
                    f"UI range {caption} is display-only; the engine derives its own coverage."
                )
            elif wire.startswith("dl:day:"):
                self._note("UI date is display-only; the engine derives its own coverage.")
            elif wire in ("dl:coverage", "dl:view-coverage"):
                self._note("Coverage is derived from candle DBs on every engine start.")
        return {"ok": True}

    def _coverage_text(self) -> str:
        """Read-only projection of the range the PRESERVED engine will use.

        The engine derives its target from ``MAX_HISTORY_YEARS`` and fills
        head/tail gaps from the per-symbol candle DBs. Nothing here changes
        that logic; this only reports the facts from those DBs so the UI's
        display-only FROM/TO controls cannot mislead the operator.
        """
        if not self._selected:
            return "Select stocks to see engine-derived coverage."
        target_from = engine_module.target_start_dt().date().isoformat()
        target_to = datetime.now().date().isoformat()
        earliest: str | None = None
        latest: str | None = None
        for symbol in self._selected:
            safe = re.sub(r"[^A-Za-z0-9_]", "", symbol)
            path = os.path.join(self._data_dir, f"{safe}.db")
            if not os.path.isfile(path):
                continue
            conn = None
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                row = conn.execute(
                    "SELECT MIN(candle_time), MAX(candle_time) FROM ohlcv"
                ).fetchone()
            except sqlite3.Error:
                continue
            finally:
                if conn is not None:
                    conn.close()
            if not row:
                continue
            for value in row:
                if not isinstance(value, str) or not value:
                    continue
                day = value[:10]
                earliest = day if earliest is None else min(earliest, day)
                latest = day if latest is None else max(latest, day)
        prefix = f"Engine-derived from candle DBs: target {target_from} to {target_to}"
        if earliest is None or latest is None:
            return f"{prefix}; no candles yet. FROM/TO controls are display-only."
        head = f"; missing head {target_from}..{earliest}" if target_from < earliest else ""
        tail = f"; missing tail {latest}..{target_to}" if latest < target_to else ""
        return (
            f"{prefix}; existing {earliest} to {latest}{head}{tail}. "
            "FROM/TO controls are display-only."
        )

    # ── snapshot (exact contract consumed by apply_download_snapshot) ────────

    @staticmethod
    def _empty_status() -> dict[str, Any]:
        return {
            "mode": "idle",
            "run_status": "",
            "run_symbol": "",
            "run_interval": "",
            "run_range": "",
            "run_chunk": "",
            "run_rows": "",
            "run_coverage": "",
            "progress_pct": 0.0,
            "progress_note": "",
            "perf_rows": "",
            "perf_elapsed": "",
            "perf_eta": "",
            "perf_size": "",
            "complete_status": "",
            "complete_rows": "",
            "complete_coverage": "",
            "complete_duration": "",
            "complete_size": "",
            "error_line": "",
            "error_detail": "",
            "cov_symbol": "",
            "cov_interval": "",
            "cov_range": "",
            "cov_coverage": "",
            "cov_rows": "",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status)
            error = self._broker_block or ("" if self._selected else "Select at least one stock.")
            plan = {
                "stocks": str(len(self._selected)),
                "interval": self._interval,
                "range": self._coverage_text(),
                "days": "",
                "rows": "",
                "error": error,
            }
            return {
                "busy": self._busy,
                "interval_items": list(self._interval_items),
                "interval_index": max(0, self._interval_items.index(self._interval)),
                "universe": list(self._universe),
                "selected": list(self._selected),
                "from_display": self._from_display,
                "to_display": self._to_display,
                "plan": plan,
                "status": status,
                "log": list(self._log),
            }


__all__ = ["HistoricalDownloadService"]
