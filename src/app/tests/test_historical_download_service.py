"""Composition tests: preserved engine driven by the SYSTEM-connected broker."""

from __future__ import annotations

import csv
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.services.historical_download_service import HistoricalDownloadService
from data import historical_download_engine as engine_module


class _Manager:
    def __init__(
        self,
        broker_id: str = "fyers",
        connected: bool = True,
        auth_valid: bool = True,
    ) -> None:
        self.broker_id = broker_id
        self.connected = connected
        self.auth_valid = auth_valid

    def snapshot(self) -> dict:
        return {"active_broker": self.broker_id}

    def is_connected(self, broker_id: str) -> bool:
        return self.connected and broker_id == self.broker_id

    def get_session_snapshot(self, _broker_id: str) -> dict:
        return {
            "state": "CONNECTED" if self.connected and self.auth_valid else "CHECKING",
            "auth_valid": self.auth_valid,
        }

    def read_only_adapter(self, _broker_id: str) -> Any:
        return object()


@pytest.fixture
def service(tmp_path: Path) -> HistoricalDownloadService:
    created = HistoricalDownloadService(str(tmp_path), _Manager())
    created.refresh_universe(["KAYNES", "KEC", "ZEEL"])
    return created


def test_snapshot_matches_the_rust_download_contract(service: HistoricalDownloadService) -> None:
    snap = service.snapshot()
    for key in (
        "busy",
        "interval_items",
        "interval_index",
        "universe",
        "selected",
        "from_display",
        "to_display",
        "plan",
        "status",
        "log",
    ):
        assert key in snap
    # No duplicate broker selector / credentials state is projected.
    assert "brokers" not in snap
    assert "broker_index" not in snap
    assert "credentials" not in snap
    assert snap["interval_items"] == ["1m", "5m", "15m", "30m", "1h"]
    assert snap["universe"] == ["KAYNES", "KEC", "ZEEL"]
    assert snap["selected"] == []
    assert set(snap["plan"]) == {"stocks", "interval", "range", "days", "rows", "error"}
    for key in ("mode", "progress_pct", "error_line"):
        assert key in snap["status"]


def test_selection_and_interval_wires_update_the_snapshot(
    service: HistoricalDownloadService,
) -> None:
    service.handle_wire("dl:select-all")
    assert service.snapshot()["selected"] == ["KAYNES", "KEC", "ZEEL"]
    service.handle_wire("dl:select:ZEEL")
    assert service.snapshot()["selected"] == ["KAYNES", "KEC"]
    service.handle_wire("dl:interval:3")
    assert service.snapshot()["interval_index"] == 3
    service.handle_wire("dl:clear-all")
    assert service.snapshot()["selected"] == []


def test_dates_are_display_only_and_engine_unchanged(service: HistoricalDownloadService) -> None:
    service.handle_wire("dl:quick:1M")
    snap = service.snapshot()
    assert snap["from_display"] and snap["to_display"]
    assert "display-only" in snap["log"][-1]


def test_no_connected_broker_does_not_start_the_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = HistoricalDownloadService(str(tmp_path), _Manager(connected=False))
    service.refresh_universe(["KAYNES"])
    service.handle_wire("dl:select-all")
    seen = []
    monkeypatch.setattr(
        engine_module,
        "HistoricalDownloadEngine",
        lambda: seen.append("constructed"),
    )
    outcome = service.start()
    assert outcome["ok"] is False
    assert "Connect broker in SYSTEM" in outcome["reason"]
    assert seen == []
    assert service.snapshot()["status"]["mode"] == "failed"


def test_start_runs_preserved_engine_with_ui_selection(
    monkeypatch: pytest.MonkeyPatch, service: HistoricalDownloadService
) -> None:
    progress: list[dict] = []
    service.set_progress_emitter(progress.append)
    service.handle_wire("dl:select:KEC")
    service.handle_wire("dl:interval:1")

    original = (engine_module.DATA_FOLDER, engine_module.LOCK_FILE, engine_module.SYMBOLS_CSV)
    built: list[str] = []

    release = threading.Event()

    class FakeEngine:
        def run(self) -> None:
            built.append("run")
            release.wait(timeout=2.0)

    monkeypatch.setattr(engine_module, "HistoricalDownloadEngine", FakeEngine)
    try:
        outcome = service.start()
        assert outcome["ok"] is True
        assert outcome["broker"] == "fyers"
        assert service.snapshot()["busy"] is True
        symbols_csv = Path(engine_module.SYMBOLS_CSV)
    finally:
        release.set()
        engine_module.DATA_FOLDER, engine_module.LOCK_FILE, engine_module.SYMBOLS_CSV = original

    with symbols_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"trading_symbol": "KEC", "interval": "5minute"}]

    # The engine thread is a daemon; wait briefly for its no-op run to finish.
    import time

    for _ in range(50):
        if progress and progress[-1].get("kind") == "done":
            break
        time.sleep(0.01)
    assert progress and progress[-1]["mode"] == "complete"
    assert built == ["run"]


def _seed_watchlist(data_dir: Path, symbols: list[str]) -> None:
    """Create real per-symbol SQLite files exactly like the MARKET store."""
    for symbol in symbols:
        connection = sqlite3.connect(data_dir / f"{symbol}.db")
        connection.execute(
            "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL, high REAL, "
            "low REAL, close REAL, volume INTEGER)"
        )
        connection.commit()
        connection.close()


def test_universe_equals_market_watchlist_without_a_22_stock_cap(tmp_path: Path) -> None:
    """Every eligible MARKET watchlist symbol is selectable in download."""
    from app.services.market_data_service import MarketDataService

    symbols = [f"STOCK{i:03d}" for i in range(30)]
    _seed_watchlist(tmp_path, symbols)

    service = HistoricalDownloadService(str(tmp_path), _Manager())
    service.refresh_universe_from_store()

    expected = MarketDataService(tmp_path).list_symbols()
    assert len(expected) == 30  # more than the old hardcoded 22
    assert service.snapshot()["universe"] == expected
    assert service.snapshot()["selected"] == []  # availability never auto-selects
    assert service.snapshot()["plan"]["stocks"] == "0"

    service.handle_wire("dl:select-all")
    assert len(service.snapshot()["selected"]) == 30
    service.handle_wire("dl:clear-all")
    assert service.snapshot()["selected"] == []


def test_universe_follows_watchlist_changes_and_prunes_stale_selection(tmp_path: Path) -> None:
    service = HistoricalDownloadService(str(tmp_path), _Manager())
    _seed_watchlist(tmp_path, ["AAA", "BBB", "CCC"])
    service.refresh_universe_from_store()
    service.handle_wire("dl:select:BBB")
    assert service.snapshot()["selected"] == ["BBB"]

    # A new watchlist symbol appears (new candle DB discovered).
    _seed_watchlist(tmp_path, ["DDD"])
    service.refresh_universe_from_store()
    assert service.snapshot()["universe"] == ["AAA", "BBB", "CCC", "DDD"]
    assert service.snapshot()["selected"] == ["BBB"]  # choice kept, still not auto-expanded

    # A watchlist symbol disappears; its selection must not survive.
    (tmp_path / "BBB.db").unlink()
    service.refresh_universe_from_store()
    assert service.snapshot()["universe"] == ["AAA", "CCC", "DDD"]
    assert service.snapshot()["selected"] == []


def test_headless_download_snapshot_syncs_from_market_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The snapshot producer refreshes the universe on every read."""
    import app.headless as headless

    _seed_watchlist(tmp_path, ["ONE", "TWO", "THREE"])
    service = HistoricalDownloadService(str(tmp_path), _Manager())
    monkeypatch.setattr(headless, "_ensure_download_service", lambda: service)
    snapshot = headless._download_snapshot()
    assert snapshot["universe"] == ["ONE", "THREE", "TWO"]
    assert snapshot["selected"] == []


@pytest.mark.parametrize("broker_id", ["fyers", "zerodha"])
def test_download_uses_exactly_the_system_broker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, broker_id: str
) -> None:
    """No selector is needed: the authoritative SYSTEM choice is used as-is."""
    manager = _Manager(broker_id=broker_id)
    service = HistoricalDownloadService(str(tmp_path), manager)
    service.refresh_universe(["AAA"])
    service.handle_wire("dl:select-all")

    class FakeEngine:
        def run(self) -> None:
            return None

    monkeypatch.setattr(engine_module, "HistoricalDownloadEngine", FakeEngine)
    original = (engine_module.DATA_FOLDER, engine_module.LOCK_FILE, engine_module.SYMBOLS_CSV)
    try:
        outcome = service.start()
        assert outcome["ok"] is True
        assert outcome["broker"] == broker_id
    finally:
        engine_module.DATA_FOLDER, engine_module.LOCK_FILE, engine_module.SYMBOLS_CSV = original
    assert service.snapshot()["plan"]["error"] == ""


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"connected": False}, "Connect broker in SYSTEM"),
        ({"auth_valid": False}, "not authenticated in SYSTEM"),
    ],
)
def test_disconnected_or_unauthenticated_broker_blocks_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kwargs: dict, message: str
) -> None:
    service = HistoricalDownloadService(str(tmp_path), _Manager(**kwargs))
    service.refresh_universe(["AAA"])
    service.handle_wire("dl:select-all")
    seen: list[str] = []
    monkeypatch.setattr(engine_module, "HistoricalDownloadEngine", lambda: seen.append("run"))
    outcome = service.start()
    assert outcome["ok"] is False
    assert message in outcome["reason"]
    assert seen == []
    assert service.snapshot()["plan"]["error"] == outcome["reason"]


def test_system_broker_change_refreshes_download_state(tmp_path: Path) -> None:
    manager = _Manager(broker_id="fyers")
    service = HistoricalDownloadService(str(tmp_path), manager)
    service.refresh_universe(["AAA"])
    service.handle_wire("dl:select-all")
    assert service.refresh_broker_state() == "fyers"
    assert service.snapshot()["plan"]["error"] == ""

    manager.broker_id = "zerodha"
    assert service.refresh_broker_state() == "zerodha"
    manager.connected = False
    assert service.refresh_broker_state() == ""
    assert "Connect broker in SYSTEM" in service.snapshot()["plan"]["error"]


def test_broker_switch_during_download_aborts_safely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _Manager(broker_id="fyers")
    service = HistoricalDownloadService(str(tmp_path), manager)
    service.refresh_universe(["AAA"])
    service.handle_wire("dl:select-all")
    progress: list[dict] = []
    service.set_progress_emitter(progress.append)

    class FakeEngine:
        _abort = False

        def run(self) -> None:
            while not self._abort:
                time.sleep(0.02)

    monkeypatch.setattr(engine_module, "HistoricalDownloadEngine", FakeEngine)
    original = (engine_module.DATA_FOLDER, engine_module.LOCK_FILE, engine_module.SYMBOLS_CSV)
    try:
        assert service.start()["ok"] is True
        engine = service._engine
        manager.broker_id = "zerodha"  # SYSTEM switched mid-run
        for _ in range(200):
            if engine._abort:
                break
            time.sleep(0.02)
        assert engine._abort is True
        for _ in range(200):
            if progress and progress[-1].get("kind") == "done":
                break
            time.sleep(0.02)
        assert progress[-1]["mode"] == "failed"
        assert "broker changed or disconnected" in progress[-1]["note"].lower()
        assert service.snapshot()["busy"] is False
    finally:
        service._monitor_stop.set()
        engine_module.DATA_FOLDER, engine_module.LOCK_FILE, engine_module.SYMBOLS_CSV = original


def test_selection_count_tracks_exactly_checked_stocks(tmp_path: Path) -> None:
    """Five checked symbols must yield a plan of 5; clearing must yield 0."""
    symbols = [f"STOCK{i}" for i in range(8)]
    service = HistoricalDownloadService(str(tmp_path), _Manager())
    service.refresh_universe(symbols)
    assert service.snapshot()["plan"]["stocks"] == "0"

    for symbol in symbols[:5]:
        service.handle_wire(f"dl:select:{symbol}")
    snap = service.snapshot()
    assert snap["selected"] == symbols[:5]
    assert snap["plan"]["stocks"] == "5"

    service.handle_wire("dl:clear-all")
    snap = service.snapshot()
    assert snap["selected"] == []
    assert snap["plan"]["stocks"] == "0"


def test_engine_derived_coverage_is_real_and_dates_are_display_only(tmp_path: Path) -> None:
    symbol = "TESTSYM"
    with sqlite3.connect(tmp_path / f"{symbol}.db") as conn:
        conn.execute("CREATE TABLE ohlcv (candle_time TEXT)")
        conn.executemany(
            "INSERT INTO ohlcv VALUES (?)",
            [("2019-01-02 09:15:00",), ("2025-03-04 15:00:00",)],
        )
    service = HistoricalDownloadService(str(tmp_path), _Manager())
    assert "Select stocks" in service.snapshot()["plan"]["range"]

    service.refresh_universe([symbol])
    service.handle_wire(f"dl:select:{symbol}")
    snap = service.snapshot()
    assert snap["plan"]["stocks"] == "1"
    rng = snap["plan"]["range"]
    assert "2019-01-02" in rng and "2025-03-04" in rng
    assert "missing head" in rng and "missing tail" in rng
    assert "display-only" in rng
