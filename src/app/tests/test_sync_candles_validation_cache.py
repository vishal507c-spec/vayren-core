"""Candle sync must retire the stale START blocker it actually satisfied.

``LiveTradingService.validate()`` caches its verdict on the config tuple
(strategy, symbols, timeframe, mode, quantity, venue). That key carries no
candle-store state, so a "no market data for <symbol>" blocker outlived the
very sync that satisfied it and kept showing on a stopped, disarmed session
whose store was in fact populated.

These tests pin both halves of the repair:

* a cached missing-data verdict is NOT reused once the venue's bars land;
* a sync that stored nothing (venue error, or no rows) leaves every blocker
  exactly as it found them, so a sync can never imply data it did not fetch;
* the repair retires only the stale verdict - every other validation and
  trading-safety rule still stands after the invalidation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.candle_sync_service import sync_candles  # noqa: E402
from strategy.universe_store import StrategyUniverseStore  # noqa: E402

STRATEGY = "OBR C1C4"
SYMBOL = "NSE:ZEEL"
MISSING = f"no market data for {SYMBOL} 30m"


class FakeAdapter:
    """A venue answering `history` with fixed real-shaped rows."""

    def __init__(self, rows: dict[str, list[tuple]], fail: set[str] | None = None) -> None:
        self.rows = rows
        self.fail = fail or set()

    def history(self, symbol, resolution, start, end):  # noqa: ANN001, ANN201, ARG002
        if symbol in self.fail:
            raise RuntimeError("venue said no")
        return list(self.rows.get(symbol, []))


class FakeManager:
    def __init__(self, adapter: Any | None, active: str = "fyers") -> None:
        self.adapter = adapter
        self.active = active

    def snapshot(self) -> dict[str, Any]:
        return {"active_broker": self.active}

    def state(self, broker_id: str) -> dict[str, Any]:  # noqa: ARG002
        return {}

    def read_only_adapter(self, broker_id: str) -> Any | None:  # noqa: ARG002
        return self.adapter


def _rows(close: float = 100.0, count: int = 3) -> list[tuple]:
    base = 1_790_000_000
    return [
        (base + 1800 * i, close - 1.0, close + 2.0, close - 2.0, close + i, 10 * (i + 1))
        for i in range(count)
    ]


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    headless._ARMED[0] = False
    # Mirror the deployed shape: the strategy ALREADY owns a saved universe, so
    # the setup under test NARROWS to a subset of it (the "operator selected
    # specific symbols" branch) instead of reloading the whole thing.
    store = StrategyUniverseStore(Path(tmp_path) / "live" / "strategy_universes.json")
    store.save(STRATEGY, (SYMBOL, "NSE:KAYNES"))
    return str(tmp_path), str(tmp_path)


def _service(dirs: tuple[str, str], manager: Any = None) -> Any:
    service = headless._live_service(dirs[0], dirs[1], manager)
    assert service is not None
    return service


def _prime_store(dirs: tuple[str, str], adapter: FakeAdapter) -> None:
    """Give the store a real instrument so `has_local_store` is genuinely true.

    The missing-data blocker is only reachable once SOME symbol has bars - the
    rule is "this store exists but cannot serve this symbol", not "store is
    empty". KAYNES stands in for the already-synced neighbour.
    """
    report = sync_candles(dirs[0], ["NSE:KAYNES"], FakeManager(adapter), timeframe="30m", days=30)
    assert report["written_total"] > 0
    assert "KAYNES" in _service(dirs)._repository.list_symbols()


def _configure_zeel(dirs: tuple[str, str]) -> Any:
    # Step 1 selects the strategy (which loads its saved universe - the
    # `strategy_changed` branch). Step 2 then narrows to a subset of that
    # already-selected strategy, exactly as the deployed isolate did.
    headless._live_action(
        dirs[0], dirs[1], {"action": "setup", "strategy_name": STRATEGY, "timeframe": "30m"}
    )
    headless._live_action(
        dirs[0],
        dirs[1],
        {
            "action": "setup",
            "strategy_name": STRATEGY,
            "symbols": [SYMBOL],
            "timeframe": "30m",
            "quantity": 1.0,
        },
    )
    service = _service(dirs)
    assert service.config.strategy_name == STRATEGY
    assert service.config.symbols == (SYMBOL,)
    return service


def _sync(dirs: tuple[str, str], manager: FakeManager) -> dict[str, Any]:
    snap = headless._live_action(
        dirs[0], dirs[1], {"action": "sync_candles", "timeframe": "30m", "days": 30}, manager
    )
    assert "candle_sync" in snap
    return dict(snap["candle_sync"])


def test_successful_sync_retires_the_cached_missing_data_verdict(
    dirs: tuple[str, str],
) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()})
    _prime_store(dirs, adapter)
    service = _configure_zeel(dirs)

    blockers = service.validate()
    assert any(MISSING in b for b in blockers), f"precondition failed: {blockers}"

    # Prove the verdict is genuinely CACHED, so the repair below is doing the
    # work rather than the store merely happening to be re-read each time.
    assert service.validate() == blockers
    assert service._validate_cache is not None

    report = _sync(
        dirs, FakeManager(FakeAdapter({"KAYNES": _rows(), SYMBOL.split(":")[1]: _rows(73.85)}))
    )
    assert report["written_total"] > 0

    after = service.validate()
    assert not any(MISSING in b for b in after), f"stale verdict survived the sync: {after}"
    assert service._repository.get_candles_timeframe("ZEEL", "30m", 1), "store has no ZEEL 30m bars"


def test_failed_sync_does_not_imply_data_became_available(dirs: tuple[str, str]) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()})
    _prime_store(dirs, adapter)
    service = _configure_zeel(dirs)

    assert any(MISSING in b for b in service.validate())

    report = _sync(dirs, FakeManager(FakeAdapter({}, fail={"ZEEL"})))
    assert report["written_total"] == 0
    assert report["results"][0]["status"] == "FAILED"

    after = service.validate()
    assert any(MISSING in b for b in after), (
        f"a failed sync cleared a blocker it never earned: {after}"
    )
    assert not service._repository.get_candles_timeframe("ZEEL", "30m", 1)


def test_empty_venue_response_does_not_imply_data_became_available(
    dirs: tuple[str, str],
) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()})
    _prime_store(dirs, adapter)
    service = _configure_zeel(dirs)

    assert any(MISSING in b for b in service.validate())

    report = _sync(dirs, FakeManager(FakeAdapter({"KAYNES": _rows()})))
    assert report["written_total"] == 0
    assert report["results"][0]["status"] == "NO_DATA"

    after = service.validate()
    assert any(MISSING in b for b in after), (
        f"an empty sync cleared a blocker it never earned: {after}"
    )


def test_sync_keeps_every_unrelated_blocker_intact(dirs: tuple[str, str]) -> None:
    """The invalidation must not soften any other validation rule.

    A second, unrelated blocker is introduced on purpose (quantity 0) so the
    test can show the sync retires the stale store verdict ONLY - the rest of
    the gate list has to survive it untouched.
    """
    adapter = FakeAdapter({"KAYNES": _rows()})
    _prime_store(dirs, adapter)
    _configure_zeel(dirs)
    service = _service(dirs)
    # configure() rejects a non-positive quantity outright, so the unrelated
    # blocker is staged directly on the config. LiveTradeConfig is a plain
    # (mutable) dataclass owned by the LIVE tab.
    service._config.quantity = 0.0

    before = set(service.validate())
    assert any(MISSING in b for b in before)
    unrelated_before = {b for b in before if MISSING not in b}
    assert unrelated_before, f"precondition failed, expected an unrelated blocker in {before}"
    assert "quantity must be positive" in unrelated_before

    _sync(dirs, FakeManager(FakeAdapter({"KAYNES": _rows(), "ZEEL": _rows(73.85)})))

    after = set(service.validate())
    assert not any(MISSING in b for b in after)
    unrelated_after = {b for b in after if MISSING not in b}
    assert unrelated_after == unrelated_before, (
        f"unrelated blockers changed: dropped={sorted(unrelated_before - unrelated_after)} "
        f"added={sorted(unrelated_after - unrelated_before)}"
    )
    # Invalidating the cache re-runs the SAME checks; it never skips them.
    assert "quantity must be positive" in service.validate()


def test_sync_is_still_refused_while_the_strategy_runs(dirs: tuple[str, str]) -> None:
    """Trading-safety rule: a running strategy must never have its bars rewritten."""
    adapter = FakeAdapter({"KAYNES": _rows()})
    _prime_store(dirs, adapter)
    _configure_zeel(dirs)
    service = _service(dirs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(service), "snapshot", lambda _self: {"session_status": "RUNNING"})
        snap = headless._live_action(
            dirs[0], dirs[1], {"action": "sync_candles"}, FakeManager(adapter)
        )
    assert "candle sync refused" in str(snap.get("action_note", ""))
    assert "candle_sync" not in snap
