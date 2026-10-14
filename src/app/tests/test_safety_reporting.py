"""Safety-reporting regressions: stale quotes and hardcoded arming state.

Two defects this file pins:

1. A candle close from the local `<SYMBOL>.db` store was stamped with the
   CURRENT time, so a previous-session close rendered as a live tick. The
   store's own ``candle_time`` is the only honest label.
2. ``broker_session.live_armed`` / ``strategy_running`` were hardcoded
   literals, so an armed, running LIVE session rendered as disarmed with
   zero uptime.

No test here starts a session, arms consent, or touches a broker.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services.market_data_service import MarketDataService
from remote.snapshot import build_snapshot


def _write_store(tmp_path: Path, symbol: str, stamps: list[str], closes: list[float]) -> Path:
    db = tmp_path / f"{symbol}.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, "
        "high REAL, low REAL, close REAL, volume INTEGER)"
    )
    con.executemany(
        "INSERT INTO ohlcv VALUES (?,?,?,?,?,?)",
        [(s, c, c, c, c, 1) for s, c in zip(stamps, closes, strict=True)],
    )
    con.commit()
    con.close()
    return db


# ── Quote carries its source stamp ──────────────────────────────────────


def test_quote_reports_the_store_candle_stamp_not_now(tmp_path: Path) -> None:
    """The price's own candle_time must survive into the Quote."""
    stamp = "2026-10-13 15:15:00"
    _write_store(tmp_path, "KAYNES", [stamp, "2026-10-13 14:45:00"], [3290.0, 3289.0])

    quote = MarketDataService(tmp_path).get_quotes(["KAYNES"])[0]

    assert quote.price == 3290.0
    assert quote.as_of == stamp
    # The bug stamped the CURRENT clock; the stamp must be the old candle.
    assert quote.as_of != datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def test_quote_without_a_store_reports_no_price_and_no_stamp(tmp_path: Path) -> None:
    quote = MarketDataService(tmp_path).get_quotes(["NOSUCH"])[0]
    assert quote.price is None
    assert quote.as_of == ""


def test_quote_change_pct_is_between_the_two_store_closes(tmp_path: Path) -> None:
    _write_store(tmp_path, "AAA", ["2026-10-13 15:15:00", "2026-10-13 14:45:00"], [110.0, 100.0])
    quote = MarketDataService(tmp_path).get_quotes(["AAA"])[0]
    assert quote.change_pct == pytest.approx(10.0)


# ── A stale candle must never look like a live tick ─────────────────────


def _live_snapshot(live: dict[str, Any]) -> dict[str, Any]:
    """`build_snapshot` returns the section map directly (no wrapper key)."""
    return build_snapshot(live)


def test_market_data_never_claims_a_fresh_tick_stamp(tmp_path: Path) -> None:
    """`last_tick_time` must not be the wall clock and age must not be a constant."""
    from app.services.live_trading_service import LiveTradingService

    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir()
    strat_dir.mkdir()
    service = LiveTradingService(data_dir=data_dir, strategy_dir=strat_dir)

    md = service.snapshot()["market_data"]

    # No tick timestamp is observable in this layer, so it stays empty.
    assert md["last_tick_time"] == ""
    # The old code reported a hardcoded 0.4 s freshness that never happened.
    assert md["freshness_age_s"] is None
    assert md["price_source"] == "candle_store"


def _market_data_gate(tmp_path: Path, reason_rows: list[dict[str, Any]], feed: str):
    """Build the real gate row from a real service (no live session, no broker)."""
    from app.services.live_trading_service import LiveTradingService

    data_dir = tmp_path / "gate-data"
    strat_dir = tmp_path / "gate-strategies"
    data_dir.mkdir()
    strat_dir.mkdir()
    service = LiveTradingService(data_dir=data_dir, strategy_dir=strat_dir)
    return next(
        g
        for g in service._readiness_gates({"feed": feed}, {}, reason_rows)
        if g["name"] == "Market Data Streaming"
    )


def test_market_data_gate_names_the_candle_store_not_live_delivery(
    tmp_path: Path,
) -> None:
    """'N available' must be attributed to the store, with the feed separate."""
    rows = [{"status": "AVAILABLE"}, {"status": "NOT FOUND"}]
    gate = _market_data_gate(tmp_path, rows, "live")

    assert gate["status"] == "NOT READY"
    # The store, not the venue, is what the counts describe.
    assert "candle store 1/2" in gate["reason"]
    assert "1 not found" in gate["reason"]
    # The live feed is reported on its own terms and never as tick delivery.
    assert "venue feed connected" in gate["reason"]
    assert "no tick stamp observed" in gate["reason"]


def test_market_data_gate_says_no_live_feed_when_there_is_none(tmp_path: Path) -> None:
    gate = _market_data_gate(tmp_path, [{"status": "AVAILABLE"}], "none")
    assert gate["status"] == "READY"
    assert "candle store 1/1" in gate["reason"]
    assert "no live feed" in gate["reason"]


# ── Worker timing comes from the nested session_timing ───────────────────


def test_worker_reads_started_at_and_uptime_from_session_timing() -> None:
    """Regression: read from the top level, these were always blank/zero."""
    live = {
        "session_status": "RUNNING",
        "session_timing": {
            "status": "RUNNING",
            "started_at": "2026-10-14T09:23:48+05:30",
            "stopped_at": "",
            "elapsed_seconds": 742,
        },
    }
    worker = _live_snapshot(live)["worker"]

    assert worker["status"] == "RUNNING"
    assert worker["running"] is True
    assert worker["started_at"] == "2026-10-14T09:23:48+05:30"
    assert worker["uptime_seconds"] == 742


def test_worker_timing_is_zero_when_session_timing_is_absent() -> None:
    worker = _live_snapshot({"session_status": "STOPPED"})["worker"]
    assert worker["running"] is False
    assert worker["started_at"] == ""
    assert worker["uptime_seconds"] == 0


def test_worker_uptime_survives_a_shape_that_looks_like_the_old_bug() -> None:
    """Top-level duplicates must not win over the nested authority."""
    live = {
        "session_status": "RUNNING",
        "started_at": "",  # the unreachable top-level key
        "elapsed_seconds": 0,
        "session_timing": {"started_at": "2026-10-14T09:23:48+05:30", "elapsed_seconds": 742},
    }
    worker = _live_snapshot(live)["worker"]
    assert worker["started_at"] == "2026-10-14T09:23:48+05:30"
    assert worker["uptime_seconds"] == 742


# ── Consent and arming are read, never invented ─────────────────────────


def test_runtime_state_reads_consent_and_session_status() -> None:
    from app.services.live_trading_service import LiveTradingService

    service = LiveTradingService.__new__(LiveTradingService)
    service._confirmed_live_at = "2026-10-14T09:23:00+05:30"
    service._status = "RUNNING"

    facts = LiveTradingService._runtime_state(service)

    assert facts["live_armed"] is True
    assert facts["strategy_running"] is True
    assert facts["consent_confirmed_at"] == "2026-10-14T09:23:00+05:30"


def test_runtime_state_without_consent_never_fabricates_arming() -> None:
    from app.services.live_trading_service import LiveTradingService

    service = LiveTradingService.__new__(LiveTradingService)
    service._confirmed_live_at = ""
    service._status = "RUNNING"

    facts = LiveTradingService._runtime_state(service)

    # Running is real; arming without consent must stay False.
    assert facts["strategy_running"] is True
    assert facts["live_armed"] is False
    assert facts["consent_confirmed_at"] == ""


def test_runtime_state_reads_a_registered_provider() -> None:
    """Regression: these two fields were hardcoded False regardless of reality."""
    from app.services.broker_manager import BrokerManager

    manager = BrokerManager.__new__(BrokerManager)
    provider = {"live_armed": True, "strategy_running": True, "session_status": "RUNNING"}
    manager._runtime_provider = lambda: provider

    facts = manager._runtime_state()

    assert facts["live_armed"] is True
    assert facts["strategy_running"] is True
    assert facts["session_status"] == "RUNNING"


def test_runtime_state_defaults_to_false_without_a_provider() -> None:
    from app.services.broker_manager import BrokerManager

    manager = BrokerManager.__new__(BrokerManager)
    manager._runtime_provider = None
    assert manager._runtime_state() == {}


def test_runtime_state_swallows_provider_failures() -> None:
    """A reporting provider must never break a status read."""
    from app.services.broker_manager import BrokerManager

    def boom() -> dict[str, Any]:
        raise RuntimeError("runtime service gone")

    manager = BrokerManager.__new__(BrokerManager)
    manager._runtime_provider = boom
    assert manager._runtime_state() == {}


def test_stale_candle_age_is_reported_not_hidden(tmp_path: Path) -> None:
    """A day-old close must surface its own stamp, not a fresh-looking row."""
    now = datetime.now()
    old = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    older = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    _write_store(tmp_path, "OLD", [older, old], [100.0, 101.0])

    quote = MarketDataService(tmp_path).get_quotes(["OLD"])[0]

    assert quote.price == 101.0
    # The stamp is a day back, so any consumer reading `as_of` sees that.
    assert quote.as_of == old
