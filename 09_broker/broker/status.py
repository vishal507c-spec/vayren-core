"""Broker lifecycle states — the single status vocabulary for broker UI/ops.

Every broker surface (SYSTEM → BROKERS cards, LIVE readiness rows, the
manager snapshot) renders these exact strings. Fail-closed by shape:
unknown conditions map to ERROR, never to a ready-looking state.
"""

from __future__ import annotations

from enum import Enum


class BrokerStatus(Enum):
    """One broker's management-plane state (not the order path's mode)."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURING = "CONFIGURING"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"
    ACCOUNT_NOT_READY = "ACCOUNT_NOT_READY"
    MARKET_DATA_NOT_READY = "MARKET_DATA_NOT_READY"
    EXECUTION_NOT_READY = "EXECUTION_NOT_READY"
    LIVE_READY = "LIVE_READY"


READY_STATES = frozenset(
    {
        BrokerStatus.CONNECTED,
        BrokerStatus.LIVE_READY,
    }
)

__all__ = ["BrokerStatus", "READY_STATES"]
