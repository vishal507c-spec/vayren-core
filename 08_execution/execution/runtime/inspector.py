"""Strategy capability inspector — derive and validate live requirements.

Conventions (all optional class attributes on the strategy logic class;
safe defaults apply and gaps are reported, never invented):

- ``LIVE_INSTRUMENTS``: tuple of symbols, default ``()``
- ``LIVE_TIMEFRAMES``: tuple of timeframe labels, default ``()``
- ``LIVE_INDICATORS``: tuple of indicator names, default ``()``
- ``LIVE_ORDER_TYPES``: default ``("MARKET",)``
- ``LIVE_POSITION_MODEL``: default ``"single"``
- ``LIVE_MAX_POSITION_SIZE``: default None
- ``SUPPORTS_LIVE`` / ``SUPPORTS_PAPER``: default False / True
- ``LIVE_ENTRY`` / ``LIVE_EXIT``: human-readable condition text, default ""
- ``LIVE_RISK_ASSUMPTIONS``: default ()
- ``LIVE_EXPECTED_SIGNALS_PER_DAY`` / ``LIVE_LATENCY_SENSITIVE``: defaults
"""

from __future__ import annotations

from typing import Any

from strategy import StrategyDefinition, StrategyLogic

from execution.models.contract import StrategyRuntimeContract


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
    indicators = tuple(str(i) for i in _attr(logic, "LIVE_INDICATORS", ()))
    return StrategyRuntimeContract(
        strategy_id=definition.id,
        strategy_version=definition.version,
        instruments=tuple(str(s) for s in _attr(logic, "LIVE_INSTRUMENTS", ())),
        timeframes=tuple(str(t) for t in _attr(logic, "LIVE_TIMEFRAMES", ())),
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
        supports_paper=bool(_attr(logic, "SUPPORTS_PAPER", True)),
        required_broker_capabilities=tuple(
            str(c) for c in _attr(logic, "LIVE_BROKER_CAPABILITIES", ())
        ),
        entry_description=str(_attr(logic, "LIVE_ENTRY", "")),
        exit_description=str(_attr(logic, "LIVE_EXIT", "")),
        risk_assumptions=tuple(str(r) for r in _attr(logic, "LIVE_RISK_ASSUMPTIONS", ())),
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
        f"  live: {contract.supports_live}; paper: {contract.supports_paper}",
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


__all__ = ["inspect_strategy", "describe_requirements", "evaluate_signal_requirements"]
