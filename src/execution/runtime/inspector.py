"""Strategy capability inspector — derive and validate live requirements.

Conventions (all optional class attributes on the strategy logic class;
safe defaults apply and gaps are reported, never invented):

- ``LIVE_INSTRUMENTS``: tuple of symbols, default ``()``
- ``LIVE_TIMEFRAMES``: tuple of timeframe labels, default ``()``
- ``LIVE_INDICATORS``: tuple of indicator names, default ``()``
- ``LIVE_ORDER_TYPES``: default ``("MARKET",)``
- ``LIVE_POSITION_MODEL``: default ``"single"``
- ``LIVE_MAX_POSITION_SIZE``: default None
- ``SUPPORTS_LIVE``: default False — capability, NOT readiness
- ``SUPPORTS_BACKTEST`` (legacy name ``SUPPORTS_PAPER``): default True
- ``LIVE_ENTRY`` / ``LIVE_EXIT``: human-readable condition text, default ""
- ``LIVE_RISK_ASSUMPTIONS``: default ()
- ``LIVE_EXPECTED_SIGNALS_PER_DAY`` / ``LIVE_LATENCY_SENSITIVE``: defaults

Precedence between a strategy's explicit declaration on
:class:`~strategy.models.definition.StrategyDefinition` and these legacy class
attributes is deterministic: **a non-empty declaration wins; an empty
declaration falls back to the legacy attribute** — so a strategy that declares
nothing behaves exactly as before, and a strategy that declares something can
never be overridden by a stale class attribute.
"""

from __future__ import annotations

from typing import Any

from execution.models.contract import StrategyRuntimeContract
from strategy import StrategyDefinition, StrategyLogic

#: Mode label a strategy must declare support for before LIVE may use it.
LIVE_MODE = "LIVE"


def _attr(logic: Any, name: str, default: Any) -> Any:
    return getattr(logic if logic is not None else None, name, default) or default


def inspect_strategy(
    definition: StrategyDefinition, logic: StrategyLogic | None = None
) -> StrategyRuntimeContract:
    """Build the machine-readable live contract for a strategy.

    Never raises for missing declarations: gaps land in ``missing`` so the
    readiness gate can fail BEFORE live start with exact reasons.
    """
    missing: list[str] = []
    if logic is None:
        missing.append("strategy_logic")
    warmup = 0
    if logic is not None:
        try:
            warmup = max(0, int(logic.warmup()))
        except Exception:
            missing.append("warmup")
    params: tuple[str, ...] = ()
    try:
        params = tuple(str(k) for k in dict(definition.params))
    except Exception:
        missing.append("parameters")

    # ── declaration first, legacy attribute second ──────────────────
    timeframes = tuple(definition.supported_timeframes) or tuple(
        str(t) for t in _attr(logic, "LIVE_TIMEFRAMES", ())
    )
    warmup = max(warmup, max(0, int(definition.required_warmup_bars)))
    capabilities = tuple(definition.required_capabilities) or tuple(
        str(c) for c in _attr(logic, "LIVE_BROKER_CAPABILITIES", ())
    )
    supports_backtest = _attr(logic, "SUPPORTS_BACKTEST", None)
    if supports_backtest is None:
        supports_backtest = _attr(logic, "SUPPORTS_PAPER", True)
    indicators = tuple(str(i) for i in _attr(logic, "LIVE_INDICATORS", ()))
    supported_modes = tuple(definition.supported_modes)
    if LIVE_MODE not in supported_modes:
        missing.append(
            f"strategy declares no {LIVE_MODE} support (supported: {list(supported_modes)})"
        )
    return StrategyRuntimeContract(
        strategy_id=definition.id,
        strategy_version=definition.version,
        instruments=tuple(str(s) for s in _attr(logic, "LIVE_INSTRUMENTS", ())),
        timeframes=timeframes,
        data_requirements=("candle-close",),
        indicators_required=indicators,
        warmup_bars=warmup,
        parameters=params,
        position_model=str(_attr(logic, "LIVE_POSITION_MODEL", "single")),
        order_types=tuple(str(o) for o in _attr(logic, "LIVE_ORDER_TYPES", ("MARKET",))),
        max_position_size=_attr(logic, "LIVE_MAX_POSITION_SIZE", None),
        expected_signals_per_day=_attr(logic, "LIVE_EXPECTED_SIGNALS_PER_DAY", None),
        latency_sensitive=bool(_attr(logic, "LIVE_LATENCY_SENSITIVE", False)),
        stateful=True,
        persistence_required=True,
        supports_live=bool(_attr(logic, "SUPPORTS_LIVE", False)),
        supports_backtest=bool(supports_backtest),
        supported_modes=supported_modes,
        required_broker_capabilities=capabilities,
        entry_description=str(_attr(logic, "LIVE_ENTRY", "")),
        exit_description=str(_attr(logic, "LIVE_EXIT", "")),
        risk_assumptions=tuple(str(a) for a in _attr(logic, "LIVE_RISK_ASSUMPTIONS", ())),
        missing=tuple(missing),
    )


def describe_requirements(contract: StrategyRuntimeContract) -> str:
    """Answer: what does this strategy need in order to run live?"""
    lines = [
        f"strategy {contract.strategy_id} v{contract.strategy_version}",
        f"  instruments: {', '.join(contract.instruments) or 'any'}",
        f"  timeframes: {', '.join(contract.timeframes) or 'any'}",
        f"  data: {', '.join(contract.data_requirements)}",
        f"  indicators: {', '.join(contract.indicators_required) or 'none declared'}",
        f"  warmup: {contract.warmup_bars} bars",
        f"  parameters: {', '.join(contract.parameters) or 'none'}",
        f"  position model: {contract.position_model}; "
        f"order types: {', '.join(contract.order_types)}",
        f"  modes: {', '.join(contract.supported_modes)}",
        f"  live capable: {contract.supports_live}; backtest capable: {contract.supports_backtest}",
        f"  broker capabilities: {', '.join(contract.required_broker_capabilities) or 'none'}",
    ]
    if contract.missing:
        lines.append(f"  MISSING: {', '.join(contract.missing)}")
    return "\n".join(lines)


def evaluate_signal_requirements(contract: StrategyRuntimeContract) -> tuple[str, ...]:
    """Signal-side requirements a strategy imposes on the runtime."""
    requirements = ["candle-close events", "position state per bar"]
    if contract.position_model != "single":
        requirements.append(f"position model: {contract.position_model}")
    return tuple(requirements)


__all__ = ["inspect_strategy", "describe_requirements", "evaluate_signal_requirements", "LIVE_MODE"]
