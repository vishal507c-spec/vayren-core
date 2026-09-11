"""LiveTradingService proofs — validation, safety defaults, persistence.

Covers the UI-service contract (Qt offscreen, temp store):
- empty config blocks START with exact reasons
- PAPER is the default; LIVE refuses without explicit confirmation
- LIVE with confirmation but no venue fails closed with venue reasons
- PAPER starts end-to-end on real SQLite seed and ticks without errors
- STOP halts new submissions; config restores on reopen without autostart
- persisted files carry no secrets
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import datetime
import json
import sqlite3
from pathlib import Path

from PySide6.QtWidgets import QApplication
from strategy.language.storage import create_strategy

from app.services.live_trading_service import LiveTradingService

CODE = """from strategy.strategies.base import PythonStrategy


class Strategy(PythonStrategy):
    SUPPORTS_LIVE = True

    @staticmethod
    def param_specs():
        return ()

    def warmup(self):
        return 0

    def on_bar_logic(self, view):
        if view.index < 2:
            return
        bar = view.bar
        if bar.close > bar.open and view.state.flat:
            self.buy()
        elif bar.close < bar.open and not view.state.flat:
            self.close_position(view)
"""


def _seed(tmp: Path) -> None:
    closes = []
    price = 100.0
    for i in range(30):
        price += 2.0 if i % 2 == 0 else -1.0
        closes.append(round(price, 2))
    conn = sqlite3.connect(tmp / "AAA.db")
    conn.execute(
        "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, "
        "high REAL, low REAL, close REAL, volume INTEGER);"
    )
    times = ("09:15:00", "09:30:00", "09:45:00", "10:00:00", "10:15:00", "10:30:00")
    day = datetime.date(2026, 1, 5)
    i = 0
    while i < len(closes):
        if day.weekday() < 5:
            for t in times:
                if i >= len(closes):
                    break
                c = closes[i]
                prev = closes[i - 1] if i else c
                o = prev - 0.5 if i % 2 == 0 else prev + 0.5
                conn.execute(
                    "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
                    (f"{day.isoformat()} {t}", o, c + 1.0, c - 1.0, c, 1500),
                )
                i += 1
        day += datetime.timedelta(days=1)
    conn.commit()
    conn.close()
    create_strategy("SvcObr", CODE, data_dir=tmp)


def _service(qt_app: QApplication, tmp_path: Path) -> LiveTradingService:
    assert qt_app is not None
    _seed(tmp_path)
    return LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path)


def test_empty_config_blocks_start_with_reasons(qt_app: QApplication, tmp_path: Path) -> None:
    svc = _service(qt_app, tmp_path)
    assert svc.config.mode == "PAPER"
    blockers = svc.validate()
    assert any("strategy" in b for b in blockers)
    assert any("symbol" in b for b in blockers)
    assert any("timeframe" in b for b in blockers)
    ok, reasons = svc.start()
    assert not ok and reasons == blockers
    assert svc.status == "STOPPED"


def test_paper_runs_end_to_end(qt_app: QApplication, tmp_path: Path) -> None:
    svc = _service(qt_app, tmp_path)
    svc.configure(strategy_name="SvcObr", symbols=("AAA",), timeframe="15m", quantity=10.0)
    assert svc.validate() == ()
    ok, reasons = svc.start()
    assert ok, reasons
    assert svc.status == "RUNNING"
    for _ in range(3):
        svc.tick()
    snap = svc.snapshot()
    assert snap["mode"] == "PAPER"
    assert snap["session_status"] == "RUNNING"
    assert snap["strategy"] and snap["strategy"]["id"] == "SvcObr"
    assert snap["market_bars"], "chart backdrop must come from real rows"
    svc.stop()
    assert svc.status == "STOPPED"
    assert svc.snapshot()["session_status"] == "STOPPED"


def test_stop_freezes_submissions(qt_app: QApplication, tmp_path: Path) -> None:
    svc = _service(qt_app, tmp_path)
    svc.configure(strategy_name="SvcObr", symbols=("AAA",), timeframe="15m", quantity=10.0)
    ok, _ = svc.start()
    assert ok
    svc.tick()
    before = sum(len(s.journal.of_kind("ORDER_SUBMITTED")) for s in svc._sessions.values())
    svc.stop("test halt")
    for _ in range(5):
        svc.tick()
    after = sum(len(s.journal.of_kind("ORDER_SUBMITTED")) for s in svc._sessions.values())
    assert after == before


def test_live_refuses_without_confirmation(qt_app: QApplication, tmp_path: Path) -> None:
    svc = _service(qt_app, tmp_path)
    svc.configure(
        strategy_name="SvcObr", symbols=("AAA",), timeframe="15m", mode="LIVE", quantity=10.0
    )
    ok, reasons = svc.start(confirmed=False)
    assert not ok
    assert any("confirmation" in r for r in reasons)
    assert svc.status != "RUNNING"


def test_live_confirmed_still_fail_closed_without_venue(
    qt_app: QApplication, tmp_path: Path
) -> None:
    svc = _service(qt_app, tmp_path)
    svc.configure(
        strategy_name="SvcObr", symbols=("AAA",), timeframe="15m", mode="LIVE", quantity=10.0
    )
    ok, reasons = svc.start(confirmed=True)
    assert not ok, "no live venue exists — must never start"
    assert any("broker" in r.lower() for r in reasons)
    assert svc.status != "RUNNING"
    assert not svc._sessions


def test_config_restores_without_autostart(qt_app: QApplication, tmp_path: Path) -> None:
    svc = _service(qt_app, tmp_path)
    svc.configure(strategy_name="SvcObr", symbols=("AAA",), timeframe="15m", quantity=7.0)
    assert (tmp_path / "live" / "live_session.json").is_file()
    reopened = LiveTradingService(data_dir=tmp_path, strategy_dir=tmp_path)
    assert reopened.config.strategy_name == "SvcObr"
    assert reopened.config.symbols == ("AAA",)
    assert reopened.config.timeframe == "15m"
    assert reopened.config.quantity == 7.0
    assert reopened.status == "STOPPED"
    assert not reopened._sessions


def test_live_never_silently_falls_back_to_paper(
    qt_app: QApplication, tmp_path: Path, monkeypatch
) -> None:
    """A venue downgrade mid-start must fail loudly, never trade as PAPER."""
    from execution import ExecutionMode

    svc = _service(qt_app, tmp_path)
    svc.configure(
        strategy_name="SvcObr", symbols=("AAA",), timeframe="15m", mode="LIVE", quantity=10.0
    )
    monkeypatch.setattr(svc, "validate", lambda: ())
    monkeypatch.setattr(
        svc,
        "_resolve_live_venue_id",
        lambda _name: "sandbox",  # type: ignore[method-assign]
    )
    monkeypatch.setattr(
        svc,
        "_probe_live_venue",
        lambda _venue_id: {},  # type: ignore[method-assign]
    )
    ok, reasons = svc.start(confirmed=True)
    assert not ok
    assert any("PAPER" in r for r in reasons)
    assert svc.status != "RUNNING"
    assert not svc._sessions
    assert ExecutionMode.PAPER is not None  # contract import guard


def test_persisted_files_carry_no_secrets(qt_app: QApplication, tmp_path: Path) -> None:
    svc = _service(qt_app, tmp_path)
    svc.configure(strategy_name="SvcObr", symbols=("AAA",), timeframe="15m", quantity=10.0)
    ok, _ = svc.start()
    assert ok
    svc.tick()
    svc.stop()
    live_dir = tmp_path / "live"
    assert live_dir.is_dir()
    for path in live_dir.rglob("*.json*"):
        text = path.read_text(encoding="utf-8").lower()
        for needle in ("api_key", "api_secret", "password", "totp", "token.json"):
            assert needle not in text, (path, needle)
    stored = json.loads((live_dir / "live_session.json").read_text(encoding="utf-8"))
    assert stored["config"]["mode"] == "PAPER"
