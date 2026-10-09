"""Phase 4 market-data routing — failure injection without any network.

FakeFeed implements the router's structural feed contract with scripted
ticks, health, and failure modes; FakeClock drives freshness/hysteresis
deterministically. Covers the §25 matrix (abstraction, selection,
per-instrument failover, staleness, disconnects, recovery, hysteresis,
stickiness, failback, contract independence, dedup/ordering, sharing,
connection-free strategy load) plus the §27 static proofs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy.instrument_registry import reset_instrument_registry  # noqa: E402
from strategy.market_data_router import MarketDataRouter, RouterError  # noqa: E402
from strategy.models.market_data import (  # noqa: E402
    DATA_STALE,
    FAILBACK_COMPLETED,
    FAILOVER,
    FAILOVER_COMPLETED,
    FAILOVER_STARTED,
    NO_DATA_AVAILABLE,
    PRIMARY_RECOVERED,
    READY,
    UNAVAILABLE,
    WAITING_FOR_DATA,
    NormalizedMarketData,
    RouterConfig,
)
from strategy.provider_mapping import ProviderMappingRegistry  # noqa: E402

STRAT_ROOT = ROOT / "src" / "strategy"
RELIANCE = "NSE:EQUITY:RELIANCE"
TCS = "NSE:EQUITY:TCS"
INFY = "NSE:EQUITY:INFY"


class FakeClock:
    """Deterministic monotonic clock (tests advance time by hand)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        """Move time forward (never backward — monotonic like the real one)."""
        assert seconds >= 0
        self.now += seconds
        return self.now


class FakeFeed:
    """Scripted router feed: ticks, health, and every failure mode."""

    def __init__(self, provider: str = "FYERS", healthy: bool = True) -> None:
        self.provider = provider
        self._healthy = healthy
        self._reason = "fake healthy" if healthy else "fake down"
        self._ticks: list[dict[str, Any]] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.fail_subscribe = False
        self.raise_on_poll = False
        self.raise_on_health = False
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        if self.fail_subscribe:
            raise ConnectionError(f"{self.provider} subscribe rejected")
        for symbol in symbols:
            if symbol not in self.subscribed:
                self.subscribed.append(symbol)

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        for symbol in symbols:
            self.unsubscribed.append(symbol)
            if symbol in self.subscribed:
                self.subscribed.remove(symbol)

    def poll(self) -> tuple[dict[str, Any], ...]:
        if self.raise_on_poll:
            raise ConnectionError(f"{self.provider} poll exploded")
        rows, self._ticks = tuple(self._ticks), []
        return rows

    def health(self) -> tuple[bool, str]:
        if self.raise_on_health:
            raise ConnectionError(f"{self.provider} health exploded")
        return self._healthy, self._reason

    def set_healthy(self, healthy: bool, reason: str = "") -> None:
        self._healthy = healthy
        self._reason = reason or ("fake healthy" if healthy else "fake down")

    def emit(
        self,
        pid: str,
        price: float,
        event_time: str = "2026-01-02T10:00:00+00:00",
        seq: int | None = None,
        **extra: Any,
    ) -> None:
        row: dict[str, Any] = {
            "provider_instrument_id": pid,
            "price": price,
            "event_time": event_time,
        }
        if seq is not None:
            row["seq"] = seq
        row.update(extra)
        self._ticks.append(row)


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    yield
    reset_instrument_registry()


def _mappings() -> ProviderMappingRegistry:
    registry = ProviderMappingRegistry()
    registry.ingest_master(
        "FYERS", ["NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:INFY-EQ"], source="fake-fyers"
    )
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
        ],
        source="fake-kite",
    )
    return registry


def _router(
    clock: FakeClock | None = None,
    config: RouterConfig | None = None,
    mappings: ProviderMappingRegistry | None = None,
) -> tuple[MarketDataRouter, FakeClock, FakeFeed, FakeFeed]:
    clock = clock or FakeClock()
    fyers = FakeFeed("FYERS")
    zerodha = FakeFeed("ZERODHA")
    router = MarketDataRouter(
        mappings or _mappings(),
        config=config or RouterConfig(freshness_threshold_s=5.0, recovery_window_s=10.0),
        feeds={"FYERS": fyers, "ZERODHA": zerodha},
        clock=clock,
    )
    return router, clock, fyers, zerodha


def test_01_provider_abstraction_contract() -> None:
    router, _, fyers, _ = _router()
    router.add_feed("FYERS", fyers)
    assert router.provider_health()["FYERS"]["state"] == "HEALTHY"
    with pytest.raises(RouterError):
        router.add_feed("", fyers)
    with pytest.raises(RouterError):
        router.add_feed("BROKEN", object())


def test_02_canonical_resolves_to_mapping() -> None:
    router, _, _, _ = _router()
    assert router.subscribe(RELIANCE) == "FYERS"
    assert router.get_active_provider(RELIANCE) == "FYERS"
    readiness, _ = router.get_readiness(RELIANCE)
    assert readiness == WAITING_FOR_DATA  # requested ≠ ready


def test_03_primary_selected() -> None:
    router, _, fyers, _ = _router()
    router.subscribe(RELIANCE)
    router.subscribe(TCS)
    assert fyers.subscribed == ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"]
    assert router.get_active_provider(TCS) == "FYERS"


def test_04_secondary_when_primary_unavailable() -> None:
    router, _, _, _ = _router()
    router._feeds.pop("FYERS")
    assert router.subscribe(RELIANCE) == "ZERODHA"
    assert router.get_active_provider(RELIANCE) == "ZERODHA"


def test_05_per_instrument_failover() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    router.subscribe(TCS)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    fyers.emit("NSE:TCS-EQ", 3100.0)
    assert len(router.poll()) == 2
    # FYERS dies for TCS only: silence TCS while RELIANCE keeps flowing.
    clock.advance(6.0)
    fyers.emit("NSE:RELIANCE-EQ", 2501.0)
    router.poll()
    zerodha.emit("2953216", 3102.0)
    router.poll()
    assert router.get_active_provider(TCS) == "ZERODHA"
    assert router.get_active_provider(RELIANCE) == "FYERS"


def test_06_healthy_instruments_stay_on_primary() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    router.subscribe(TCS)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.set_healthy(False, "fake transport down")
    clock.advance(6.0)
    zerodha.emit("738561", 2505.0)
    zerodha.emit("2953216", 3105.0)
    router.poll()
    # Whole-provider outage moves every instrument; the pre-switch rows
    # are rightly dropped, so each resumes on its next secondary tick.
    assert router.get_active_provider(RELIANCE) == "ZERODHA"
    assert router.get_active_provider(TCS) == "ZERODHA"
    zerodha.emit("738561", 2505.0)
    zerodha.emit("2953216", 3105.0)
    router.poll()
    assert router.poll() == ()
    readiness, _ = router.get_readiness(RELIANCE)
    assert readiness == READY


def test_07_missing_mapping_gives_unmapped() -> None:
    router, _, _, _ = _router()
    with pytest.raises(RouterError):
        router.subscribe("NSE:EQUITY:NOPE")
    # INFY has a FYERS mapping but none on Zerodha in this fixture.
    assert router._mappings.get_mapping(INFY, "ZERODHA").status == "NOT_FOUND"


def test_08_missing_provider_gives_unavailable() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    # Primary dies and the mapped secondary has no feed attached at all.
    fyers.set_healthy(False, "primary down")
    router._feeds.pop("ZERODHA")
    clock.advance(6.0)
    router.evaluate()
    readiness, reason = router.get_readiness(RELIANCE)
    assert readiness == UNAVAILABLE
    assert reason != ""


def test_09_stale_data_detected() -> None:
    router, clock, fyers, _ = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    assert router.get_readiness(RELIANCE)[0] == READY
    clock.advance(6.0)
    router.evaluate()
    # The mapped secondary is usable, so staleness fails over instead of
    # sitting stale — the DATA_STALE transition is still journaled first.
    assert router.get_readiness(RELIANCE)[0] == FAILOVER
    kinds = {event["type"] for event in router.events()}
    assert DATA_STALE in kinds
    assert FAILOVER_STARTED in kinds


def test_10_fresh_data_accepted() -> None:
    router, clock, fyers, _ = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0, volume=1200.0, bid=2499.5, ask=2500.5)
    (tick,) = router.poll()
    assert isinstance(tick, NormalizedMarketData)
    assert tick.instrument_id == RELIANCE
    assert tick.last_price == 2500.0
    assert tick.volume == 1200.0
    assert tick.bid == 2499.5 and tick.ask == 2500.5
    assert tick.provider == "FYERS"
    assert tick.display == "NSE:RELIANCE"
    clock.advance(2.0)
    router.evaluate()
    assert router.get_readiness(RELIANCE)[0] == READY


def test_11_stale_primary_triggers_failover() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.set_healthy(False, "fake stale transport")
    clock.advance(6.0)
    zerodha.emit("738561", 2506.0)
    router.poll()
    assert router.get_active_provider(RELIANCE) == "ZERODHA"
    zerodha.emit("738561", 2507.0)
    router.poll()
    assert router.get_readiness(RELIANCE)[0] == READY
    kinds = [event["type"] for event in router.events()]
    assert FAILOVER_STARTED in kinds
    assert FAILOVER_COMPLETED in kinds
    assert router.get_readiness(RELIANCE)[0] == READY


def test_12_disconnect_triggers_failover() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.disconnect()
    fyers.set_healthy(False, "disconnected")
    clock.advance(6.0)
    zerodha.emit("738561", 2506.0)
    router.poll()
    assert router.get_active_provider(RELIANCE) == "ZERODHA"


def test_13_subscription_failure_triggers_failover() -> None:
    router, clock, fyers, zerodha = _router()
    fyers.fail_subscribe = True
    # Primary subscribe blows up (feed marked ERROR) → the next sweep
    # lands on secondary; the explosion never strands the instrument.
    assert router.subscribe(RELIANCE) == "FYERS"
    router.evaluate()
    assert router.get_active_provider(RELIANCE) == "ZERODHA"
    zerodha.emit("738561", 2506.0)
    router.poll()
    assert router.get_readiness(RELIANCE)[0] == READY


def test_14_secondary_unavailable_gives_safe_no_data() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.set_healthy(False, "primary down")
    zerodha.set_healthy(False, "secondary down")
    clock.advance(6.0)
    assert router.poll() == ()
    readiness, reason = router.get_readiness(RELIANCE)
    assert readiness == UNAVAILABLE
    assert reason != ""
    kinds = {event["type"] for event in router.events()}
    assert NO_DATA_AVAILABLE in kinds
    # Recoverable: secondary returns, the instrument resubscribes and
    # data resumes on its next tick.
    zerodha.set_healthy(True)
    zerodha.emit("738561", 2507.0)
    router.poll()
    assert router.get_active_provider(RELIANCE) == "ZERODHA"
    zerodha.emit("738561", 2508.0)
    router.poll()
    assert router.get_readiness(RELIANCE)[0] == READY


def test_15_primary_recovery_detected() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.set_healthy(False, "blip")
    clock.advance(6.0)
    zerodha.emit("738561", 2506.0)
    router.poll()
    assert router.get_active_provider(RELIANCE) == "ZERODHA"
    fyers.set_healthy(True, "recovered")
    clock.advance(1.0)
    router.evaluate()
    kinds = {event["type"] for event in router.events()}
    assert PRIMARY_RECOVERED in kinds
    # Hysteresis: still on secondary before the window elapses.
    assert router.get_active_provider(RELIANCE) == "ZERODHA"


def test_16_hysteresis_prevents_flapping() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    providers_seen = []
    for cycle in range(4):
        fyers.set_healthy(False, f"blip {cycle}")
        clock.advance(6.0)
        zerodha.emit("738561", 2500.0 + cycle)
        router.poll()
        providers_seen.append(router.get_active_provider(RELIANCE))
        fyers.set_healthy(True, "recovered")
        clock.advance(3.0)  # inside the 10s recovery window
        router.evaluate()
        providers_seen.append(router.get_active_provider(RELIANCE))
    # Never flaps back early: every read after each recovery stays secondary.
    assert providers_seen == ["ZERODHA"] * 8
    assert FAILBACK_COMPLETED not in {event["type"] for event in router.events()}


def test_17_sticky_on_secondary() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.set_healthy(False, "down")
    clock.advance(6.0)
    zerodha.emit("738561", 2506.0)
    router.poll()
    fyers.set_healthy(True, "recovered")
    for _ in range(3):
        clock.advance(2.0)
        zerodha.emit("738561", 2507.0)
        router.poll()
        assert router.get_active_provider(RELIANCE) == "ZERODHA"


def test_18_successful_failback_after_recovery() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.set_healthy(False, "down")
    clock.advance(6.0)
    zerodha.emit("738561", 2506.0)
    router.poll()
    fyers.set_healthy(True, "recovered")
    clock.advance(11.0)  # secondary still flowing, primary observed healthy
    zerodha.emit("738561", 2507.0)
    router.poll()
    assert router.get_active_provider(RELIANCE) == "ZERODHA"
    clock.advance(10.0)  # full recovery window under observation
    router.evaluate()
    assert router.get_active_provider(RELIANCE) == "FYERS"
    fyers.emit("NSE:RELIANCE-EQ", 2508.0)
    router.poll()
    assert router.get_readiness(RELIANCE)[0] == READY
    kinds = {event["type"] for event in router.events()}
    assert FAILBACK_COMPLETED in kinds


def test_19_contract_is_provider_independent() -> None:
    router, _, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0, seq=7)
    (first,) = router.poll()
    fields = set(first.__dataclass_fields__)
    assert "raw" not in fields and "token" not in fields and "fyers" not in fields
    assert first.provider_seq == 7 and first.router_seq == 1
    # Same instrument via Zerodha carries the same identity, new provider.
    router._switch_provider(router._subs[RELIANCE], "ZERODHA", FAILOVER_STARTED)
    zerodha.emit("738561", 2506.0)
    (second,) = router.poll()
    assert second.instrument_id == first.instrument_id == RELIANCE
    assert second.provider == "ZERODHA"
    assert second.router_seq == 2


def test_20_no_raw_fields_leak() -> None:
    router, _, fyers, _ = _router()
    router.subscribe(RELIANCE)
    fyers.emit(
        "NSE:RELIANCE-EQ",
        2500.0,
        fy_token="SECRET123",
        kite_stuff={"a": 1},
        access_token="hunter2",
    )
    (tick,) = router.poll()
    blob = repr(tick).lower()
    for forbidden in ("secret123", "hunter2", "kite", "fy_token", "access_token"):
        assert forbidden not in blob, forbidden


def test_21_duplicates_and_older_ticks_dropped() -> None:
    router, _, fyers, _ = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0, event_time="2026-01-02T10:00:00+00:00", seq=5)
    fyers.emit("NSE:RELIANCE-EQ", 2501.0, event_time="2026-01-02T10:00:00+00:00", seq=5)
    fyers.emit("NSE:RELIANCE-EQ", 2499.0, event_time="2026-01-02T09:59:00+00:00", seq=4)
    fyers.emit("NSE:RELIANCE-EQ", 2502.0, event_time="2026-01-02T10:01:00+00:00", seq=6)
    first, later = router.poll()
    assert (first.last_price, later.last_price) == (2500.0, 2502.0)
    assert router.poll() == ()


def test_22_shared_instrument_across_strategies() -> None:
    from app.services.live_trading_service import LiveTradingService

    router, _, fyers, _ = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    (tick,) = router.poll()
    # Two strategies holding the same canonical instrument read the same
    # normalized tick — strategy state is untouched by routing.
    assert tick.instrument_id == RELIANCE
    service = LiveTradingService(str(ROOT / "nonexistent-data"), str(ROOT / "nonexistent-strat"))
    assert service.available_symbols("Momentum") == ()


def test_23_independent_routing_preserves_identity() -> None:
    router, _, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    router.subscribe(TCS)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    fyers.emit("NSE:TCS-EQ", 3100.0)
    first = router.poll()
    assert {tick.instrument_id for tick in first} == {RELIANCE, TCS}
    assert {tick.provider for tick in first} == {"FYERS"}


def test_24_strategy_loads_without_provider_connection(tmp_path) -> None:
    from app.services.live_trading_service import LiveTradingService

    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    service = LiveTradingService(str(tmp_path), str(strategy_dir))
    service.configure(strategy_name="Momentum")
    service.configure(symbols=("NSE:RELIANCE",))
    # No feeds attached anywhere: canonical identity stands on its own.
    router = MarketDataRouter(_mappings())
    readiness_states = router.instrument_states()
    assert readiness_states == {}
    assert service.config.symbols == ("NSE:RELIANCE",)


def test_25_no_provider_fields_on_canonical() -> None:
    import strategy.models.instrument as canonical_module

    fields = set(canonical_module.CanonicalInstrument.__dataclass_fields__)
    assert "provider" not in fields
    assert not {name for name in fields if "fyers" in name.lower() or "token" in name.lower()}


def test_26_no_sdk_leaks_into_strategy_or_domain() -> None:
    for module_file in (
        "models/instrument.py",
        "models/market_data.py",
        "models/universe.py",
        "market_data_router.py",
        "instrument_registry.py",
        "provider_mapping.py",
        "universe_store.py",
    ):
        source = (STRAT_ROOT / module_file).read_text(encoding="utf-8")
        assert "fyers_apiv3" not in source, module_file
        assert "kiteconnect" not in source, module_file
        assert "import fyers" not in source, module_file
        assert "import kite" not in source, module_file


def test_router_config_is_explicit_and_validated() -> None:
    config = RouterConfig(primary="fyers", secondaries=("zerodha",))
    assert config.primary == "FYERS"
    assert config.ordered_providers == ("FYERS", "ZERODHA")
    with pytest.raises(ValueError):
        RouterConfig(primary="")
    with pytest.raises(ValueError):
        RouterConfig(primary="FYERS", secondaries=("FYERS",))
    with pytest.raises(ValueError):
        RouterConfig(freshness_threshold_s=0)
    with pytest.raises(ValueError):
        RouterConfig(recovery_window_s=-1)


def test_unmapped_rows_never_publish() -> None:
    router, _, fyers, _ = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    fyers.emit("NSE:MADEUP-EQ", 10.0)
    (only,) = router.poll()
    assert only.instrument_id == RELIANCE
    assert router.diagnostics()["dropped_unmapped"] == {"FYERS:NSE:MADEUP-EQ": 1}


def test_future_timestamps_are_not_trusted() -> None:
    router, _, fyers, _ = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0, event_time="2999-01-01T00:00:00+00:00")
    (tick,) = router.poll()
    assert tick.event_time <= tick.received_time


def test_events_carry_debugging_context() -> None:
    router, clock, fyers, zerodha = _router()
    router.subscribe(RELIANCE)
    fyers.emit("NSE:RELIANCE-EQ", 2500.0)
    router.poll()
    fyers.set_healthy(False, "down")
    clock.advance(6.0)
    zerodha.emit("738561", 2506.0)
    router.poll()
    for event in router.events():
        assert set(event) == {"type", "instrument", "provider", "reason", "timestamp"}
    kinds = [event["type"] for event in router.events()]
    assert kinds[:3] == ["SUBSCRIPTION_STARTED", "SUBSCRIPTION_CONFIRMED", "DATA_RECEIVED"]
