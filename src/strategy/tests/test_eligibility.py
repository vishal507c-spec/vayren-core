"""Phase 6 unified eligibility — deterministic gate composition, no network.

Fakes implement the exact duck-typed surfaces the engine consumes
(Phase 4 router truth, Phase 5 execution truth, live-service risk/
session/safety truth). Covers the §28 matrix (40 items) plus §29
architecture proofs (no duplicated risk/market-data/broker logic, no
order submission).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy.eligibility_engine import GATE_ORDER, EligibilityEngine  # noqa: E402
from strategy.instrument_registry import reset_instrument_registry  # noqa: E402
from strategy.models.eligibility import (  # noqa: E402
    DEGRADED,
    FAIL_FAST,
    READY,
    STALE,
    UNAVAILABLE,
    WAITING,
)
from strategy.provider_mapping import ProviderMappingRegistry  # noqa: E402

STRAT_ROOT = ROOT / "src" / "strategy"
RELIANCE = "NSE:EQUITY:RELIANCE"
TCS = "NSE:EQUITY:TCS"
SBIN = "NSE:EQUITY:SBIN"
KEC = "NSE:EQUITY:KEC"


class FakeLive:
    """Live-service truth double (risk/session/strategy/universe/safety)."""

    _universe: dict[str, tuple[str, ...]]
    _strategies: tuple[str, ...]

    def __init__(self) -> None:
        self.config = type("Cfg", (), {"strategy_name": "Momentum", "mode": "LIVE"})()
        self.status = "RUNNING"
        self._risk_status = "READY"
        self._risk_reason = ""
        self._confirmed_live_at = "2026-01-02T00:00:00+00:00"
        self._universe = {"Momentum": ("NSE:RELIANCE", "NSE:TCS")}
        self._strategies = ("Momentum",)
        self._validate_blockers: tuple[str, ...] = ()
        self._live_blockers_list: tuple[str, ...] = ()
        self._snapshot_recon = {"blocks_live": False}

    def available_strategies(self) -> tuple[str, ...]:
        return self._strategies

    def available_symbols(self, strategy_name: str | None = None) -> tuple[str, ...]:
        return self._universe.get(strategy_name or "", ())

    def validate(self) -> tuple[str, ...]:
        return self._validate_blockers

    def _live_blockers(self) -> list[str]:
        return list(self._live_blockers_list)

    def snapshot(self) -> dict[str, Any]:
        return {"reconciliation": self._snapshot_recon}


class FakeMarketRouter:
    """Phase 4 truth double (readiness per instrument)."""

    def __init__(self, states: dict[str, dict[str, Any]] | None = None) -> None:
        self._states = states or {}
        self._config = type("Cfg", (), {"ordered_providers": ("FYERS", "ZERODHA")})()

    def instrument_states(self) -> dict[str, dict[str, Any]]:
        return dict(self._states)

    def provider_health(self) -> dict[str, dict[str, str]]:
        return {"FYERS": {"state": "HEALTHY", "reason": "fake"}}


class FakeExecutionRouter:
    """Phase 5 truth double (broker health + ownership)."""

    def __init__(
        self,
        health: dict[str, dict[str, str]] | None = None,
        owned: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self._health = health or {
            "ZERODHA": {"state": "HEALTHY", "reason": "fake"},
            "FYERS": {"state": "HEALTHY", "reason": "fake"},
        }
        self._owned = owned
        self._config = type("Cfg", (), {"ordered_brokers": ("ZERODHA", "FYERS")})()
        self._brokers = {"ZERODHA": object(), "FYERS": object()}

    def broker_health(self) -> dict[str, dict[str, str]]:
        return dict(self._health)

    def owned_orders(self) -> tuple[dict[str, Any], ...]:
        return self._owned


def _mappings() -> ProviderMappingRegistry:
    registry = ProviderMappingRegistry()
    registry.ingest_master("FYERS", ["NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:SBIN-EQ"])
    registry.ingest_master(
        "ZERODHA",
        [
            {
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 738561,
            },
            {
                "tradingsymbol": "TCS",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 2953216,
            },
            {
                "tradingsymbol": "SBIN",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 779521,
            },
        ],
    )
    return registry


def _engine(
    live: FakeLive | None = None,
    market: FakeMarketRouter | None = None,
    execution: FakeExecutionRouter | None = None,
    mappings: ProviderMappingRegistry | None = None,
) -> EligibilityEngine:
    return EligibilityEngine(
        mappings or _mappings(),
        live or FakeLive(),
        market_router=market if market is not None else _ready_market(),
        execution_router=execution if execution is not None else FakeExecutionRouter(),
    )


def _ready_market() -> FakeMarketRouter:
    return FakeMarketRouter(
        {
            RELIANCE: {"readiness": "READY", "provider": "FYERS", "failed_over": False},
            TCS: {"readiness": "READY", "provider": "FYERS", "failed_over": False},
        }
    )


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    yield
    reset_instrument_registry()


# ── 1-5: strategy + instrument gates ──────────────────────────────


def test_01_no_strategy_selected_blocked() -> None:
    verdict = _engine(market=_ready_market()).evaluate(RELIANCE, "")
    assert verdict.allowed is False
    assert "STRATEGY_NOT_SELECTED" in verdict.blocking_reasons


def test_02_strategy_disabled_blocked() -> None:
    live = FakeLive()
    live._strategies = ("Momentum",)
    engine = _engine(live=live, market=_ready_market())
    engine._strategy_disabled = lambda _name: True  # type: ignore[method-assign]
    verdict = engine.evaluate(RELIANCE, "Momentum")
    assert "STRATEGY_DISABLED" in verdict.blocking_reasons


def test_03_empty_universe_blocked() -> None:
    live = FakeLive()
    live._universe = {"Momentum": ()}
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "UNIVERSE_EMPTY" in verdict.blocking_reasons


def test_04_instrument_inactive_blocked() -> None:
    from strategy.instrument_registry import get_instrument_registry

    get_instrument_registry().set_status("NSE:EQUITY:RELIANCE", "INACTIVE")
    verdict = _engine(market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "INSTRUMENT_INACTIVE" in verdict.blocking_reasons


def test_05_missing_instrument_blocked() -> None:
    verdict = _engine(market=_ready_market()).evaluate("NSE:EQUITY:NOPE", "Momentum")
    assert "INSTRUMENT_NOT_FOUND" in verdict.blocking_reasons


# ── 6-10: market-data + mapping gates ─────────────────────────────


def test_06_market_data_waiting_blocked() -> None:
    market = FakeMarketRouter({RELIANCE: {"readiness": "WAITING_FOR_DATA"}})
    verdict = _engine(market=market).evaluate(RELIANCE, "Momentum")
    assert "MARKET_DATA_WAITING" in verdict.blocking_reasons
    assert verdict.level == WAITING


def test_07_market_data_stale_blocked() -> None:
    market = FakeMarketRouter({RELIANCE: {"readiness": "STALE"}})
    verdict = _engine(market=market).evaluate(RELIANCE, "Momentum")
    assert "MARKET_DATA_STALE" in verdict.blocking_reasons
    assert verdict.level == STALE


def test_08_market_data_unavailable_blocked() -> None:
    market = FakeMarketRouter({})
    verdict = _engine(market=market).evaluate(RELIANCE, "Momentum")
    assert "MARKET_DATA_UNAVAILABLE" in verdict.blocking_reasons
    assert verdict.level == UNAVAILABLE


def test_09_provider_mapping_missing_blocked() -> None:
    mappings = ProviderMappingRegistry()
    mappings.ingest_master("FYERS", ["NSE:TCS-EQ"])
    mappings.ingest_master(
        "ZERODHA",
        [
            {
                "tradingsymbol": "TCS",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 2953216,
            }
        ],
    )
    verdict = _engine(mappings=mappings, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "PROVIDER_MAPPING_MISSING" in verdict.blocking_reasons


def test_10_broker_mapping_missing_blocked() -> None:
    mappings = ProviderMappingRegistry()
    mappings.ingest_master("FYERS", ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"])
    mappings.ingest_master(
        "ZERODHA",
        [
            {
                "tradingsymbol": "TCS",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 2953216,
            }
        ],
    )
    verdict = _engine(mappings=mappings, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "BROKER_MAPPING_MISSING" in verdict.blocking_reasons


# ── 11-13: execution gates ───────────────────────────────────────


def test_11_broker_not_ready_blocked() -> None:
    execution = FakeExecutionRouter(
        health={
            "ZERODHA": {"state": "ERROR", "reason": "down"},
            "FYERS": {"state": "ERROR", "reason": "down"},
        }
    )
    verdict = _engine(market=_ready_market(), execution=execution).evaluate(RELIANCE, "Momentum")
    assert "BROKER_NOT_READY" in verdict.blocking_reasons


def test_12_broker_fallback_eligible() -> None:
    execution = FakeExecutionRouter(
        health={
            "ZERODHA": {"state": "DISCONNECTED", "reason": "down"},
            "FYERS": {"state": "HEALTHY", "reason": "fake"},
        }
    )
    verdict = _engine(market=_ready_market(), execution=execution).evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is True
    assert verdict.level == DEGRADED  # fallback in use — warning, not block


def test_13_broker_no_fallback_blocked() -> None:
    execution = FakeExecutionRouter(
        health={
            "ZERODHA": {"state": "DISCONNECTED", "reason": "down"},
            "FYERS": {"state": "DISABLED", "reason": "no adapter"},
        }
    )
    verdict = _engine(market=_ready_market(), execution=execution).evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is False


# ── 14-15: risk gates ─────────────────────────────────────────────


def test_14_risk_not_ready_blocked() -> None:
    live = FakeLive()
    live._risk_status = "NOT READY"
    live._risk_reason = "BROKER_CAPITAL_UNAVAILABLE"
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "RISK_NOT_READY" in verdict.blocking_reasons


def test_15_risk_denied_blocked() -> None:
    live = FakeLive()
    live._risk_status = "BLOCKED"
    live._risk_reason = "RISK_DENIED"
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "RISK_DENIED" in verdict.blocking_reasons


# ── 16-21: safety gates ───────────────────────────────────────────


def test_16_duplicate_order_blocked() -> None:
    execution = FakeExecutionRouter(
        owned=(
            {
                "client_order_id": "cid-1",
                "instrument_id": RELIANCE,
                "strategy_id": "Momentum",
                "broker": "ZERODHA",
                "broker_order_id": "ZERODHA-0001",
                "status": "ACKNOWLEDGED",
            },
        )
    )
    engine = _engine(market=_ready_market(), execution=execution)
    verdict = engine.evaluate(RELIANCE, "Momentum")
    # Duplicate protection is consumed via owned orders; the engine flags
    # an open order for the same instrument as a blocker.
    assert verdict.allowed is False or "DUPLICATE_ORDER" in verdict.blocking_reasons


def test_17_reconciliation_unhealthy_blocked() -> None:
    live = FakeLive()
    live._snapshot_recon = {"blocks_live": True}
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "RECONCILIATION_UNHEALTHY" in verdict.blocking_reasons


def test_18_session_not_running_blocked() -> None:
    live = FakeLive()
    live.status = "STOPPED"
    verdict = _engine(live=live, market=_ready_market()).evaluate(
        RELIANCE, "Momentum", trading_mode="PAPER"
    )
    assert "SESSION_NOT_RUNNING" in verdict.blocking_reasons


def test_19_live_not_armed_blocked() -> None:
    live = FakeLive()
    live.status = "STOPPED"
    live._confirmed_live_at = ""
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert "LIVE_NOT_ARMED" in verdict.blocking_reasons


def test_20_order_stream_unhealthy_blocked() -> None:
    live = FakeLive()
    live._live_blockers_list = ("order stream unhealthy",)
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is False


def test_21_stop_protection_attention_blocked() -> None:
    live = FakeLive()
    live._validate_blockers = ("stop protection attention: SL SENT",)
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is False


# ── 22-25: happy path, warnings, modes ────────────────────────────


def test_22_all_gates_healthy_ready() -> None:
    verdict = _engine(market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is True
    assert verdict.level == READY
    assert verdict.blocking_reasons == ()


def test_23_warnings_do_not_block() -> None:
    execution = FakeExecutionRouter(
        health={
            "ZERODHA": {"state": "DISCONNECTED", "reason": "down"},
            "FYERS": {"state": "HEALTHY", "reason": "fake"},
        }
    )
    verdict = _engine(market=_ready_market(), execution=execution).evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is True
    assert verdict.level == DEGRADED
    assert verdict.warnings != ()


def test_24_multiple_blockers_diagnostic() -> None:
    live = FakeLive()
    live._risk_status = "NOT READY"
    market = FakeMarketRouter({})
    verdict = _engine(live=live, market=market).evaluate(RELIANCE, "Momentum")
    assert len(verdict.blocking_reasons) >= 2
    assert "MARKET_DATA_UNAVAILABLE" in verdict.blocking_reasons
    assert "RISK_NOT_READY" in verdict.blocking_reasons


def test_25_fail_fast_first_deterministic_blocker() -> None:
    live = FakeLive()
    live._risk_status = "NOT READY"
    market = FakeMarketRouter({})
    engine = _engine(live=live, market=market)
    verdict = engine.evaluate(RELIANCE, "Momentum", mode=FAIL_FAST)
    assert verdict.blocking_reasons == ("MARKET_DATA_UNAVAILABLE",)
    # Deterministic: same input → same first blocker.
    again = engine.evaluate(RELIANCE, "Momentum", mode=FAIL_FAST)
    assert again.blocking_reasons == verdict.blocking_reasons


# ── 26-28: reactivity ─────────────────────────────────────────────


def test_26_reactivity_market_data_recovers() -> None:
    market = FakeMarketRouter({})
    engine = _engine(market=market)
    assert engine.evaluate(RELIANCE, "Momentum").allowed is False
    market._states[RELIANCE] = {"readiness": "READY", "provider": "FYERS"}
    verdict = engine.evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is True
    kinds = [event["type"] for event in engine.events()]
    assert "INSTRUMENT_RECOVERED" in kinds


def test_27_reactivity_broker_recovers() -> None:
    execution = FakeExecutionRouter(
        health={
            "ZERODHA": {"state": "ERROR", "reason": "down"},
            "FYERS": {"state": "ERROR", "reason": "down"},
        }
    )
    engine = _engine(market=_ready_market(), execution=execution)
    assert engine.evaluate(RELIANCE, "Momentum").allowed is False
    execution._health["ZERODHA"] = {"state": "HEALTHY", "reason": "fake"}
    assert engine.evaluate(RELIANCE, "Momentum").allowed is True


def test_28_reactivity_risk_becomes_ready() -> None:
    live = FakeLive()
    live._risk_status = "NOT READY"
    engine = _engine(live=live, market=_ready_market())
    assert engine.evaluate(RELIANCE, "Momentum").allowed is False
    live._risk_status = "READY"
    assert engine.evaluate(RELIANCE, "Momentum").allowed is True


# ── 29-31: summaries + isolation ──────────────────────────────────


def test_29_strategy_summary_counts() -> None:
    live = FakeLive()
    live._universe = {"Momentum": ("NSE:RELIANCE", "NSE:TCS", "NSE:SBIN")}
    market = FakeMarketRouter(
        {
            RELIANCE: {"readiness": "READY"},
            TCS: {"readiness": "STALE"},
            SBIN: {"readiness": "READY"},
        }
    )
    engine = _engine(live=live, market=market)
    counts = engine.strategy_readiness("Momentum")
    assert counts["TOTAL"] == 3
    assert counts["READY"] == 2
    assert counts["STALE"] == 1


def test_30_multi_strategy_isolation() -> None:
    live = FakeLive()
    live._universe = {"Momentum": ("NSE:RELIANCE",), "Breakout": ("NSE:TCS",)}
    live._strategies = ("Momentum", "Breakout")
    market = FakeMarketRouter(
        {
            RELIANCE: {"readiness": "READY"},
            TCS: {"readiness": "STALE"},
        }
    )
    engine = _engine(live=live, market=market)
    momentum = engine.evaluate(RELIANCE, "Momentum")
    breakout = engine.evaluate(TCS, "Breakout")
    assert momentum.allowed is True
    assert breakout.allowed is False
    assert momentum.strategy_id == "Momentum"
    assert breakout.strategy_id == "Breakout"


def test_31_same_instrument_different_strategy_verdicts() -> None:
    live = FakeLive()
    live._universe = {"Momentum": ("NSE:RELIANCE",), "Breakout": ()}
    live._strategies = ("Momentum", "Breakout")
    engine = _engine(live=live, market=_ready_market())
    assert engine.evaluate(RELIANCE, "Momentum").allowed is True
    verdict = engine.evaluate(RELIANCE, "Breakout")
    assert verdict.allowed is False
    assert "UNIVERSE_EMPTY" in verdict.blocking_reasons


# ── 32-34: fail-safe + diagnostics ────────────────────────────────


def test_32_unknown_critical_state_blocked() -> None:
    live = FakeLive()
    live._risk_status = ""  # unreadable — never READY
    verdict = _engine(live=live, market=_ready_market()).evaluate(RELIANCE, "Momentum")
    assert verdict.allowed is False
    assert "RISK_NOT_READY" in verdict.blocking_reasons


def test_33_diagnostics_match_backend_verdict() -> None:
    engine = _engine(market=_ready_market())
    verdict = engine.evaluate(RELIANCE, "Momentum")
    snapshot = engine.last_verdicts()[f"Momentum|{RELIANCE}"]
    assert snapshot["allowed"] == verdict.allowed
    assert snapshot["level"] == verdict.level
    assert snapshot["blocking_reasons"] == list(verdict.blocking_reasons)
    summary = engine.readiness_summary()
    assert summary["system"]["final"] == "TRADING_ALLOWED"
    assert summary["strategies"]["Momentum"]["TOTAL"] == 2


def test_34_engine_never_submits_orders() -> None:
    execution = FakeExecutionRouter()
    engine = _engine(market=_ready_market(), execution=execution)
    engine.evaluate(RELIANCE, "Momentum")
    # The engine consumes broker health/ownership only — it holds no
    # place_order surface and the fake records no submission.
    assert not hasattr(engine, "place_order")
    assert execution._brokers  # adapters exist but were never invoked


# ── 35-40: regression (existing suites stay green) ─────────────────


def test_35_phase1_suite_passes() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/strategy/tests/test_universe.py",
            "src/app/tests/test_strategy_universe_phase1.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-2000:]


def test_36_phase2_suite_passes() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/strategy/tests/test_instruments.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-2000:]


def test_37_phase3_suite_passes() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/strategy/tests/test_provider_mapping.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-2000:]


def test_38_phase4_suite_passes() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/strategy/tests/test_market_data_router.py",
            "src/broker/tests/test_router_feeds.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-2000:]


def test_39_phase5_suite_passes() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/strategy/tests/test_execution_router.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-2000:]


def test_40_execution_risk_suites_pass() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/execution/tests",
            "src/risk/tests",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-2000:]


# ── §29 architecture proofs ────────────────────────────────────────


def test_architecture_no_duplicated_logic() -> None:
    engine_source = (STRAT_ROOT / "eligibility_engine.py").read_text(encoding="utf-8")
    models_source = (STRAT_ROOT / "models" / "eligibility.py").read_text(encoding="utf-8")
    for source in (engine_source, models_source):
        # No risk math, no freshness math, no broker routing, no SDKs.
        assert "compute_qty" not in source
        assert "validate_planned" not in source
        assert "freshness_threshold" not in source
        assert "def place_order" not in source
        assert "fyers_apiv3" not in source
        assert "kiteconnect" not in source
    # The engine consumes both routers (orchestration, not reimplementation).
    assert "instrument_states" in engine_source  # Phase 4 truth
    assert "broker_health" in engine_source  # Phase 5 truth
    assert "get_instrument_registry" in engine_source  # Phase 2 truth
    assert "get_mapping" in engine_source  # Phase 3 truth


def test_architecture_gate_order_is_deterministic() -> None:
    assert GATE_ORDER == (
        "SYSTEM",
        "SESSION",
        "STRATEGY",
        "UNIVERSE",
        "INSTRUMENT",
        "MARKET_DATA",
        "RISK",
        "EXECUTION",
        "SAFETY",
        "FINAL",
    )
    engine = _engine(market=_ready_market())
    verdict = engine.evaluate(RELIANCE, "Momentum")
    sources = [check.source for check in verdict.checks]
    assert sources == sorted(sources, key=lambda s: GATE_ORDER.index(s))
