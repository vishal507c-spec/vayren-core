"""Built-in strategies — permanently removed (system cleanup 2026-11-08).

The VAYREN system ships exactly TWO strategies: OBR and OBR C1C4, both
registered by `strategy.registry`. The former SMA/EMA crossover and RSI
built-ins were deleted from code, registry, Strategy Lab listings and
storage (backed up to `vayren-backup-20261108-strategy` before removal).
This module keeps its public API so existing imports keep working and
return an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinStrategy:
    """Identity facts for one built-in strategy (retained for API compat)."""

    name: str
    description: str
    tags: tuple[str, ...]
    version: str
    module: str
    classname: str


BUILTIN_STRATEGIES: tuple[BuiltinStrategy, ...] = ()


def list_builtins() -> tuple[BuiltinStrategy, ...]:
    """All built-in strategies — permanently empty (OBR/OBR C1C4 only)."""
    return BUILTIN_STRATEGIES


def get_builtin(name: str):
    """Always raises: no built-in strategies remain."""
    raise KeyError(f"unknown built-in strategy: {name!r}")


def is_builtin(name: str) -> bool:  # noqa: ARG001 — API compat, always False
    """Always False: no built-in strategies remain."""
    return False


def builtin_source(name: str) -> str:
    """Always raises: no built-in strategies remain."""
    raise KeyError(f"unknown built-in strategy: {name!r}")
