"""Unified broker session state — one authoritative snapshot for every UI screen.

The EC2 backend owns the LIVE FYERS session. Windows UI, LIVE, dashboard and
health panels all consume this snapshot. It distinguishes user-requested state
from authentication validity from per-leg transport health, so a partial
failure (REST ok, market WS down) is never reported as fully connected.

States: DISCONNECTED, CHECKING, CONNECTING, LOGIN_REQUIRED, VERIFYING,
CONNECTED, DEGRADED, RATE_LIMITED, RECONNECTING, ERROR, UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SESSION_STATES = (
    "DISCONNECTED",
    "CHECKING",
    "VALIDATING_SESSION",
    "CONNECTING",
    "AUTHENTICATING",
    "FETCHING_TOKEN",
    "VERIFYING",
    "VERIFYING_CONNECTION",
    "CONNECTED",
    "DEGRADED",
    "RATE_LIMITED",
    "RETRY_WAIT",
    "AUTH_REQUIRED",
    "LOGIN_REQUIRED",
    "RECONNECTING",
    "ERROR",
    "FAILED",
    "UNKNOWN",
)

LEG_NAMES = ("rest", "market_ws", "order_ws")

# Backend BrokerStatus -> unified session state (fail-closed: unknown -> UNKNOWN).
_BROKER_STATUS_MAP = {
    "NOT_CONFIGURED": "DISCONNECTED",
    "LOGIN_REQUIRED": "LOGIN_REQUIRED",
    "AUTH_REQUIRED": "LOGIN_REQUIRED",
    "AUTHENTICATING": "CONNECTING",
    "CONFIGURING": "CONNECTING",
    "VALIDATING_SESSION": "CONNECTING",
    "FETCHING_TOKEN": "CONNECTING",
    "VERIFYING_CONNECTION": "VERIFYING",
    "RETRY_WAIT": "RATE_LIMITED",
    "FAILED": "ERROR",
    "CONNECTED": "CONNECTED",
    "LIVE_READY": "CONNECTED",
    "DISCONNECTED": "DISCONNECTED",
    "ERROR": "ERROR",
    "ACCOUNT_NOT_READY": "ERROR",
    "MARKET_DATA_NOT_READY": "DEGRADED",
    "EXECUTION_NOT_READY": "DEGRADED",
}


def map_broker_status(status_raw: str, configured: bool) -> str:
    """Map a BrokerStatus string onto the unified session vocabulary."""
    text = str(status_raw or "").strip().upper()
    if not text:
        return "UNKNOWN"
    if text in _BROKER_STATUS_MAP:
        mapped = _BROKER_STATUS_MAP[text]
        # Configured-but-never-authed LOGIN_REQUIRED stays LOGIN_REQUIRED;
        # unconfigured is simply DISCONNECTED (never LOGIN_REQUIRED).
        if text == "LOGIN_REQUIRED" and not configured:
            return "DISCONNECTED"
        return mapped
    if text in SESSION_STATES:
        return text
    return "UNKNOWN"


def next_action_for(state: str) -> str:
    """Single recommended next action per unified state (one clear action)."""
    actions = {
        "DISCONNECTED": "CONNECT from SYSTEM → BROKERS",
        "CHECKING": "Wait — checking saved session",
        "VALIDATING_SESSION": "Wait — checking saved session",
        "CONNECTING": "Wait — authentication in progress",
        "AUTHENTICATING": "Wait — authentication in progress",
        "FETCHING_TOKEN": "Wait — acquiring session tokens",
        "LOGIN_REQUIRED": (
            "Daily token expired or login required — click CONNECT in SYSTEM → BROKERS"
        ),
        "AUTH_REQUIRED": (
            "Daily token expired or login required — click CONNECT in SYSTEM → BROKERS"
        ),
        "VERIFYING": "Wait — verifying account with FYERS",
        "VERIFYING_CONNECTION": "Wait — verifying account with FYERS",
        "CONNECTED": "No action needed — session verified",
        "DEGRADED": "Reconnect failed leg from SYSTEM → BROKERS",
        "RATE_LIMITED": "Wait — retrying automatically with backoff",
        "RETRY_WAIT": "Wait — retrying automatically with backoff",
        "RECONNECTING": "Wait — reconnecting failed leg",
        "ERROR": "Review error and RETRY from SYSTEM → BROKERS",
        "FAILED": "Review error and RETRY from SYSTEM → BROKERS",
        "UNKNOWN": "Refresh status from SYSTEM → BROKERS",
    }
    return actions.get(str(state).upper(), "Refresh status from SYSTEM → BROKERS")


@dataclass(frozen=True)
class LegHealth:
    """Health of one connection leg (REST / market WS / order WS)."""

    name: str
    status: str = "UNKNOWN"
    last_success: str = ""
    last_error: str = ""
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "consecutive_failures": int(self.consecutive_failures),
        }


@dataclass(frozen=True)
class UnifiedSessionSnapshot:
    """One authoritative backend session snapshot for every UI screen."""

    broker_id: str = ""
    desired: str = "DISCONNECTED"
    state: str = "UNKNOWN"
    auth_valid: bool = False
    account_id: str = ""
    funds_available: float | None = None
    funds_used: float | None = None
    funds_total: float | None = None
    legs: tuple = field(default_factory=tuple)
    last_verified: str = ""
    last_error: str = ""
    last_error_kind: str = ""
    retry_after_s: float = 0.0
    version: int = 0
    live_armed: bool = False
    strategy_running: bool = False
    reason: str = ""
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_id": self.broker_id,
            "desired": self.desired,
            "state": self.state,
            "auth_valid": bool(self.auth_valid),
            "account_id": self.account_id,
            "funds_available": self.funds_available,
            "funds_used": self.funds_used,
            "funds_total": self.funds_total,
            "legs": [leg.to_dict() if hasattr(leg, "to_dict") else leg for leg in self.legs],
            "last_verified": self.last_verified,
            "last_error": self.last_error,
            "last_error_kind": self.last_error_kind,
            "retry_after_s": float(self.retry_after_s or 0.0),
            "version": int(self.version),
            "live_armed": bool(self.live_armed),
            "strategy_running": bool(self.strategy_running),
            "reason": self.reason,
            "next_action": self.next_action or next_action_for(self.state),
        }


def build_snapshot(
    *,
    broker_id: str = "",
    desired: str = "DISCONNECTED",
    state: str = "UNKNOWN",
    auth_valid: bool = False,
    account_id: str = "",
    funds: dict[str, Any] | None = None,
    legs: list[LegHealth] | list[dict[str, Any]] | None = None,
    last_verified: str = "",
    last_error: str = "",
    last_error_kind: str = "",
    retry_after_s: float = 0.0,
    version: int = 0,
    live_armed: bool = False,
    strategy_running: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """Build the serializable unified snapshot dict (all keys always present)."""
    funds = funds or {}
    leg_dicts: list[dict[str, Any]] = []
    for leg in legs or []:
        if hasattr(leg, "to_dict"):
            leg_dicts.append(leg.to_dict())  # type: ignore[union-attr]
        elif isinstance(leg, dict):
            leg_dicts.append(
                {
                    "name": str(leg.get("name", "")),
                    "status": str(leg.get("status", "UNKNOWN")),
                    "last_success": str(leg.get("last_success", "") or ""),
                    "last_error": str(leg.get("last_error", "") or ""),
                    "consecutive_failures": int(leg.get("consecutive_failures", 0) or 0),
                }
            )
    snap = UnifiedSessionSnapshot(
        broker_id=str(broker_id or ""),
        desired=str(desired or "DISCONNECTED"),
        state=str(state or "UNKNOWN"),
        auth_valid=bool(auth_valid),
        account_id=str(account_id or ""),
        funds_available=_safe_float((funds or {}).get("available")),
        funds_used=_safe_float((funds or {}).get("used")),
        funds_total=_safe_float((funds or {}).get("total")),
        legs=tuple(leg_dicts),
        last_verified=str(last_verified or ""),
        last_error=str(last_error or ""),
        last_error_kind=str(last_error_kind or ""),
        retry_after_s=float(retry_after_s or 0.0),
        version=int(version or 0),
        live_armed=bool(live_armed),
        strategy_running=bool(strategy_running),
        reason=str(reason or ""),
        next_action=next_action_for(str(state or "UNKNOWN")),
    )
    return snap.to_dict()


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


__all__ = [
    "LEG_NAMES",
    "SESSION_STATES",
    "LegHealth",
    "UnifiedSessionSnapshot",
    "build_snapshot",
    "map_broker_status",
    "next_action_for",
]
