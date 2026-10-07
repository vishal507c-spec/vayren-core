"""Broker-neutral snapshot + event projection for remote clients.

Reads ONLY the existing live-service snapshot dict (the same shape the
native UI consumes) and re-projects it into the remote contract. No second
state machine: every value is copied or directly derived from backend
facts, with a ``source`` note wherever a section is derived rather than
verbatim. Anything that looks credential-like is scrubbed — broker
secrets can never reach a client even if an upstream shape ever changed.
"""

from __future__ import annotations

from typing import Any

#: Key fragments that must never travel to a remote client. Matched
#: case-insensitively against every dict key before it leaves the backend.
FORBIDDEN_KEY_SUBSTRINGS = (
    "secret",
    "token",
    "totp",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "private_key",
    "seed",
    "otp",
    "auth_code",
    "session_token",
    "access_token",
    "refresh_token",
)

#: Substrings that must never appear in serialized outbound traffic.
#: Checked by tests over real snapshots (defense in depth, not parsing).
#: Venue display names (e.g. "FYERS") are legitimate identity facts and are
#: NOT in this list — only credential-shaped fragments are.
FORBIDDEN_VALUE_MARKERS = (
    "secret",
    "totp",
    "password",
    "api_key",
    "access_token",
    "private_key",
)


def _is_forbidden_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(mark in lowered for mark in FORBIDDEN_KEY_SUBSTRINGS)


def scrub(value: Any) -> Any:
    """Deep-copy plain data while dropping credential-like keys."""
    if isinstance(value, dict):
        return {str(key): scrub(item) for key, item in value.items() if not _is_forbidden_key(key)}
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _strategy_status(live: dict[str, Any]) -> dict[str, Any]:
    """Map backend lifecycle facts to RUNNING / STOPPED / BLOCKED / ERROR."""
    status = str(live.get("session_status", "STOPPED") or "STOPPED").upper()
    blockers = list(live.get("start_blockers", []) or [])
    recon = live.get("reconciliation") or {}
    kill = live.get("kill") or {}
    if status == "RUNNING":
        if bool(kill.get("halted")):
            return {"status": "ERROR", "reason": "kill switch halted"}
        if bool(recon.get("blocks_live")):
            return {"status": "BLOCKED", "reason": "reconciliation blocks live"}
        return {"status": "RUNNING", "reason": ""}
    if status == "ERROR":
        return {
            "status": "ERROR",
            "reason": str(live.get("status_reason", "") or "backend error"),
        }
    if blockers or bool(recon.get("blocks_live")) or bool(kill.get("halted")):
        reason = "; ".join(blockers) if blockers else str(live.get("status_reason", ""))
        return {"status": "BLOCKED", "reason": reason}
    return {"status": "STOPPED", "reason": str(live.get("status_reason", "") or "")}


def _recon_status(live: dict[str, Any]) -> dict[str, Any]:
    """Map backend reconciliation facts to SYNCED / REQUIRED / ERROR."""
    recon = live.get("reconciliation") or {}
    raw = str(recon.get("status", "") or "").upper()
    if bool(recon.get("blocks_live")):
        return {"status": "REQUIRED", "detail": raw or "reconciliation blocks live"}
    if "ERROR" in raw or "MISMATCH" in raw:
        return {"status": "ERROR", "detail": raw}
    if "SYNC" in raw or "RECONCILED" in raw or "OK" in raw:
        return {"status": "SYNCED", "detail": raw}
    return {"status": raw or "NOT REPORTED", "detail": raw}


def _stop_state(live: dict[str, Any]) -> dict[str, Any]:
    """Derive SL protection from real orders + positions (never assumed).

    Backend sessions arm a protective STOP order per filled position; the
    live snapshot carries the engine's orders, so protection is read from
    working STOP orders covering open positions — not from a second table.
    """
    positions = live.get("positions") or []
    orders = live.get("orders") or []
    open_positions = [p for p in positions if isinstance(p, dict) and p.get("status") == "OPEN"]
    if not open_positions:
        return {"status": "NONE", "detail": "no open positions"}
    working_stops = 0
    for order in orders:
        if not isinstance(order, dict):
            continue
        state = str(order.get("status", "") or "").upper()
        kind = str(order.get("type", "") or "").upper()
        if state in ("WORKING", "OPEN", "ACKNOWLEDGED", "SUBMITTED") and (
            "STOP" in kind or "SL" in kind
        ):
            working_stops += 1
    if working_stops >= len(open_positions):
        return {
            "status": "PROTECTED",
            "detail": f"{working_stops} working stop order(s) for "
            f"{len(open_positions)} open position(s)",
        }
    return {
        "status": "AT RISK",
        "detail": f"{len(open_positions)} open position(s), {working_stops} working stop order(s)",
    }


def build_snapshot(live: dict[str, Any]) -> dict[str, Any]:
    """Project one live-service snapshot into the remote contract.

    Sections: broker, market, order_stream, strategy, risk, capital,
    orders, positions, stops, reconciliation, system, blockers. Every
    section names its derivation in ``source``; genuinely unknown facts
    stay ``NOT REPORTED`` instead of being invented.
    """
    if not isinstance(live, dict):
        return {"error": "backend snapshot unavailable"}
    broker = live.get("broker") or {}
    websocket = live.get("websocket") or {}
    market_data = live.get("market_data") or {}
    risk_engine = live.get("risk_engine") or {}
    capital = live.get("capital") or {}
    recon = live.get("reconciliation") or {}
    execution = live.get("execution") or {}
    kill = live.get("kill") or {}

    broker_connected = bool(broker.get("connected", False))
    feed = str(live.get("feed", "none") or "none")
    if broker_connected and feed == "live":
        order_stream = {"status": "CONNECTED", "detail": "broker order stream"}
    elif broker_connected:
        order_stream = {"status": "CONNECTED", "detail": "broker connected (local tail)"}
    else:
        order_stream = {"status": "DISCONNECTED", "detail": "broker not connected"}

    strategy = _strategy_status(live)
    strategy["mode"] = str(live.get("mode", "PAPER") or "PAPER")
    strategy["source"] = "live session lifecycle + start blockers"

    sizing_status = str(risk_engine.get("sizing_status", "") or "").upper()
    risk_status = str(risk_engine.get("status", "") or "").upper()
    ready = sizing_status == "READY" or risk_status == "READY"
    risk = {
        "status": "READY" if ready else "NOT READY",
        "sizing_status": sizing_status or "NOT REPORTED",
        "sizing_reason": str(risk_engine.get("sizing_reason", "") or ""),
        "source": "risk engine sizing verdict",
    }
    capital_block = {
        "available": _num(capital.get("available_margin")),
        "effective": _num(risk_engine.get("effective_capital")),
        "max_risk": _num(risk_engine.get("max_allowed_risk")),
        "broker_capital": _num(risk_engine.get("broker_capital")),
        "capital_source": str(capital.get("source", "") or "NOT REPORTED"),
        "source": "broker-reported funds via risk engine (no fallback)",
    }
    snapshot = {
        "broker": {
            "status": "CONNECTED" if broker_connected else "DISCONNECTED",
            "name": str(broker.get("name", "") or ""),
            "reason": str(broker.get("reason", "") or ""),
            "source": "SYSTEM broker manager view",
        },
        "market": {
            "status": str(market_data.get("status", "") or "NOT REPORTED"),
            "feed": feed,
            "exchange": str(market_data.get("exchange", "") or ""),
            "timeframe": str(market_data.get("timeframe", "") or ""),
            "source": "market-data service + venue feed",
        },
        "order_stream": {**order_stream, "source": "broker connection + feed kind"},
        "strategy": strategy,
        "risk": risk,
        "capital": capital_block,
        "orders": scrub(live.get("orders", [])),
        "positions": scrub(live.get("positions", [])),
        "fills": scrub(live.get("fills", [])),
        "stops": {**_stop_state(live), "source": "engine orders covering open positions"},
        "reconciliation": {
            **_recon_status(live),
            "blocks_live": bool(recon.get("blocks_live", False)),
            "source": "session reconciliation verdict",
        },
        "system": {
            "execution_ready": bool(execution.get("ready", False)),
            "execution_reason": str(execution.get("reason", "") or ""),
            "kill_halted": bool(kill.get("halted", False)),
            "websocket": str(websocket.get("status", "") or "NOT REPORTED"),
            "source": "execution readiness + kill switch + transport",
        },
        "blockers": [str(item) for item in (live.get("start_blockers", []) or [])],
        "as_of": str(live.get("as_of", "") or ""),
    }
    return scrub(snapshot)


#: Remote event names (fixed vocabulary for EXE/APK clients).
EVENT_NAMES = (
    "MARKET_TICK",
    "SIGNAL_GENERATED",
    "RISK_VALIDATED",
    "RISK_BLOCKED",
    "ORDER_SENT",
    "BROKER_ACK",
    "ORDER_WORKING",
    "PARTIAL_FILL",
    "FILL",
    "POSITION_UPDATE",
    "SL_UPDATE",
    "REJECTION",
    "CANCELLATION",
    "RECONCILIATION",
    "SYSTEM",
)

_EVENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SIGNAL_GENERATED", ("SIGNAL GENERATED", "SIGNAL")),
    ("RISK_VALIDATED", ("RISK VALIDATED",)),
    ("RISK_BLOCKED", ("RISK BLOCKED", "RISK DENIED", "DENIED")),
    ("BROKER_ACK", ("ACKNOWLEDGED BY BROKER",)),
    ("ORDER_SENT", ("ORDER SUBMITTED", "ORDER SENT")),
    ("ORDER_WORKING", ("WORKING",)),
    ("FILL", ("FILLED", "FILL")),
    ("REJECTION", ("REJECTED", "REJECT")),
    ("CANCELLATION", ("CANCEL",)),
    ("RECONCILIATION", ("RECONCIL", "SYNC")),
    ("POSITION_UPDATE", ("POSITION",)),
    ("SL_UPDATE", (" SL", "SL ", "STOP LOSS", "STOP_LOSS", "STOP PROTECTION")),
    ("MARKET_TICK", ("TICK", "QUOTE", "MARKET DATA", "FEED")),
)


def classify_event(entry: dict[str, Any]) -> str:
    """Map one backend activity entry to the fixed remote event vocabulary.

    Uses the backend's own category first (the service already buckets
    every journal kind), then the entry text. Unknown entries land in
    ``SYSTEM`` — the vocabulary never grows by invention.
    """
    if not isinstance(entry, dict):
        return "SYSTEM"
    text = str(entry.get("event", "") or "").upper()
    category = str(entry.get("category", "") or "").upper()
    if "PARTIAL" in text and "FILL" in text:
        return "PARTIAL_FILL"
    for name, keywords in _EVENT_KEYWORDS:
        if any(word in text for word in keywords):
            return name
    if category == "ORDERS":
        return "ORDER_WORKING"
    if category == "RISK":
        return "RISK_BLOCKED"
    if category == "STRATEGY":
        return "SIGNAL_GENERATED"
    if category == "MARKET DATA":
        return "MARKET_TICK"
    if category == "BROKER":
        return "SYSTEM"
    return "SYSTEM"


def project_event(entry: dict[str, Any]) -> dict[str, Any]:
    """Project one activity entry into the remote ``event`` payload."""
    if not isinstance(entry, dict):
        entry = {}
    return scrub(
        {
            "name": classify_event(entry),
            "timestamp": str(entry.get("timestamp", "") or ""),
            "strategy": str(entry.get("strategy", "") or ""),
            "symbol": str(entry.get("symbol", "") or ""),
            "detail": str(entry.get("event", "") or ""),
            "status": str(entry.get("status", "") or ""),
        }
    )
