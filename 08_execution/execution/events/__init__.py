"""Live execution events — normalized market data + journal facts.

Conventions mirror the existing catalog: frozen dataclasses, ``Event``
base, data-only payloads (no widgets, connections or callables).
"""

from dataclasses import dataclass

from core import Event

# ── normalized market-data events (provider-agnostic) ──────────────


@dataclass(frozen=True)
class MarketEvent(Event):
    """Base for normalized market data. Sequence is per-stream monotonic."""

    symbol: str
    timestamp: str  # UTC ISO-8601, normalized at ingestion
    seq: int
    source: str = ""  # provider name that produced it


@dataclass(frozen=True)
class QuoteEvent(MarketEvent):
    """Best bid/ask snapshot."""

    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0


@dataclass(frozen=True)
class TradeEvent(MarketEvent):
    """One tape print."""

    price: float = 0.0
    quantity: float = 0.0


@dataclass(frozen=True)
class CandleEvent(MarketEvent):
    """One closed (or updating) OHLCV candle."""

    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    timeframe: str = ""
    is_closed: bool = True


@dataclass(frozen=True)
class OrderBookEvent(MarketEvent):
    """Top-N depth snapshot (empty when the provider has no book)."""

    bids: tuple[tuple[float, float], ...] = ()
    asks: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class HeartbeatEvent(MarketEvent):
    """Provider liveness pulse; gaps in heartbeats mean disconnect risk."""

    status: str = "ok"


# ── execution journal facts (observable pipeline trace) ────────────


@dataclass(frozen=True)
class SignalGenerated(Event):
    request_id: str
    strategy_id: str
    signal_id: str
    event_seq: int


@dataclass(frozen=True)
class RiskApproved(Event):
    request_id: str
    intent_id: str


@dataclass(frozen=True)
class RiskDenied(Event):
    request_id: str
    intent_id: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrderPlanned(Event):
    request_id: str
    intent_id: str
    client_order_id: str


@dataclass(frozen=True)
class OrderSubmitted(Event):
    request_id: str
    client_order_id: str


@dataclass(frozen=True)
class OrderAcknowledged(Event):
    request_id: str
    client_order_id: str
    broker_order_id: str = ""


@dataclass(frozen=True)
class OrderFill(Event):
    request_id: str
    client_order_id: str
    fill_qty: float
    fill_price: float
    partial: bool = False


@dataclass(frozen=True)
class OrderRejected(Event):
    request_id: str
    client_order_id: str
    reason: str = ""


@dataclass(frozen=True)
class PositionUpdated(Event):
    request_id: str
    symbol: str
    quantity: float


@dataclass(frozen=True)
class KillSwitchEngaged(Event):
    request_id: str
    level: str
    reason: str
