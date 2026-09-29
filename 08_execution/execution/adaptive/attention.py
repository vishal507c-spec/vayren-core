"""Attention layer — prioritize market events, never drop silently.

Scores each event 0..1 for processing priority (active instrument,
signal-relevant moves, volatility shifts, execution-critical updates).
Low-priority ticks may be SKIPPED under load, but every skip is counted
and the latest price is always retained — prioritization, not loss.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

from execution.events import (
    CandleEvent,
    MarketEvent,
    OrderFill,
    OrderPlanned,
    OrderRejected,
    OrderSubmitted,
    QuoteEvent,
    TradeEvent,
)

# Journal facts that must never be shed: order lifecycle + fill + risk +
# kill-switch outcomes. Anything that is not market data (i.e. not a
# `MarketEvent`) is execution-critical by construction; order events are
# listed explicitly so the exemption survives future event additions.
_EXECUTION_CRITICAL_TYPES: tuple[type, ...] = (
    OrderFill,
    OrderPlanned,
    OrderSubmitted,
    OrderRejected,
)

_LAST_PRICE_MAX = 500


@dataclass
class AttentionConfig:
    active_symbols: tuple[str, ...] = ()
    price_move_threshold_pct: float = 0.1
    volatility_threshold_pct: float = 0.5
    always_process_candles: bool = True


@dataclass
class AttentionDecision:
    process: bool
    score: float
    reasons: tuple[str, ...] = ()


class AttentionFilter:
    """Stateless scorer + skip counter."""

    def __init__(self, config: AttentionConfig | None = None) -> None:
        self._config = config if config is not None else AttentionConfig()
        self._last_price: dict[str, float] = {}
        self._lock = threading.Lock()
        self.skipped = 0
        self.processed = 0

    @staticmethod
    def _is_execution_critical(event: MarketEvent) -> bool:
        """True for fill/order journal facts — never shed under load."""
        if isinstance(event, _EXECUTION_CRITICAL_TYPES):
            return True
        return not isinstance(event, MarketEvent)

    def _price_of(self, event: MarketEvent) -> float | None:
        if isinstance(event, CandleEvent):
            return event.close
        if isinstance(event, TradeEvent):
            return event.price
        if isinstance(event, QuoteEvent):
            for name, value in (("bid", event.bid), ("ask", event.ask)):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"attention quote {name} must be a number, got {value!r}")
                if not math.isfinite(float(value)):
                    raise ValueError(f"attention quote {name} must be finite, got {value!r}")
            mid = (event.bid + event.ask) / 2.0 if event.ask > 0 else 0.0
            return mid if mid > 0 else None
        return None

    def score(self, event: MarketEvent) -> AttentionDecision:
        cfg = self._config
        if cfg.always_process_candles and isinstance(event, CandleEvent):
            return AttentionDecision(True, 1.0, ("candle",))
        if self._is_execution_critical(event):
            return AttentionDecision(True, 1.0, ("execution-critical",))
        score = 0.2
        reasons: list[str] = []
        if cfg.active_symbols and event.symbol in cfg.active_symbols:
            score += 0.4
            reasons.append("active instrument")
        price = self._price_of(event)
        if price is not None:
            if not math.isfinite(price):
                raise ValueError(f"attention price must be finite, got {price!r}")
            previous = self._last_price.get(event.symbol)
            if previous is not None:
                if not math.isfinite(previous):
                    raise ValueError(f"attention previous price must be finite, got {previous!r}")
                if previous == 0.0:
                    # A zero baseline is valid market data (not "absent"):
                    # any non-zero print is a full move + volatility shift.
                    if price != 0.0:
                        score += 0.3
                        reasons.append("price move from zero baseline")
                        score += 0.1
                        reasons.append("volatility shift")
                else:
                    move_pct = abs(price - previous) / abs(previous) * 100.0
                    if move_pct >= cfg.price_move_threshold_pct:
                        score += 0.3
                        reasons.append(f"price move {move_pct:.2f}%")
                    if move_pct >= cfg.volatility_threshold_pct:
                        score += 0.1
                        reasons.append("volatility shift")
        score = min(1.0, score)
        # The process flag is the shed-path verdict: sub-0.5 ticks are
        # shed candidates under load (`observe` enforces it and counts).
        return AttentionDecision(score >= 0.5, score, tuple(reasons))

    def _remember_price(self, symbol: str, price: float) -> None:
        """Bounded latest-price memory (LRU, max 500 symbols)."""
        previous = self._last_price.pop(symbol, None)
        if previous is None and len(self._last_price) >= _LAST_PRICE_MAX:
            self._last_price.pop(next(iter(self._last_price)))
        self._last_price[symbol] = price

    def observe(self, event: MarketEvent, under_load: bool = False) -> AttentionDecision:
        """Score and, only under load, skip shed candidates (counted).

        Execution-critical journal facts (fills, order events) and candles
        are never shed — only sub-0.5 market-data ticks take the shed path.
        """
        decision = self.score(event)
        price = self._price_of(event)
        with self._lock:
            if price is not None:
                self._remember_price(event.symbol, price)
            if (
                under_load
                and not decision.process
                and not self._is_execution_critical(event)
                and not (self._config.always_process_candles and isinstance(event, CandleEvent))
            ):
                self.skipped += 1
                return AttentionDecision(
                    False, decision.score, decision.reasons + ("shed under load",)
                )
            self.processed += 1
            return decision


@dataclass
class AttentionSnapshot:
    processed: int = 0
    skipped: int = 0
    last_scores: dict[str, float] = field(default_factory=dict)
