"""Strategy component manifest — structural description of the platform.

The ``core`` contract authorities (``ComponentManifest`` and friends) are
Rust-owned and have no Python twin, so this manifest is a plain structural
dict — not a live enforcement object. It documents the component identity,
capabilities, events and dependencies factually; enforcement lives with the
registry/runtime/tests. Importing this module must never fail.
"""

from __future__ import annotations

from typing import Any


def strategy_manifest() -> dict[str, Any]:
    """Return the structural manifest of the strategy component."""
    return {
        "identity": "strategy",
        "version": "1.0.0",
        "type": "domain",
        "capabilities": (
            {
                "id": "strategy.registry",
                "description": "register and resolve strategy kinds and definitions",
                "inputs": ("definition: StrategyDefinition",),
                "outputs": ("definitions: tuple[StrategyDefinition, ...]",),
                "rules": {
                    "guarantees": (
                        "explicit registration only",
                        "parameters validated against kind specs",
                    ),
                    "failure_modes": ("unknown kind rejected", "duplicate id rejected"),
                },
            },
            {
                "id": "strategy.runtime",
                "description": "execute strategy logic bar by bar into signals",
                "inputs": ("bars: tuple[Bar, ...]", "params: StrategyParameters"),
                "outputs": ("signals: tuple[Signal, ...]",),
            },
        ),
        "inputs": ("bars: tuple[Bar, ...]",),
        "outputs": ("signals: tuple[Signal, ...]",),
        "dependencies": ("core", "market"),
        "events_consumed": (),
        "events_produced": (
            "StrategiesListed",
            "StrategySelected",
            "PaperTradeRequested",
            "LabReset",
        ),
        "invariants": ("no fabricated signals", "logic state isolated per run"),
    }
