"""Phase-3 LIVE position sizing — broker capital × 4 × 0.15% → quantity.

Single authoritative implementation of VAYREN's live risk formula::

    broker_available_capital
        ↓
    effective_capital = broker_available_capital × 4
        ↓
    max_risk_per_stock = effective_capital × 0.15%
        ↓
    risk_per_share = ABS(entry_price - stop_price)
        ↓
    quantity = FLOOR(max_risk_per_stock / risk_per_share)
        ↓
    planned_risk = quantity × risk_per_share   (must satisfy ≤ max_risk_per_stock)

Role in the architecture: this module owns the *sizing verdict* (fail-closed
orchestration over plain numbers, exactly what the RUST_RISK retention scope
allows Python to retain). It holds NO 18-gate checklist logic (that stays in
``risk.native_engine`` / ``crates/vayren-core``) and invents NO stop levels —
entry/stop arrive from the existing strategy/execution models and are only
consumed here. ``CapitalRiskEngine`` (UI projection in ``app``) and the
execution session delegate to this module instead of restating the formula.

Capital semantics (strict)::

    LIVE     → broker-reported AVAILABLE funds only. Unavailable / zero /
                negative / stale capital blocks with a structured reason.
                No fallback, no cache, no configured default.
    PAPER    → the existing simulated capital source (paper broker funds).
    SANDBOX  → the existing sandbox account source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Intraday leverage multiplier applied to broker available capital.
LEVERAGE_MULTIPLIER = 4.0

#: Maximum risk per stock as a fraction of effective capital (0.15%).
PER_TRADE_RISK_PCT = 0.0015

#: Broker capital older than this is STALE and must not size a live order.
CAPITAL_STALE_AFTER_SECONDS = 60.0

# ── structured verdict reasons (stable strings; UI and journals log them) ──

READY = "READY"
BROKER_CAPITAL_UNAVAILABLE = "BROKER_CAPITAL_UNAVAILABLE"
CAPITAL_UNAVAILABLE = "CAPITAL_UNAVAILABLE"
STALE_CAPITAL = "STALE_CAPITAL"
INVALID_ENTRY_PRICE = "INVALID_ENTRY_PRICE"
INVALID_STOP_PRICE = "INVALID_STOP_PRICE"
ZERO_RISK_PER_SHARE = "ZERO_RISK_PER_SHARE"
RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE = "RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE"
PLANNED_RISK_EXCEEDED = "PLANNED_RISK_EXCEEDED"
QUANTITY_INVALID = "QUANTITY_INVALID"
RISK_ENGINE_ERROR = "RISK_ENGINE_ERROR"

_LIVE_MODES = ("LIVE",)


@dataclass(frozen=True)
class BrokerCapital:
    """One authoritative capital reading. Plain data, no behavior.

    ``available`` is the broker's reported AVAILABLE leg (never equity,
    margin, buying power or a configured default). ``fetched_epoch`` is the
    unix time the reading was taken (``None`` = age unknown → stale).
    ``source`` names the origin: ``"broker"`` (live venue), ``"paper"``
    (simulated), ``"sandbox"`` (sandbox account) or ``"configured"``.
    """

    available: float | None
    fetched_epoch: float | None = None
    source: str = "broker"


@dataclass(frozen=True)
class SizingVerdict:
    """Outcome of one per-stock sizing request (fail-closed by construction).

    ``ready`` is True ONLY when a LIVE order may continue; otherwise
    ``reason`` names the blocker and ``quantity`` is 0. Every monetary leg
    is exposed so the UI can render the full pipeline without recomputing.
    """

    ready: bool
    reason: str
    broker_capital: float | None = None
    effective_capital: float | None = None
    max_risk_per_stock: float | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    risk_per_share: float | None = None
    quantity: int = 0
    planned_risk: float | None = None


def _blocked(
    reason: str,
    *,
    broker_capital: float | None = None,
    effective_capital: float | None = None,
    max_risk: float | None = None,
    entry_price: float | None = None,
    stop_price: float | None = None,
    risk_per_share: float | None = None,
) -> SizingVerdict:
    """Deny with a structured reason (quantity is always 0 when blocked)."""
    return SizingVerdict(
        ready=False,
        reason=reason,
        broker_capital=broker_capital,
        effective_capital=effective_capital,
        max_risk_per_stock=max_risk,
        entry_price=entry_price,
        stop_price=stop_price,
        risk_per_share=risk_per_share,
        quantity=0,
        planned_risk=None,
    )


def _as_finite(value: Any) -> float | None:
    """Finite float or ``None`` (non-numbers, NaN and inf are invalid)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def size_position(
    *,
    broker_capital: BrokerCapital | None,
    entry_price: Any,
    stop_price: Any,
    mode: str = "LIVE",
    now_epoch: float | None = None,
    leverage: float = LEVERAGE_MULTIPLIER,
    risk_pct: float = PER_TRADE_RISK_PCT,
    max_capital_age_seconds: float = CAPITAL_STALE_AFTER_SECONDS,
) -> SizingVerdict:
    """Size one stock from broker capital + entry/stop. Never raises.

    Broker capital determines max risk, entry/stop determine risk/share, and
    both combine ONLY at the quantity step. Any invalid input blocks with a
    structured reason instead of fabricating a quantity.
    """
    try:
        return _size_position(
            broker_capital=broker_capital,
            entry_price=entry_price,
            stop_price=stop_price,
            mode=str(mode or "LIVE").upper(),
            now_epoch=now_epoch,
            leverage=leverage,
            risk_pct=risk_pct,
            max_capital_age_seconds=max_capital_age_seconds,
        )
    except Exception:
        return _blocked(RISK_ENGINE_ERROR)


def _size_position(
    *,
    broker_capital: BrokerCapital | None,
    entry_price: Any,
    stop_price: Any,
    mode: str,
    now_epoch: float | None,
    leverage: float,
    risk_pct: float,
    max_capital_age_seconds: float,
) -> SizingVerdict:
    live = mode in _LIVE_MODES
    capital_reason = BROKER_CAPITAL_UNAVAILABLE if live else CAPITAL_UNAVAILABLE

    # ── 1. capital must be a real, positive broker reading ─────────────
    available = broker_capital.available if broker_capital is not None else None
    available = _as_finite(available)
    if available is None or available <= 0.0:
        return _blocked(capital_reason)
    if live and (broker_capital is None or broker_capital.source != "broker"):
        # LIVE never sizes from paper/configured/simulated capital.
        return _blocked(capital_reason, broker_capital=available)

    # ── 2. capital must be fresh ───────────────────────────────────────
    fetched = broker_capital.fetched_epoch if broker_capital is not None else None
    now = _as_finite(now_epoch)
    if fetched is None or now is None or (now - float(fetched)) > max_capital_age_seconds:
        return _blocked(STALE_CAPITAL, broker_capital=available)

    # ── 3. entry/stop come ONLY from the strategy/risk model ───────────
    entry = _as_finite(entry_price)
    if entry is None or entry <= 0.0:
        return _blocked(INVALID_ENTRY_PRICE, broker_capital=available)
    stop = _as_finite(stop_price)
    if stop is None or stop <= 0.0:
        return _blocked(INVALID_STOP_PRICE, broker_capital=available)

    # ── 4. exact formula (no alternate spelling) ──────────────────────
    effective_capital = available * leverage
    max_risk = effective_capital * risk_pct
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0.0:
        return _blocked(
            ZERO_RISK_PER_SHARE,
            broker_capital=available,
            effective_capital=effective_capital,
            max_risk=max_risk,
            entry_price=entry,
            stop_price=stop,
            risk_per_share=0.0,
        )
    quantity = int(math.floor(max_risk / risk_per_share))
    if quantity <= 0:
        return _blocked(
            RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE,
            broker_capital=available,
            effective_capital=effective_capital,
            max_risk=max_risk,
            entry_price=entry,
            stop_price=stop,
            risk_per_share=risk_per_share,
        )
    planned_risk = float(quantity) * risk_per_share
    # Float dust must never let planned risk exceed the ceiling: FLOOR is a
    # mathematical guarantee, so any overshoot is rounding — step down.
    while quantity > 0 and planned_risk > max_risk:
        quantity -= 1
        planned_risk = float(quantity) * risk_per_share
    if quantity <= 0 or planned_risk > max_risk:
        return _blocked(
            RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE,
            broker_capital=available,
            effective_capital=effective_capital,
            max_risk=max_risk,
            entry_price=entry,
            stop_price=stop,
            risk_per_share=risk_per_share,
        )
    return SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=available,
        effective_capital=effective_capital,
        max_risk_per_stock=max_risk,
        entry_price=entry,
        stop_price=stop,
        risk_per_share=risk_per_share,
        quantity=quantity,
        planned_risk=planned_risk,
    )


def validate_planned_quantity(
    *,
    quantity: Any,
    entry_price: Any,
    stop_price: Any,
    max_risk_per_stock: Any,
) -> tuple[bool, str]:
    """Final LIVE execution gate: ``planned_risk <= max_risk_per_stock``.

    Re-validates the FINAL planned quantity (even one sized earlier) so a
    manually inflated or planner-adjusted quantity can never slip through.
    Never raises; any invalid input denies.
    """
    try:
        qty = _as_finite(quantity)
        entry = _as_finite(entry_price)
        stop = _as_finite(stop_price)
        ceiling = _as_finite(max_risk_per_stock)
        if qty is None or entry is None or stop is None or ceiling is None:
            return False, PLANNED_RISK_EXCEEDED
        if qty <= 0 or qty != math.floor(qty):
            return False, QUANTITY_INVALID
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0.0:
            return False, ZERO_RISK_PER_SHARE
        planned = qty * risk_per_share
        if planned <= ceiling:
            return True, READY
        return False, PLANNED_RISK_EXCEEDED
    except Exception:
        return False, RISK_ENGINE_ERROR


__all__ = [
    "BrokerCapital",
    "SizingVerdict",
    "CAPITAL_STALE_AFTER_SECONDS",
    "LEVERAGE_MULTIPLIER",
    "PER_TRADE_RISK_PCT",
    "READY",
    "BROKER_CAPITAL_UNAVAILABLE",
    "CAPITAL_UNAVAILABLE",
    "STALE_CAPITAL",
    "INVALID_ENTRY_PRICE",
    "INVALID_STOP_PRICE",
    "ZERO_RISK_PER_SHARE",
    "RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE",
    "PLANNED_RISK_EXCEEDED",
    "QUANTITY_INVALID",
    "RISK_ENGINE_ERROR",
    "size_position",
    "validate_planned_quantity",
]
