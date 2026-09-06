"""Risk policy, decisions and requests — frozen, auditable value objects."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskPolicy:
    """Hard limits enforced before ANY order. Fail-closed by construction.

    All quantities are in absolute units unless noted. ``None`` disables the
    check it guards, except where the check is structural (duplicate orders,
    kill switch) and therefore always on.
    """

    max_position_qty: float = 1000.0
    max_order_qty: float = 500.0
    max_notional: float | None = None
    max_exposure_pct: float | None = None
    daily_loss_limit: float | None = None
    strategy_loss_limit: float | None = None
    allowed_symbols: tuple[str, ...] = ()
    spread_limit_pct: float | None = None
    slippage_limit_pct: float | None = None
    require_fresh_data_seconds: float | None = 60.0
    session_start: str | None = None  # "HH:MM", None = no session gate
    session_end: str | None = None
    cooldown_seconds: float = 0.0
    max_orders_per_day: int | None = None


@dataclass(frozen=True)
class RiskRequest:
    """One order intent asking for a risk verdict. Plain data, no behavior."""

    intent_id: str
    strategy_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float  # reference price (expected fill basis)
    timestamp: str  # ISO-8601 event time of the originating signal
    position_qty: float = 0.0  # signed current position in symbol
    day_pnl: float = 0.0
    strategy_day_pnl: float = 0.0
    equity: float = 0.0
    available_capital: float = 0.0
    spread_pct: float | None = None
    data_age_seconds: float | None = None
    broker_healthy: bool = True
    orders_today: int = 0
    last_order_epoch: float | None = None
    now_epoch: float = 0.0


@dataclass(frozen=True)
class RiskCheck:
    """One named gate result inside a decision (audit trail)."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class RiskDecision:
    """Verdict for one RiskRequest. Denied unless every check passes."""

    approved: bool
    intent_id: str
    reasons: tuple[str, ...] = ()
    checks: tuple[RiskCheck, ...] = field(default_factory=tuple)
