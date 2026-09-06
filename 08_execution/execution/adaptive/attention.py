"""Attention layer — prioritize market events, never drop silently.

Scores each event 0..1 for processing priority (active instrument,
signal-relevant moves, volatility shifts, execution-critical updates).
Low-priority ticks may be SKIPPED under load, but every skip is counted
and the latest price is always retained — prioritization, not loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from execution.events import CandleEvent, MarketEvent, QuoteEvent, TradeEvent


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
        self.skipped = 0
        self.processed = 0

    def _price_of(self, event: MarketEvent) -> float | None:
        if isinstance(event, CandleEvent):
            return event.close
        if isinstance(event, TradeEvent):
            return event.price
        if isinstance(event, QuoteEvent):
            mid = (event.bid + event.ask) / 2.0 if event.ask > 0 else 0.0
            return mid if mid > 0 else None
        return None

    def score(self, event: MarketEvent) -> AttentionDecision:
        cfg = self._config
        if cfg.always_process_candles and isinstance(event, CandleEvent):
            return AttentionDecision(True, 1.0, ("candle",))
        score = 0.2
        reasons: list[str] = []
        if cfg.active_symbols and event.symbol in cfg.active_symbols:
            score += 0.4
            reasons.append("active instrument")
        price = self._price_of(event)
        if price is not None:
            previous = self._last_price.get(event.symbol)
            if previous:
                move_pct = abs(price - previous) / previous * 100.0
                if move_pct >= cfg.price_move_threshold_pct:
                    score += 0.3
                    reasons.append(f"price move {move_pct:.2f}%")
                if move_pct >= cfg.volatility_threshold_pct:
                    score += 0.1
                    reasons.append("volatility shift")
        return AttentionDecision(True, min(1.0, score), tuple(reasons))

    def observe(self, event: MarketEvent, under_load: bool = False) -> AttentionDecision:
        """Score and, only under load, skip sub-0.5 events (counted)."""
        decision = self.score(event)
        price = self._price_of(event)
        if price is not None:
            self._last_price[event.symbol] = price
        if under_load and decision.score < 0.5 and not isinstance(event, CandleEvent):
            self.skipped += 1
            return AttentionDecision(False, decision.score, decision.reasons + ("shed under load",))
        self.processed += 1
        return decision


@dataclass
class AttentionSnapshot:
    processed: int = 0
    skipped: int = 0
    last_scores: dict[str, float] = field(default_factory=dict)
