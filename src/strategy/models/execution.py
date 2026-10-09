"""Execution routing contract — intent in, normalized result out (Phase 5).

``OrderIntent`` is the provider-independent order (canonical identity +
trade facts + routing metadata). ``NormalizedOrderResult`` is the
provider-independent outcome (Rust ``OrderState`` string values — the
single lifecycle vocabulary, projected here without forking its
transition table, which stays Rust-owned).

Two hard rules shape this contract:
- The router NEVER alters quantity/price (adapters receive exact values;
  sizing stays with the existing risk pipeline, which must approve first:
  ``risk_approved`` defaults False and routing refuses without it).
- ``broker_order_id`` is diagnostic-only: core logic must never branch
  on it (ownership lookups key on ``client_order_id``).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

#: Order lifecycle values (Rust OrderState projection — values only, the
#: transition table stays Rust-owned; never extended here).
SUBMITTED = "SUBMITTED"
ACKNOWLEDGED = "ACKNOWLEDGED"
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"
MODIFIED = "MODIFIED"
FILLED = "FILLED"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
UNKNOWN = "UNKNOWN"

#: Routing decisions (router-owned, not order states).
ROUTED_PRIMARY = "ROUTED_PRIMARY"
ROUTED_SECONDARY = "ROUTED_SECONDARY"
REJECTED_SAFE = "REJECTED_SAFE"
ATTENTION_REQUIRED = "BROKER_ATTENTION_REQUIRED"

#: Safe-rejection reasons (exact strings reach diagnostics).
RISK_APPROVAL_MISSING = "risk approval missing — gates must pass first"
INSTRUMENT_NOT_FOUND = "canonical instrument not found"
BROKER_MAPPING_MISSING = "broker mapping missing"
BROKER_MAPPING_DISABLED = "broker mapping disabled"
BROKER_MAPPING_CONFLICT = "broker mapping conflict"
BROKER_NOT_READY = "broker not ready"
BROKER_UNAVAILABLE = "broker unavailable"
NO_READY_BROKER = "no ready broker"
UNKNOWN_CLIENT_ORDER = "unknown client order — never routed here"
INVALID_INTENT = "invalid order intent"
DUPLICATE_INTENT = "duplicate client order — returning prior result"
NO_READY_BROKER = "no ready broker"

_VALID_SIDES = ("BUY", "SELL")
_VALID_TYPES = ("MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT")
_VALID_MODES = ("PAPER", "SANDBOX", "LIVE")


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


@dataclass(frozen=True)
class OrderIntent:
    """One provider-independent order (canonical identity throughout).

    Duck-compatible with ``OrderPlan`` (``symbol/side/quantity/
    order_type/limit_price/stop_price/time_in_force``) so venue adapters
    consume it unchanged — with ``symbol`` carrying the canonical display
    at construction; the router swaps in the per-broker venue symbol on
    a private plan shim at submit time.

    Attributes:
        instrument_id: Stable ``NSE:EQUITY:*`` identity.
        symbol: Canonical display (``NSE:SBIN``) — adapters never see it.
        side/quantity/order_type/limit_price/stop_price/time_in_force:
            Trade facts, passed through EXACTLY (never resized).
        strategy_id: Owning strategy (isolation key).
        client_order_id: VAYREN-level idempotency key (auto uuid4-hex
            when empty — unique per intent, never reused).
        mode: PAPER | SANDBOX | LIVE (venue selector, never bypassed).
        risk_approved: Must be True (set only after the existing
            risk/safety pipeline passes) or routing refuses.
        created_at: ISO-8601 UTC creation stamp.
    """

    instrument_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "DAY"
    strategy_id: str = ""
    client_order_id: str = ""
    mode: str = "PAPER"
    risk_approved: bool = False
    created_at: str = ""

    def __post_init__(self) -> None:
        """Fill defaults (id + timestamp); field validation lives in check()."""
        if not self.client_order_id:
            object.__setattr__(self, "client_order_id", uuid.uuid4().hex)
        if not self.created_at:
            object.__setattr__(self, "created_at", _utcnow_iso())

    @classmethod
    def from_order_plan(cls, plan: Any, **overrides: Any) -> OrderIntent:
        """Build from an OrderPlan-shaped object (duck-typed, no import)."""
        get = lambda name, default=None: getattr(plan, name, default)  # noqa: E731
        try:
            quantity = float(get("quantity", 0.0) or 0.0)
        except (TypeError, ValueError):
            quantity = 0.0
        return cls(
            instrument_id=str(overrides.get("instrument_id", "")),
            symbol=str(get("symbol", "")),
            side=str(get("side", "")),
            quantity=quantity,
            order_type=str(get("order_type", "MARKET")),
            limit_price=get("limit_price"),
            stop_price=get("stop_price"),
            time_in_force=str(get("time_in_force", "DAY")),
            **{k: v for k, v in overrides.items() if k != "instrument_id"},
        )

    def check(self) -> str | None:
        """Intent validation (shape only — risk math stays authoritative).

        Returns a refusal reason, or None when the intent is well-formed.
        Never touches a broker.
        """
        if not isinstance(self.instrument_id, str) or not self.instrument_id.strip():
            return f"{INVALID_INTENT}: instrument_id missing"
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            return f"{INVALID_INTENT}: symbol missing"
        side = str(self.side or "").strip().upper()
        if side not in _VALID_SIDES:
            return f"{INVALID_INTENT}: side must be BUY/SELL, got {self.side!r}"
        quantity = self.quantity
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, (int, float))
            or quantity != quantity
            or quantity in (float("inf"), float("-inf"))
            or quantity <= 0
        ):
            return f"{INVALID_INTENT}: quantity must be a positive finite number"
        order_type = str(self.order_type or "").strip().upper()
        if order_type not in _VALID_TYPES:
            return f"{INVALID_INTENT}: order_type must be one of {sorted(_VALID_TYPES)}"
        if order_type in ("LIMIT", "STOP_LIMIT") and (
            self.limit_price is None or not self._positive(self.limit_price)
        ):
            return f"{INVALID_INTENT}: limit price must be positive for {order_type}"
        if order_type in ("STOP_MARKET", "STOP_LIMIT") and (
            self.stop_price is None or not self._positive(self.stop_price)
        ):
            return f"{INVALID_INTENT}: stop price must be positive for {order_type}"
        if str(self.mode or "").strip().upper() not in _VALID_MODES:
            return f"{INVALID_INTENT}: mode must be PAPER/SANDBOX/LIVE"
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            return f"{INVALID_INTENT}: strategy_id missing"
        if not isinstance(self.client_order_id, str) or not self.client_order_id.strip():
            return f"{INVALID_INTENT}: client_order_id missing"
        return None

    @staticmethod
    def _positive(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class NormalizedOrderResult:
    """One provider-independent execution outcome (diagnostic broker id).

    ``status`` carries Rust OrderState values; ``routing`` carries the
    router decision; ``attention_required`` marks orders needing an
    operator (original broker gone — never silently moved).
    """

    client_order_id: str
    instrument_id: str
    strategy_id: str
    broker: str
    broker_order_id: str
    status: str
    routing: str
    submitted_quantity: float
    filled_quantity: float = 0.0
    average_price: float | None = None
    rejection_reason: str = ""
    attention_required: bool = False
    event_time: str = ""

    def __post_init__(self) -> None:
        """Fill the event stamp (all else is caller-supplied truth)."""
        if not self.event_time:
            object.__setattr__(self, "event_time", _utcnow_iso())


@dataclass(frozen=True)
class RouterExecutionConfig:
    """Explicit broker priority (no hard-wired venue choice).

    Attributes:
        primary: First-choice broker name (``ZERODHA``).
        secondary: Fallback broker name (``FYERS``).
    """

    primary: str = "ZERODHA"
    secondary: str = "FYERS"

    def __post_init__(self) -> None:
        """Normalize names; primary and secondary must differ."""
        primary = str(self.primary or "").strip().upper()
        secondary = str(self.secondary or "").strip().upper()
        if not primary or not secondary:
            raise ValueError("RouterExecutionConfig brokers must be non-empty")
        if primary == secondary:
            raise ValueError("RouterExecutionConfig primary and secondary must differ")
        object.__setattr__(self, "primary", primary)
        object.__setattr__(self, "secondary", secondary)

    @property
    def ordered_brokers(self) -> tuple[str, ...]:
        """Primary first, then secondary — the only priority order."""
        return (self.primary, self.secondary)


__all__ = [
    "OrderIntent",
    "NormalizedOrderResult",
    "RouterExecutionConfig",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "REJECTED",
    "CANCELLED",
    "MODIFIED",
    "FILLED",
    "PARTIALLY_FILLED",
    "UNKNOWN",
    "ROUTED_PRIMARY",
    "ROUTED_SECONDARY",
    "REJECTED_SAFE",
    "ATTENTION_REQUIRED",
    "RISK_APPROVAL_MISSING",
    "INSTRUMENT_NOT_FOUND",
    "BROKER_MAPPING_MISSING",
    "BROKER_MAPPING_DISABLED",
    "BROKER_MAPPING_CONFLICT",
    "BROKER_NOT_READY",
    "BROKER_UNAVAILABLE",
    "NO_READY_BROKER",
    "UNKNOWN_CLIENT_ORDER",
    "INVALID_INTENT",
    "DUPLICATE_INTENT",
    "NO_READY_BROKER",
]
