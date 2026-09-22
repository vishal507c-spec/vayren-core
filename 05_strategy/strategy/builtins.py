"""Built-in strategies — small, clearly-marked, real OHLCV strategies.

These exist so Strategy Lab is usable on a fresh workstation with an empty
strategy library. Each entry maps a display name to its native
:class:`PythonStrategy` implementation in ``strategy.strategies``; all of
them execute against real historical bars through the normal compile/runtime
path — never fabricated results.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuiltinStrategy:
    """Identity facts for one built-in strategy."""

    name: str
    description: str
    tags: tuple[str, ...]
    version: str
    module: str
    classname: str


BUILTIN_STRATEGIES: tuple[BuiltinStrategy, ...] = (
    BuiltinStrategy(
        name="SMA Crossover",
        description="Buys when the fast SMA crosses above the slow SMA; sells on cross under.",
        tags=("built-in", "trend", "crossover"),
        version="1.0",
        module="strategy.strategies.sma",
        classname="SmaCrossover",
    ),
    BuiltinStrategy(
        name="EMA Crossover",
        description="Buys when the fast EMA crosses above the slow EMA; sells on cross under.",
        tags=("built-in", "trend", "crossover"),
        version="1.0",
        module="strategy.strategies.ema",
        classname="EmaCrossover",
    ),
    BuiltinStrategy(
        name="RSI Strategy",
        description="Buys oversold RSI readings and exits back to flat on overbought.",
        tags=("built-in", "momentum", "mean-reversion"),
        version="1.0",
        module="strategy.strategies.rsi",
        classname="RsiStrategy",
    ),
)


def list_builtins() -> tuple[BuiltinStrategy, ...]:
    """All built-in strategies (never empty, never invented at call time)."""
    return BUILTIN_STRATEGIES


def get_builtin(name: str):
    """The strategy class for a built-in `name` (KeyError when unknown)."""
    wanted = (name or "").strip().lower()
    for entry in BUILTIN_STRATEGIES:
        if entry.name.lower() == wanted:
            import importlib

            module = importlib.import_module(entry.module)
            return getattr(module, entry.classname)
    raise KeyError(f"unknown built-in strategy: {name!r}")


def is_builtin(name: str) -> bool:
    """True when `name` is a built-in strategy."""
    wanted = (name or "").strip().lower()
    return any(entry.name.lower() == wanted for entry in BUILTIN_STRATEGIES)


def builtin_source(name: str) -> str:
    """Full source file text backing a built-in (for the Lab editor echo)."""
    wanted = (name or "").strip().lower()
    for entry in BUILTIN_STRATEGIES:
        if entry.name.lower() == wanted:
            import importlib

            module = importlib.import_module(entry.module)
            path = Path(str(getattr(module, "__file__", "")))
            return path.read_text(encoding="utf-8")
    raise KeyError(f"unknown built-in strategy: {name!r}")
