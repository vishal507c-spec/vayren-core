"""PHASE 1 strategy-first flow — strategy-scoped universes, no global default.

The LIVE/PAPER screen must never pre-select a strategy and never fall back
to a global symbol list. Symbols are owned per strategy, persisted per
strategy, and isolated across strategies — the registry declaration and the
market-store discovery are not universe authorities.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from app.services.live_trading_service import (  # noqa: E402
    LiveConfigError,
    LiveTradingService,
)


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    return str(tmp_path), str(strategy_dir)


def _service(dirs: tuple[str, str]) -> LiveTradingService:
    return LiveTradingService(dirs[0], dirs[1])


def test_1_no_strategy_blocks_universe_configuration(
    dirs: tuple[str, str],
) -> None:
    service = _service(dirs)
    assert service.config.strategy_name == ""
    assert service.available_symbols() == ()
    with pytest.raises(LiveConfigError):
        service.configure(symbols=("NSE:RELIANCE",))
    assert "no strategy selected" in service.validate()
    snap = service.snapshot()
    assert snap["strategy"]["id"] == ""


def test_2_select_loads_only_that_strategy_symbols(
    dirs: tuple[str, str],
) -> None:
    service = _service(dirs)
    service.configure(strategy_name="Strategy A")
    service.configure(symbols=("NSE:RELIANCE", "NSE:TCS"))
    service.configure(strategy_name="Strategy B")
    # B starts empty — nothing inherited from A.
    assert service.config.symbols == ()
    assert service.available_symbols() == ()
    assert service.available_symbols("Strategy A") == ("NSE:RELIANCE", "NSE:TCS")


def test_3_save_persists_per_strategy(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(strategy_name="Strategy A")
    service.configure(symbols=("NSE:INFY",))
    assert service._universes.symbols_for("Strategy A") == ("NSE:INFY",)
    assert service._universes.path.exists()


def test_4_restart_restores_saved_symbols(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(strategy_name="Strategy A")
    service.configure(symbols=("NSE:RELIANCE",))
    service.configure(strategy_name="Strategy B")
    service.configure(symbols=("NSE:SBIN",))
    reopened = _service(dirs)
    reopened.configure(strategy_name="Strategy A")
    assert reopened.config.symbols == ("NSE:RELIANCE",)
    reopened.configure(strategy_name="Strategy B")
    assert reopened.config.symbols == ("NSE:SBIN",)


def test_5_strategy_a_symbols_never_appear_in_b(
    dirs: tuple[str, str],
) -> None:
    service = _service(dirs)
    service.configure(strategy_name="Strategy A")
    service.configure(symbols=("NSE:RELIANCE", "NSE:TCS", "NSE:INFY"))
    service.configure(strategy_name="Strategy B")
    service.configure(symbols=("NSE:SBIN", "NSE:HDFCBANK"))
    assert "NSE:RELIANCE" not in service.available_symbols("Strategy B")
    assert "NSE:SBIN" not in service.available_symbols("Strategy A")
    # A combined setup carrying stale rows on selection change is ignored.
    service.configure(strategy_name="Strategy A", symbols=("NSE:SBIN",))
    assert service.config.symbols == ("NSE:RELIANCE", "NSE:TCS", "NSE:INFY")


def test_6_switching_restores_correct_sets(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(strategy_name="Strategy A")
    service.configure(symbols=("NSE:RELIANCE",))
    service.configure(strategy_name="Strategy B")
    service.configure(symbols=("NSE:SBIN",))
    service.configure(strategy_name="Strategy A")
    assert service.config.symbols == ("NSE:RELIANCE",)
    service.configure(strategy_name="Strategy B")
    assert service.config.symbols == ("NSE:SBIN",)
    service.configure(strategy_name="Strategy A")
    assert service.config.symbols == ("NSE:RELIANCE",)


def test_7_empty_universe_shows_empty_state(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(strategy_name="Strategy A")
    assert service.config.symbols == ()
    assert service.available_symbols() == ()
    snap = service.snapshot()
    assert snap["available_symbols"] == ()
    assert snap["selected_symbols"] == ()
    assert "no symbols selected" in " ".join(snap["start_blockers"])


def test_8_no_global_or_default_fallback(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    # The registry declares OBR C1C4 with symbols and the market store may
    # know symbols — neither may become the active universe unasked.
    service._repository = _StubRepository(["RELIANCE"])  # pyright: ignore[reportAttributeAccessIssue]
    fresh = _service(dirs)
    fresh._repository = _StubRepository(["RELIANCE"])  # pyright: ignore[reportAttributeAccessIssue]
    assert fresh.config.strategy_name == ""
    assert fresh.config.symbols == ()
    assert fresh.available_symbols() == ()
    assert fresh.available_symbols("OBR C1C4") == ()


def test_9_only_nse_symbols_accepted(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service.configure(strategy_name="Strategy A")
    with pytest.raises(LiveConfigError):
        service.configure(symbols=("NSE:RELIANCE", "BSE:SBIN"))
    with pytest.raises(LiveConfigError):
        service.configure(symbols=("INFY",))
    # Refused writes change nothing.
    assert service.config.symbols == ()
    assert service.available_symbols("Strategy A") == ()


class _StubRepository:
    """Market-store discovery stub: proves discovery is not a fallback."""

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def list_symbols(self) -> list[str]:
        return list(self._symbols)
