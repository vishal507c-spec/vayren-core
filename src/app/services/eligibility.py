"""Eligibility live-wiring — Phase 6 engine over live-service truth (Phase 7).

Composition only (no new trading semantics): small duck-typed views expose
the service's EXISTING facts in the shapes EligibilityEngine consumes, plus
a builder and the per-order gate closure the sessions call.

- ``_ServiceMarketView`` reads ``LiveTradingService._universe_quotes`` (the
  same rows the watchlist renders): AVAILABLE → READY, NO MARKET DATA →
  STALE, anything else → UNAVAILABLE. No freshness math is duplicated.
- ``_ServiceExecutionView`` reads ``_broker_view`` (SYSTEM broker truth)
  for health and aggregates open orders from the session engines for
  ownership (duplicate protection stays session-owned; the view only
  reports). No routing decisions are made here.
- ``build_eligibility_engine`` wires mappings (file-backed when present,
  empty otherwise — an empty mapping store blocks enforced orders, which
  is why enforcement is opt-in) with the views and the service's risk,
  session, arming, and reconciliation truth.
- ``order_gate_for`` returns the ``(intent, plan) -> reason | None``
  closure sessions call after every existing gate. Fail-closed on any
  evaluation problem; protective-stop placement never passes through it.

Deliberately NOT here: risk math, freshness math, broker routing,
reconciliation, order submission, any UI framework.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from strategy.eligibility_engine import EligibilityEngine
from strategy.instrument_registry import get_instrument_registry
from strategy.provider_mapping import ProviderMappingRegistry

log = logging.getLogger(__name__)

_MAPPINGS_RELATIVE = Path("instruments") / "provider_mappings.json"


def canonical_id_for(symbol: str) -> str:
    """Canonical id for a venue display symbol ("" when unresolvable)."""
    try:
        result = get_instrument_registry().resolve(symbol)
    except Exception:  # noqa: BLE001
        return ""
    resolved = result.resolved
    return resolved.instrument_id if resolved is not None else ""


class _ServiceMarketView:
    """Phase-4-shaped market truth over service watchlist quotes."""

    def __init__(self, service: Any, quotes: list[dict[str, Any]] | None = None) -> None:
        self._service = service
        self._quotes = quotes

    def instrument_states(self) -> dict[str, dict[str, Any]]:
        """Per-canonical readiness from existing quote rows (no recompute)."""
        rows = self._quotes
        if rows is None:
            try:
                symbols = tuple(getattr(self._service._config, "symbols", ()) or ())
                rows = self._service._universe_quotes(symbols)
            except Exception:  # noqa: BLE001
                rows = []
        states: dict[str, dict[str, Any]] = {}
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "") or "")
            canonical_id = canonical_id_for(symbol)
            if not canonical_id:
                continue
            status = str(row.get("status", "") or "").strip().upper()
            if status == "AVAILABLE":
                readiness, reason = "READY", "quote available"
            elif status == "NO MARKET DATA":
                readiness, reason = "STALE", "store quote unreadable"
            else:
                readiness, reason = "UNAVAILABLE", "no market data"
            states[canonical_id] = {
                "readiness": readiness,
                "reason": reason,
                "provider": "live",
                "failed_over": False,
            }
        return states

    def provider_health(self) -> dict[str, dict[str, str]]:
        """Single feed-health fact from the service feed kind."""
        kind = str(getattr(self._service, "_feed_kind", "") or "").lower()
        if kind == "live":
            return {"LIVE": {"state": "HEALTHY", "reason": "live feed"}}
        if kind == "local":
            return {"LOCAL": {"state": "HEALTHY", "reason": "local tail"}}
        return {}


class _ServiceExecutionView:
    """Phase-5-shaped execution truth over service broker/session facts."""

    def __init__(self, service: Any, brokers: tuple[str, ...] = ()) -> None:
        self._service = service
        self._config = type("ViewConfig", (), {"ordered_brokers": tuple(brokers)})()
        self._brokers = {name: object() for name in brokers}

    def broker_health(self) -> dict[str, dict[str, str]]:
        """Broker health from the SYSTEM broker view (read-only)."""
        try:
            view = self._service._broker_view()
        except Exception:  # noqa: BLE001
            return {}
        name = str(view.get("display", "") or view.get("id", "") or "").strip().upper()
        if not name:
            return {}
        if view.get("connected"):
            return {name: {"state": "HEALTHY", "reason": "connected"}}
        reason = str(view.get("reason", "") or "not connected")
        lowered = reason.lower()
        state = "DISCONNECTED" if "connect" in lowered else "ERROR"
        return {name: {"state": state, "reason": reason}}

    def owned_orders(self) -> tuple[dict[str, Any], ...]:
        """Open orders aggregated from session engines (existing truth)."""
        rows: list[dict[str, Any]] = []
        try:
            sessions = self._service._sessions
        except Exception:  # noqa: BLE001
            return ()
        for session in (sessions or {}).values():
            try:
                open_orders = session.engine.open_orders()
            except Exception:  # noqa: BLE001
                continue
            for item in open_orders or ():
                symbol = str(getattr(item, "symbol", "") or "")
                canonical_id = canonical_id_for(symbol)
                if not canonical_id:
                    continue
                rows.append(
                    {
                        "client_order_id": str(getattr(item, "client_order_id", "")),
                        "instrument_id": canonical_id,
                        "strategy_id": "",
                        "broker": "",
                        "broker_order_id": str(getattr(item, "broker_order_id", "") or ""),
                        "status": str(
                            getattr(getattr(item, "state", ""), "value", "")
                            or getattr(item, "state", "")
                        ),
                    }
                )
        return tuple(rows)


def _mappings_for(service: Any) -> ProviderMappingRegistry:
    """Mapping registry for the service data dir (cached, mtime-checked)."""
    data_dir = Path(getattr(service, "_data_dir", "."))
    path = data_dir / _MAPPINGS_RELATIVE
    cached = getattr(service, "_eligibility_mappings", None)
    cached_mtime = getattr(service, "_eligibility_mappings_mtime", None)
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None
    if cached is None or mtime != cached_mtime:
        cached = ProviderMappingRegistry(path)
        service._eligibility_mappings = cached
        service._eligibility_mappings_mtime = mtime
    return cached


def build_eligibility_engine(
    service: Any,
    quotes: list[dict[str, Any]] | None = None,
    recon_blocks: bool | None = None,
    recon_reason: str = "",
) -> EligibilityEngine:
    """Build the engine over live-service truth (read-only composition).

    ``quotes``/``recon_blocks`` let snapshot callers pass precomputed
    facts (no double market reads). Otherwise the views read the service
    directly (sessions, never a nested snapshot call).
    """
    broker_name = ""
    try:
        broker_name = str(service.broker_name() or "").strip().upper()
    except Exception:  # noqa: BLE001
        broker_name = ""
    brokers = (broker_name,) if broker_name and broker_name != "PAPER" else ()
    market = _ServiceMarketView(service, quotes)
    execution = _ServiceExecutionView(service, brokers)

    def _armed() -> bool:
        return bool(getattr(service, "armed", False))

    if recon_blocks is None:

        def _reconciliation() -> tuple[bool, str]:
            # Direct session truth (never via snapshot(): the gate runs
            # inside the order path where a snapshot would recurse).
            try:
                sessions = service._sessions or {}
            except Exception:  # noqa: BLE001
                return False, "reconciliation state unavailable"
            for session in sessions.values():
                try:
                    if session.reconciliation.blocks_live:
                        return False, "reconciliation blocks live"
                except Exception:  # noqa: BLE001
                    continue
            return True, "reconciliation clean"
    else:

        def _reconciliation() -> tuple[bool, str]:
            if recon_blocks:
                return False, recon_reason or "reconciliation blocks live"
            return True, "reconciliation clean"

    return EligibilityEngine(
        _mappings_for(service),
        service,
        market_router=market,
        execution_router=execution,
        armed_provider=_armed,
        reconciliation_provider=_reconciliation,
    )


def order_gate_for(service: Any) -> Any:
    """Per-order gate closure for sessions: ``(intent, plan) -> reason | None``.

    None allows; any returned string blocks with that reason. Fail-closed:
    unknown strategy/instrument, evaluation faults, and invalid verdicts
    all block — never silently allow.
    """

    def _gate(_intent: Any, plan: Any) -> str | None:
        try:
            strategy_name = str(getattr(service._config, "strategy_name", "") or "")
            if not strategy_name:
                return "no strategy selected — eligibility cannot identify owner"
            symbol = str(getattr(plan, "symbol", "") or "")
            canonical_id = canonical_id_for(symbol)
            if not canonical_id:
                return f"cannot resolve {symbol!r} to a canonical instrument"
            mode = str(getattr(service._config, "mode", "") or "PAPER").strip().upper()
            verdict = build_eligibility_engine(service).evaluate(
                canonical_id, strategy_name, trading_mode=mode
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("eligibility evaluation failed: %s", exc)
            return f"eligibility evaluation failed: {exc}"
        if verdict.allowed:
            return None
        reasons = "; ".join(verdict.blocking_reasons) or "blocked"
        return f"eligibility blocked: {reasons}"

    return _gate


__all__ = ["build_eligibility_engine", "canonical_id_for", "order_gate_for"]
