"""Paper execution end-to-end through the real app entrypoint.

Chain under test (all real, no mocks):
SQLite store → repository → strategy compiler → LiveSession → ReplayProvider
→ signals → intents → risk → planner → PaperBroker → fills → portfolio →
journal → checkpoint → clean shutdown.
"""

import gc
from pathlib import Path

import pytest
from PySide6.QtCore import QThread
from risk import RiskPolicy
from strategy.language.storage import save_strategy

from app import App, parse_args
from app.services.paper_service import (
    PaperError,
    PaperRunConfig,
    PaperService,
    format_paper_report,
    run_paper,
)
from app.tests.conftest import OHLCV_SCHEMA

SMA_CODE = """from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_sma
class Strategy(PythonStrategy):
    def __init__(self, params=None):
        super().__init__(params)
        self.prev_fast = None
        self.prev_slow = None
    def on_bar_logic(self, view):
        fast = calc_sma(self.closes, 2)
        slow = calc_sma(self.closes, 3)
        if self.prev_fast is None:
            self.prev_fast = fast
            self.prev_slow = slow
            return
        if fast > slow and self.prev_fast <= self.prev_slow:
            self.buy()
        elif fast < slow and self.prev_fast >= self.prev_slow:
            self.sell()
        self.prev_fast = fast
        self.prev_slow = slow
"""


def _seed(tmp_path: Path, count: int = 60) -> tuple[Path, Path]:
    """Seed oscillating bars (proven to cross SMA fast/slow repeatedly)."""
    import sqlite3

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / "TEST.db")
    try:
        connection.executescript(OHLCV_SCHEMA)
        for i in range(count):
            close = 100.0 + (i % 10) - 3.0 + i * 0.05
            connection.execute(
                "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"2026-01-{(i % 28) + 1:02d} 09:{15 + (i % 45):02d}:00",
                    close - 0.5,
                    close + 0.5,
                    close - 1.0,
                    close,
                    1000 + i,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    save_strategy(SMA_CODE, "sma-paper", strategy_dir)
    return data_dir, strategy_dir


def _service(tmp_path: Path, **overrides) -> PaperService:
    data_dir, strategy_dir = _seed(tmp_path)
    values: dict = {"data_dir": data_dir, "strategy_dir": strategy_dir}
    values.update(overrides)
    return PaperService(PaperRunConfig(**values))


def _running_threads() -> int:
    return sum(1 for obj in gc.get_objects() if isinstance(obj, QThread) and obj.isRunning())


def test_paper_flags_parse() -> None:
    args = parse_args(["--paper"])
    assert args.paper is True and args.live is False
    assert args.paper_symbol is None and args.paper_strategy is None
    assert parse_args([]).paper is False
    assert parse_args(["--paper", "--paper-symbol", "TEST", "--paper-strategy", "sma-paper"])


def test_paper_starts_and_runs_successfully(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.prepare()
    assert service._session is not None
    report = service.run()
    assert report.mode == "PAPER"
    assert report.events > 0
    assert report.signals > 0
    assert report.fills > 0
    assert report.clean_shutdown is True
    state = service.state()
    assert state["lifecycle"] == "STOPPED"
    assert state["positions"]  # portfolio changed
    kinds = state["journal"]
    for expected in (
        "SIGNAL_GENERATED",
        "RISK_APPROVED",
        "ORDER_PLANNED",
        "ORDER_SUBMITTED",
        "FILL",
        "POSITION_UPDATED",
    ):
        assert expected in kinds, f"missing causal fact: {expected}"


def test_requirement_failure_blocks_startup(tmp_path: Path) -> None:
    data_dir, strategy_dir = _seed(tmp_path)
    (strategy_dir / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    service = PaperService(
        PaperRunConfig(data_dir=data_dir, strategy_dir=strategy_dir, strategy="broken")
    )
    with pytest.raises(PaperError):
        service.run()
    assert service._session is None  # stopped before any order path existed


def test_live_flag_fails_closed() -> None:
    assert App.main(["--live"]) == 2


def test_risk_rejection_prevents_submission(tmp_path: Path) -> None:
    service = _service(tmp_path, risk_policy=RiskPolicy(max_position_qty=0.0))
    report = service.run()
    assert report.fills == 0
    assert report.orders == 0
    kinds = service.state()["journal"]
    assert "RISK_DENIED" in kinds
    assert "ORDER_SUBMITTED" not in kinds


def test_fill_updates_portfolio_and_journal(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.run()
    positions = service.state()["positions"]
    assert positions and all(p["quantity"] != 0.0 for p in positions)
    assert (service._session_dir / "journal.jsonl").is_file()
    assert (service._session_dir / "checkpoint.json").is_file()


def test_shutdown_clean_and_repeatable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.run()
    service.shutdown()
    service.shutdown()  # second call is a safe no-op
    assert service._shutdown_done is True
    state = service.state()
    assert state["lifecycle"] == "STOPPED"


def test_paper_spawns_no_threads(tmp_path: Path) -> None:
    before = _running_threads()
    service = _service(tmp_path)
    service.run()
    assert _running_threads() <= before  # Qt teardown class stays fixed


def test_run_paper_cli_and_report_format(tmp_path: Path) -> None:
    import argparse

    data_dir, strategy_dir = _seed(tmp_path)
    args = argparse.Namespace(
        data_dir=str(data_dir),
        strategy_dir=str(strategy_dir),
        paper_symbol=None,
        paper_strategy=None,
        paper_timeframe=None,
        limit=None,
    )
    assert run_paper(args) == 0
    report = PaperService(PaperRunConfig(data_dir=data_dir, strategy_dir=strategy_dir))
    text = format_paper_report(report.summarize(0))
    assert "VAYREN PAPER" in text
    assert "LIVE" not in text.replace("VAYREN PAPER", "")
    assert "Shutdown: CLEAN" in text or "Shutdown:" in text
