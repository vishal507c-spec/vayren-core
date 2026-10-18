"""Server-authoritative timeframes — write-time gate + store-fresh cache key.

Pins the 2026-10-18 drift class: a client-origin ``setup {"timeframe": "3m"}``
was persisted against 30m stores, turning every read into relabeled base rows
and START into a wall of per-symbol blockers. The repair:

* setup refuses a timeframe the configured symbols' ladders cannot serve,
  BEFORE anything is persisted — the refusal surfaces as an honest note;
* a servable change still lands and persists;
* an unknown label is refused outright (grammar authority, not the store);
* ``validate()``'s cache key rides the candle-store fingerprints, so a cached
  verdict retires itself when the rows change underneath — no explicit
  invalidation call required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.candle_sync_service import sync_candles  # noqa: E402
from app.services.live_trading_service import LiveTradingService  # noqa: E402
from strategy.universe_store import StrategyUniverseStore  # noqa: E402

STRATEGY = "OBR C1C4"
SYMBOL = "NSE:KAYNES"


class FakeAdapter:
    """A venue answering `history` with fixed real-shaped rows."""

    def __init__(self, rows: dict[str, list[tuple]]) -> None:
        self.rows = rows

    def history(self, symbol, resolution, start, end):  # noqa: ANN001, ANN201, ARG002
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
    store = StrategyUniverseStore(Path(tmp_path) / "live" / "strategy_universes.json")
    store.save(STRATEGY, (SYMBOL, "NSE:ZEEL"))
    return str(tmp_path), str(tmp_path)


def _service(dirs: tuple[str, str], manager: Any = None) -> LiveTradingService:
    service = headless._live_service(dirs[0], dirs[1], manager)
    assert service is not None
    return service


def _prime_store(dirs: tuple[str, str]) -> None:
    """A real 30m store for SYMBOL, so its ladder is genuinely detectable."""
    report = sync_candles(
        dirs[0], [SYMBOL], FakeManager(FakeAdapter({"KAYNES": _rows()})), timeframe="30m", days=30
    )
    assert report["written_total"] > 0
    assert "KAYNES" in _service(dirs)._repository.list_symbols()


def _configure(dirs: tuple[str, str]) -> LiveTradingService:
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
    assert service.config.symbols == (SYMBOL,)
    return service


def _persisted_timeframe(dirs: tuple[str, str]) -> str:
    store = Path(dirs[0]) / "live" / "live_session.json"
    return str(json.loads(store.read_text(encoding="utf-8"))["config"]["timeframe"])


def test_setup_refuses_unservable_timeframe_before_persisting(dirs: tuple[str, str]) -> None:
    _prime_store(dirs)
    service = _configure(dirs)
    assert service.config.timeframe == "30m"

    snap = headless._live_action(
        dirs[0], dirs[1], {"action": "setup", "strategy_name": STRATEGY, "timeframe": "3m"}
    )

    note = str(snap.get("action_note", ""))
    assert note.startswith("setup refused:"), note
    assert "3m" in note and "30m" in note
    assert service.config.timeframe == "30m", "a refused value must not stick in memory"
    assert _persisted_timeframe(dirs) == "30m", "a refused value must not be persisted"


def test_setup_refuses_unknown_timeframe(dirs: tuple[str, str]) -> None:
    _prime_store(dirs)
    service = _configure(dirs)

    snap = headless._live_action(
        dirs[0], dirs[1], {"action": "setup", "strategy_name": STRATEGY, "timeframe": "9x"}
    )

    assert str(snap.get("action_note", "")).startswith("setup refused:")
    assert service.config.timeframe == "30m"


def test_setup_accepts_a_servable_change_and_persists(dirs: tuple[str, str]) -> None:
    _prime_store(dirs)
    service = _configure(dirs)

    snap = headless._live_action(
        dirs[0], dirs[1], {"action": "setup", "strategy_name": STRATEGY, "timeframe": "1h"}
    )

    assert snap.get("action_note") == "setup updated — readiness re-evaluated"
    assert service.config.timeframe == "1h"
    assert _persisted_timeframe(dirs) == "1h"


def test_empty_timeframe_is_a_no_op_not_a_wipe(dirs: tuple[str, str]) -> None:
    _prime_store(dirs)
    service = _configure(dirs)

    # The Rust picker emits "" while the server ladder offers no selected value
    # (its selection stays empty rather than inventing one). Headless folds ""
    # to None; configure() must treat it as no-change intent — a wipe would
    # destroy the stored config that the session's gates were evaluated against.
    snap = headless._live_action(
        dirs[0], dirs[1], {"action": "setup", "strategy_name": STRATEGY, "timeframe": ""}
    )
    assert snap.get("action_note") == "setup updated — readiness re-evaluated"
    assert service.config.timeframe == "30m"
    assert _persisted_timeframe(dirs) == "30m"

    # Direct service callers (the remote command path shares this method) get
    # the same no-op guarantee.
    service.configure(timeframe="")
    assert service.config.timeframe == "30m", "empty is no change intent, not a wipe"
    assert _persisted_timeframe(dirs) == "30m"


def test_validate_cache_retires_when_store_fingerprint_changes(dirs: tuple[str, str]) -> None:
    service = _service(dirs)
    service._config.symbols = (SYMBOL,)
    signature = [(1, 10)]
    service._repository = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        store_fingerprint=lambda _symbol: signature[0],
    )
    calls: list[int] = []
    verdict: list[tuple[str, ...]] = [("stale verdict",)]

    def fake_validate() -> tuple[str, ...]:
        calls.append(1)
        return verdict[0]

    service.invalidate_validation_cache()
    with (
        patch.object(service, "_validate_uncached", fake_validate),
        patch.object(service, "_ensure_live_venues", lambda: None),
        patch.object(service, "broker_name", return_value=""),
        patch.object(service, "_resolve_live_venue_id", lambda _name: None),
    ):
        assert service.validate() == ("stale verdict",)
        assert service.validate() == ("stale verdict",)
        assert len(calls) == 1, "the verdict must genuinely cache while nothing changes"

        signature[0] = (2, 4096)  # a sync landed: the store file changed
        verdict[0] = ("fresh verdict",)
        assert service.validate() == ("fresh verdict",)
        assert len(calls) == 2, "a store change must retire the cached verdict"
