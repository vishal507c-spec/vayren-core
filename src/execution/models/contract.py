"""Self-describing strategy contract — machine-readable live requirements."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StrategyRuntimeContract:
    """Everything the system must know before a strategy may run live.

    Derived automatically by the capability inspector where possible;
    strategy authors declare the remainder as class attributes (all
    optional — the inspector fills safe defaults and reports gaps).
    """

    strategy_id: str
    strategy_version: str
    instruments: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    data_requirements: tuple[str, ...] = ("candle-close",)
    indicators_required: tuple[str, ...] = ()
    warmup_bars: int = 0
    parameters: tuple[str, ...] = ()
    position_model: str = "single"  # single | long-short | portfolio
    order_types: tuple[str, ...] = ("MARKET",)
    max_position_size: float | None = None
    expected_signals_per_day: float | None = None
    latency_sensitive: bool = False
    stateful: bool = True
    persistence_required: bool = True
    supports_live: bool = False
    supports_paper: bool = True
    required_broker_capabilities: tuple[str, ...] = ()
    entry_description: str = ""
    exit_description: str = ""
    risk_assumptions: tuple[str, ...] = ()
    missing: tuple[str, ...] = field(default_factory=tuple)  # gaps found by inspection

    @property
    def complete(self) -> bool:
        return not self.missing
