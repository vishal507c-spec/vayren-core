"""Execution intent chain — signal → intent → plan. All frozen and traceable."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategySignal:
    """A strategy decision normalized for the live pipeline.

    Built from a backtest-compatible :class:`Signal` plus live context, so
    the same strategy code feeds both paths without modification.
    """

    signal_id: str
    strategy_id: str
    strategy_version: str
    timestamp: str
    event_seq: int
    symbol: str
    side: str  # "BUY" or "SELL"
    price: float  # reference price at signal time
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = ""
    confidence: float = 1.0


def make_intent_id(strategy_id: str, strategy_version: str, event_seq: int, intent_seq: int) -> str:
    """Deterministic idempotency key: same input events always yield same ids."""
    return f"{strategy_id}:{strategy_version}:{event_seq}:{intent_seq}"


@dataclass(frozen=True)
class ExecutionIntent:
    """What the strategy wants, before risk has spoken. Never an order yet."""

    intent_id: str
    strategy_id: str
    strategy_version: str
    signal_id: str
    timestamp: str
    event_seq: int
    symbol: str
    side: str  # "BUY" or "SELL"
    target_position_qty: float  # signed desired end state
    quantity: float  # absolute policed quantity to trade now
    urgency: str = "normal"  # patient | normal | urgent
    preferred_order_type: str = "MARKET"  # MARKET | LIMIT
    reason: str = ""
    confidence: float = 1.0
