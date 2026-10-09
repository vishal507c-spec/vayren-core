"""EligibilityEngine — unified readiness over subsystem truth (Phase 6).

ONE authoritative answer to "can this instrument generate a NEW order
right now?" — composed from existing subsystem truth, never recomputed:

- Phase 2 canonical registry → instrument existence/lifecycle
- Phase 1 universe store → strategy membership
- Phase 3 provider mappings → provider/broker mapping readiness
- Phase 4 MarketDataRouter → market-data readiness (no freshness math here)
- Phase 5 ExecutionRouter → broker health + order ownership
- Existing risk truth (``_risk_status``/``_risk_reason``) → risk readiness
- Existing session/safety truth (``validate()``, ``_live_blockers()``,
  status, arming) → session + safety gates

Safety rules (structural, test-proven):
- FAIL-SAFE DEFAULT: unknown/missing critical state is BLOCKED, never
  READY — missing information is never permission to trade.
- NO ORDER BYPASS: the engine never calls place_order (or any adapter);
  it returns verdicts. The existing execution pipeline stays the sole
  submission path (§18).
- DETERMINISTIC: gates run in the fixed order SYSTEM → SESSION →
  STRATEGY → UNIVERSE → INSTRUMENT → MARKET DATA → RISK → EXECUTION →
  SAFETY → FINAL; no dict iteration decides verdict ordering.
- STABLE TRANSITIONS: events fire on level change only — a blocker
  already present never produces a transient false READY.
- CONCURRENCY: verdicts are immutable snapshots; the engine guards only
  its own transition journal with a lock, never the subsystems.

Gate order is documented by ``GATE_ORDER`` — keep it in sync with §11 of
the phase spec.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from strategy.instrument_registry import get_instrument_registry
from strategy.models.eligibility import (
    BLOCKED,
    BLOCKING,
    BROKER_MAPPING_CONFLICT,
    BROKER_MAPPING_DISABLED,
    BROKER_MAPPING_MISSING,
    BROKER_NOT_READY,
    BROKER_UNAVAILABLE,
    DEGRADED,
    DIAGNOSTIC,
    DISABLED,
    DUPLICATE_ORDER,
    ERROR,
    EV_INSTRUMENT_BLOCKED,
    EV_INSTRUMENT_READY,
    EV_INSTRUMENT_RECOVERED,
    EV_INSTRUMENT_STALE,
    EV_TRADING_ALLOWED,
    EV_TRADING_BLOCKED,
    EXECUTION_BLOCKED,
    EXECUTION_UNAVAILABLE,
    FAIL_FAST,
    INSTRUMENT_INACTIVE,
    INSTRUMENT_NOT_FOUND,
    LIVE_NOT_ARMED,
    MARKET_DATA_STALE,
    MARKET_DATA_UNAVAILABLE,
    MARKET_DATA_WAITING,
    PROVIDER_MAPPING_MISSING,
    READY,
    RECONCILIATION_UNHEALTHY,
    RISK_BLOCKED,
    RISK_DENIED,
    RISK_NOT_READY,
    SAFETY_BLOCKER,
    SESSION_NOT_RUNNING,
    STALE,
    STRATEGY_DISABLED,
    STRATEGY_MEMBERSHIP_MISSING,
    STRATEGY_NOT_CONFIGURED,
    STRATEGY_NOT_SELECTED,
    SYSTEM_DEGRADED,
    SYSTEM_MODE_INVALID,
    SYSTEM_NOT_READY,
    TRADING_ALLOWED,
    TRADING_BLOCKED,
    UNAVAILABLE,
    UNIVERSE_EMPTY,
    WAITING,
    WARNING,
    EligibilityCheck,
    EligibilityVerdict,
    _utcnow_iso,
)
from strategy.provider_mapping import ProviderMappingRegistry

log = logging.getLogger(__name__)

#: Fixed deterministic gate order (§11). FINAL is the aggregate marker.
GATE_ORDER: tuple[str, ...] = (
    "SYSTEM",
    "SESSION",
    "STRATEGY",
    "UNIVERSE",
    "INSTRUMENT",
    "MARKET_DATA",
    "RISK",
    "EXECUTION",
    "SAFETY",
    "FINAL",
)

#: Mode names the live service already uses.
_MODES = ("PAPER", "SANDBOX", "LIVE")

#: Risk states that block new orders.
_RISK_BLOCKING = {"NOT READY", "HALTED", "BLOCKED"}

#: Router readiness → eligibility code (mapped, never recomputed).
_READINESS_MAP = {
    "READY": (READY, ""),
    "WAITING_FOR_DATA": (MARKET_DATA_WAITING, "waiting for first data"),
    "STALE": (MARKET_DATA_STALE, "data stale beyond freshness threshold"),
    "UNAVAILABLE": (MARKET_DATA_UNAVAILABLE, "no provider can deliver data"),
    "ERROR": (MARKET_DATA_UNAVAILABLE, "market-data feed error"),
}

#: Broker health states (Phase 5 vocabulary).
_HEALTHY = "HEALTHY"
_DEGRADED = "DEGRADED"
_ERROR = "ERROR"
_DISCONNECTED = "DISCONNECTED"
_DISABLED = "DISABLED"

#: Blocking-code → instrument level (specific before generic).
_LEVEL_BY_CODE = {
    MARKET_DATA_WAITING: WAITING,
    MARKET_DATA_STALE: STALE,
    MARKET_DATA_UNAVAILABLE: UNAVAILABLE,
    PROVIDER_MAPPING_MISSING: BLOCKED,
    RISK_NOT_READY: RISK_BLOCKED,
    RISK_DENIED: RISK_BLOCKED,
    BROKER_MAPPING_MISSING: EXECUTION_BLOCKED,
    BROKER_MAPPING_DISABLED: EXECUTION_BLOCKED,
    BROKER_MAPPING_CONFLICT: EXECUTION_BLOCKED,
    BROKER_NOT_READY: EXECUTION_BLOCKED,
    BROKER_UNAVAILABLE: EXECUTION_BLOCKED,
    EXECUTION_UNAVAILABLE: EXECUTION_BLOCKED,
    INSTRUMENT_NOT_FOUND: DISABLED,
    INSTRUMENT_INACTIVE: DISABLED,
}

#: Owned-order statuses that still count as "an order exists" (§7.7).
_OPEN_ORDER_STATUSES = frozenset(
    {"SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_PENDING", "MODIFY_PENDING"}
)


class EligibilityEngineError(RuntimeError):
    """Refused engine wiring (bad dependencies)."""


class EligibilityEngine:
    """Unified eligibility + readiness over Phase 1–5 subsystem truth."""

    def __init__(
        self,
        mappings: ProviderMappingRegistry,
        live_service: Any,
        market_router: Any | None = None,
        execution_router: Any | None = None,
        armed_provider: Callable[[], bool] | None = None,
        reconciliation_provider: Callable[[], tuple[bool, str]] | None = None,
        safety_provider: Callable[[str], tuple[tuple[str, str, str], ...]] | None = None,
        event_cap: int = 500,
    ) -> None:
        self._mappings = mappings
        self._live = live_service
        self._market = market_router
        self._execution = execution_router
        self._armed = armed_provider or (
            lambda: bool(getattr(live_service, "_confirmed_live_at", ""))
        )
        self._reconciliation = reconciliation_provider or self._default_reconciliation
        self._safety = safety_provider or self._default_safety
        self._lock = threading.RLock()
        self._last_verdicts: dict[tuple[str, str], EligibilityVerdict] = {}
        self._last_system_final = ""
        self._events: deque[dict[str, str]] = deque(maxlen=max(1, event_cap))

    # ── public API (§23 binding surface) ────────────────────────────

    def evaluate(
        self,
        instrument_id: str,
        strategy_id: str = "",
        trading_mode: str = "LIVE",
        mode: str = DIAGNOSTIC,
    ) -> EligibilityVerdict:
        """One authoritative verdict for instrument + strategy.

        DIAGNOSTIC evaluates every gate (all blockers + warnings returned);
        FAIL_FAST stops at the first BLOCKING check. Unknown instrument or
        unknown strategy are blockers, never silent passes.
        """
        mode = DIAGNOSTIC if mode not in (FAIL_FAST, DIAGNOSTIC) else mode
        trading_mode = str(trading_mode or "LIVE").strip().upper()
        checks: list[EligibilityCheck] = []
        blocking: list[str] = []
        warnings: list[str] = []
        for gate in GATE_ORDER:
            if gate == "FINAL":
                break
            for check in self._run_gate(gate, instrument_id, strategy_id, trading_mode):
                checks.append(check)
                if check.severity == BLOCKING:
                    blocking.append(check.code)
                    if mode == FAIL_FAST:
                        break
                elif check.severity == WARNING and check.status != READY:
                    warnings.append(check.code)
            if mode == FAIL_FAST and blocking:
                break
        verdict = self._assemble(instrument_id, strategy_id, checks, blocking, warnings)
        self._emit_transitions(verdict, trading_mode)
        return verdict

    def system_readiness(self) -> dict[str, str]:
        """Compact backend summary (§14) — aggregates subsystem truth."""
        checks = list(self._run_gate("SYSTEM", "", "", "LIVE"))
        system_blockers = [c.code for c in checks if c.severity == BLOCKING]
        system_warnings = [c.code for c in checks if c.severity == WARNING]
        risk = "READY" if self._risk_state() == "READY" else "NOT READY"
        market = self._subsystem_summary(self._market, "MARKET DATA")
        execution = self._subsystem_summary(self._execution, "EXECUTION")
        strategy = (
            "READY"
            if not any(
                c.severity == BLOCKING
                for c in self._run_gate("STRATEGY", "", self._selected_strategy(), "LIVE")
            )
            else "BLOCKED"
        )
        final = (
            TRADING_ALLOWED
            if not system_blockers
            and risk == "READY"
            and market == "READY"
            and execution == "READY"
            and strategy == "READY"
            else TRADING_BLOCKED
        )
        if final == TRADING_BLOCKED and not system_blockers:
            reason = "subsystem not ready"
        elif system_blockers:
            reason = system_blockers[0]
        else:
            reason = "all subsystems ready"
        self._emit_system_transition(final)
        return {
            "system": SYSTEM_NOT_READY
            if system_blockers
            else (SYSTEM_DEGRADED if system_warnings else "READY"),
            "strategy": strategy,
            "market_data": market,
            "execution": execution,
            "risk": risk,
            "final": final,
            "reason": reason,
        }

    def strategy_readiness(self, strategy_id: str = "") -> dict[str, Any]:
        """Per-strategy counts derived from canonical verdicts (§13)."""
        strategy_id = strategy_id or self._selected_strategy()
        symbols = self._universe_symbols(strategy_id)
        counts = {
            "TOTAL": 0,
            "READY": 0,
            "WAITING": 0,
            "BLOCKED": 0,
            "STALE": 0,
            "UNAVAILABLE": 0,
            "RISK_BLOCKED": 0,
            "EXECUTION_BLOCKED": 0,
        }
        for symbol in symbols:
            instrument_id = self._canonical_id_for(symbol)
            if not instrument_id:
                counts["BLOCKED"] += 1
                counts["TOTAL"] += 1
                continue
            verdict = self.evaluate(instrument_id, strategy_id, mode=DIAGNOSTIC)
            counts["TOTAL"] += 1
            counts[_SUMMARY_BUCKET[verdict.level]] += 1
        return counts

    def readiness_summary(self) -> dict[str, Any]:
        """System summary + per-strategy counts (§23 backend binding)."""
        return {
            "system": self.system_readiness(),
            "strategies": {
                name: self.strategy_readiness(name) for name in self._available_strategies()
            },
        }

    def last_verdicts(self) -> dict[str, dict[str, Any]]:
        """Frozen verdict snapshots (diagnostics parity with backend)."""
        with self._lock:
            return {
                f"{strategy_id}|{instrument_id}": {
                    "allowed": verdict.allowed,
                    "level": verdict.level,
                    "blocking_reasons": list(verdict.blocking_reasons),
                    "warnings": list(verdict.warnings),
                    "evaluated_at": verdict.evaluated_at,
                }
                for (strategy_id, instrument_id), verdict in self._last_verdicts.items()
            }

    def events(self) -> tuple[dict[str, str], ...]:
        """Bounded transition journal (debugging, not telemetry)."""
        with self._lock:
            return tuple(self._events)

    def diagnostics(self) -> dict[str, Any]:
        """Combined visibility: gate order, summary, events."""
        return {
            "gate_order": list(GATE_ORDER),
            "summary": self.readiness_summary(),
            "verdicts": self.last_verdicts(),
        }

    # ── gate implementations (fixed order, composable) ───────────────

    def _run_gate(
        self, gate: str, instrument_id: str, strategy_id: str, trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        handler = getattr(self, f"_gate_{gate.lower()}", None)
        if handler is None:  # pragma: no cover — GATE_ORDER drift
            raise EligibilityEngineError(f"unknown gate {gate!r}")
        return handler(instrument_id, strategy_id, trading_mode)

    def _gate_system(
        self, _instrument_id: str, _strategy_id: str, trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        checks: list[EligibilityCheck] = []
        if trading_mode not in _MODES:
            checks.append(
                EligibilityCheck(
                    SYSTEM_MODE_INVALID,
                    BLOCKED,
                    BLOCKING,
                    f"trading mode {trading_mode!r} is not configured",
                    "SYSTEM",
                )
            )
        healthy, reason = self._reconciliation()
        checks.append(
            EligibilityCheck(
                "" if healthy else RECONCILIATION_UNHEALTHY,
                READY if healthy else BLOCKED,
                WARNING if healthy else BLOCKING,
                reason or "reconciliation clean",
                "SYSTEM",
            )
        )
        return tuple(checks)

    def _gate_session(
        self, _instrument_id: str, _strategy_id: str, trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        status = str(getattr(self._live, "status", "") or "").strip().upper()
        if status == "RUNNING":
            return (EligibilityCheck("", READY, WARNING, "session running", "SESSION"),)
        if trading_mode == "LIVE":
            if status in ("HALTED", "ERROR"):
                return (
                    EligibilityCheck(
                        SESSION_NOT_RUNNING,
                        BLOCKED,
                        BLOCKING,
                        f"session {status.lower()} — not running",
                        "SESSION",
                    ),
                )
            if self._armed():
                return (
                    EligibilityCheck(
                        "", READY, WARNING, "live session armed — start to trade", "SESSION"
                    ),
                )
            return (
                EligibilityCheck(
                    LIVE_NOT_ARMED,
                    BLOCKED,
                    BLOCKING,
                    "live session not armed — arming is the consent ceremony",
                    "SESSION",
                ),
            )
        return (
            EligibilityCheck(
                SESSION_NOT_RUNNING,
                BLOCKED,
                BLOCKING,
                f"session {status.lower() or 'unknown'} — not running",
                "SESSION",
            ),
        )

    def _gate_strategy(
        self, _instrument_id: str, strategy_id: str, _trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        checks: list[EligibilityCheck] = []
        if not strategy_id:
            checks.append(
                EligibilityCheck(
                    STRATEGY_NOT_SELECTED,
                    BLOCKED,
                    BLOCKING,
                    "no strategy selected — select a strategy first",
                    "STRATEGY",
                )
            )
            return tuple(checks)
        available = self._available_strategies()
        if strategy_id not in available:
            checks.append(
                EligibilityCheck(
                    STRATEGY_NOT_CONFIGURED,
                    BLOCKED,
                    BLOCKING,
                    f"strategy {strategy_id!r} is not configured",
                    "STRATEGY",
                )
            )
            return tuple(checks)
        disabled = self._strategy_disabled(strategy_id)
        checks.append(
            EligibilityCheck(
                "" if not disabled else STRATEGY_DISABLED,
                READY if not disabled else BLOCKED,
                WARNING if not disabled else BLOCKING,
                "strategy enabled" if not disabled else "strategy disabled",
                "STRATEGY",
            )
        )
        if not self._universe_symbols(strategy_id):
            checks.append(
                EligibilityCheck(
                    UNIVERSE_EMPTY,
                    BLOCKED,
                    BLOCKING,
                    f"no symbols configured for {strategy_id}",
                    "STRATEGY",
                )
            )
        return tuple(checks)

    def _gate_universe(
        self, instrument_id: str, strategy_id: str, _trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        symbol = self._display_for(instrument_id)
        universe = set(self._universe_symbols(strategy_id))
        if symbol in universe:
            return (EligibilityCheck("", READY, WARNING, f"{symbol} in universe", "UNIVERSE"),)
        return (
            EligibilityCheck(
                STRATEGY_MEMBERSHIP_MISSING,
                BLOCKED,
                BLOCKING,
                f"{symbol} not configured for this strategy",
                "UNIVERSE",
            ),
        )

    def _gate_instrument(
        self, instrument_id: str, _strategy_id: str, _trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        instrument = get_instrument_registry().get(instrument_id)
        if instrument is None:
            return (
                EligibilityCheck(
                    INSTRUMENT_NOT_FOUND,
                    BLOCKED,
                    BLOCKING,
                    f"{instrument_id} not in canonical registry",
                    "INSTRUMENT",
                ),
            )
        if instrument.status != "ACTIVE":
            return (
                EligibilityCheck(
                    INSTRUMENT_INACTIVE,
                    BLOCKED,
                    BLOCKING,
                    f"{instrument_id} is {instrument.status.lower()}",
                    "INSTRUMENT",
                ),
            )
        return (EligibilityCheck("", READY, WARNING, "canonical instrument active", "INSTRUMENT"),)

    def _gate_market_data(
        self, instrument_id: str, _strategy_id: str, trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        # PAPER/SANDBOX do not require live data (existing mode semantics).
        if self._market is None:
            return (
                EligibilityCheck(
                    "" if trading_mode in ("PAPER", "SANDBOX") else MARKET_DATA_UNAVAILABLE,
                    READY if trading_mode in ("PAPER", "SANDBOX") else BLOCKED,
                    WARNING if trading_mode in ("PAPER", "SANDBOX") else BLOCKING,
                    "no market-data router attached"
                    if trading_mode in ("PAPER", "SANDBOX")
                    else "market-data router not attached",
                    "MARKET_DATA",
                ),
            )
        states = self._market.instrument_states()
        state = states.get(instrument_id)
        primary = self._router_primary(self._market)
        if primary:
            mapping = self._mappings.get_mapping(instrument_id, primary)
            if mapping.status != "FOUND" or mapping.provider_instrument is None:
                return (
                    EligibilityCheck(
                        PROVIDER_MAPPING_MISSING,
                        BLOCKED,
                        BLOCKING,
                        f"no provider mapping via {primary}",
                        "MARKET_DATA",
                    ),
                )
        if state is None:
            return (
                EligibilityCheck(
                    MARKET_DATA_UNAVAILABLE,
                    BLOCKED,
                    BLOCKING,
                    "instrument not subscribed — data unavailable",
                    "MARKET_DATA",
                ),
            )
        level, reason = _READINESS_MAP.get(
            str(state.get("readiness", "")).upper(),
            (MARKET_DATA_UNAVAILABLE, "unknown market-data state"),
        )
        return (
            EligibilityCheck(
                "" if level == READY else level,
                READY if level == READY else BLOCKED,
                WARNING if level == READY else BLOCKING,
                reason or "data ready",
                "MARKET_DATA",
            ),
        )

    def _gate_risk(
        self, _instrument_id: str, _strategy_id: str, _trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        state = str(getattr(self._live, "_risk_status", "") or "").strip().upper()
        reason = str(getattr(self._live, "_risk_reason", "") or "")
        if state == "READY":
            return (EligibilityCheck("", READY, WARNING, "risk engine ready", "RISK"),)
        if state in ("HALTED", "BLOCKED"):
            return (
                EligibilityCheck(
                    RISK_DENIED,
                    BLOCKED,
                    BLOCKING,
                    f"risk denied: {reason or state.lower()}",
                    "RISK",
                ),
            )
        # NOT READY and anything unreadable — fail-safe BLOCK (§17).
        return (
            EligibilityCheck(
                RISK_NOT_READY,
                BLOCKED,
                BLOCKING,
                f"risk not ready: {reason or state or 'state unknown'}",
                "RISK",
            ),
        )

    def _gate_execution(
        self, instrument_id: str, _strategy_id: str, trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        if self._execution is None:
            return (
                EligibilityCheck(
                    EXECUTION_UNAVAILABLE,
                    BLOCKED,
                    BLOCKING,
                    "execution router not attached",
                    "EXECUTION",
                ),
            )
        # Duplicate protection: an open owned order for this instrument
        # blocks a NEW order in every mode (existing protection, consumed
        # not reimplemented — paper duplicates are real duplicates).
        for owned in self._execution.owned_orders():
            if (
                owned.get("instrument_id") == instrument_id
                and str(owned.get("status", "")).upper() in _OPEN_ORDER_STATUSES
            ):
                return (
                    EligibilityCheck(
                        DUPLICATE_ORDER,
                        BLOCKED,
                        BLOCKING,
                        f"open order exists via {owned.get('broker', '')} — duplicate protection",
                        "EXECUTION",
                    ),
                )
        if trading_mode in ("PAPER", "SANDBOX"):
            # Paper/sandbox venues are local simulation: no provider mapping
            # exists or is required, and availability is the venue's own
            # health (checked per order by the session's existing gates).
            return (
                EligibilityCheck("", READY, WARNING, "paper venue needs no mapping", "EXECUTION"),
            )
        config = self._execution_config()
        health = self._execution.broker_health()
        # The primary broker's mapping must exist — a missing primary
        # mapping is a hard block (never silently route elsewhere).
        primary = config[0] if config else ""
        primary_mapping = self._mappings.get_mapping(instrument_id, primary)
        if primary_mapping.status == "DISABLED":
            return (
                EligibilityCheck(
                    BROKER_MAPPING_DISABLED,
                    BLOCKED,
                    BLOCKING,
                    "execution mapping disabled",
                    "EXECUTION",
                ),
            )
        if primary_mapping.status == "CONFLICT":
            return (
                EligibilityCheck(
                    BROKER_MAPPING_CONFLICT,
                    BLOCKED,
                    BLOCKING,
                    "execution mapping conflict",
                    "EXECUTION",
                ),
            )
        if primary_mapping.status != "FOUND" or primary_mapping.provider_instrument is None:
            return (
                EligibilityCheck(
                    BROKER_MAPPING_MISSING,
                    BLOCKED,
                    BLOCKING,
                    f"no execution mapping via {primary}",
                    "EXECUTION",
                ),
            )
        for broker in config:
            if broker not in self._execution_brokers():
                continue
            mapping = self._mappings.get_mapping(instrument_id, broker)
            if mapping.status != "FOUND" or mapping.provider_instrument is None:
                continue
            state = str(health.get(broker, {}).get("state", "")).upper()
            if state == _HEALTHY:
                fallback = config.index(broker) > 0
                return (
                    EligibilityCheck(
                        "EXECUTION_FALLBACK_ACTIVE" if fallback else "",
                        READY if not fallback else WARNING,
                        WARNING,
                        f"execution ready via {broker}" + (" (fallback)" if fallback else ""),
                        "EXECUTION",
                    ),
                )
        # No healthy eligible broker — report the primary's exact failure.
        primary = config[0] if config else ""
        if primary not in self._execution_brokers():
            return (
                EligibilityCheck(
                    BROKER_UNAVAILABLE,
                    BLOCKED,
                    BLOCKING,
                    f"no {primary} execution adapter",
                    "EXECUTION",
                ),
            )
        state = str(health.get(primary, {}).get("state", "")).upper()
        return (
            EligibilityCheck(
                BROKER_NOT_READY,
                BLOCKED,
                BLOCKING,
                f"{primary} not ready ({state.lower()})",
                "EXECUTION",
            ),
        )

    def _gate_safety(
        self, _instrument_id: str, _strategy_id: str, trading_mode: str
    ) -> tuple[EligibilityCheck, ...]:
        return tuple(
            EligibilityCheck(
                code, BLOCKED if severity == BLOCKING else WARNING, severity, message, "SAFETY"
            )
            for code, message, severity in self._safety(trading_mode)
        )

    # ── assembly ────────────────────────────────────────────────────

    def _assemble(
        self,
        instrument_id: str,
        strategy_id: str,
        checks: list[EligibilityCheck],
        blocking: list[str],
        warnings: list[str],
    ) -> EligibilityVerdict:
        if blocking:
            level = BLOCKED
            for code in blocking:
                if code in _LEVEL_BY_CODE:
                    level = _LEVEL_BY_CODE[code]
                    break
        elif warnings:
            level = DEGRADED
        else:
            level = READY
        return EligibilityVerdict(
            allowed=level in (READY, DEGRADED),
            level=level,
            instrument_id=instrument_id,
            strategy_id=strategy_id,
            checks=tuple(checks),
            blocking_reasons=tuple(blocking),
            warnings=tuple(warnings),
        )

    # ── transitions & events (§15, §16, §24) ───────────────────────

    def _emit_transitions(self, verdict: EligibilityVerdict, _trading_mode: str) -> None:
        key = (verdict.strategy_id, verdict.instrument_id)
        with self._lock:
            previous = self._last_verdicts.get(key)
            if previous is not None and previous.level != verdict.level:
                if verdict.level == STALE:
                    kind = EV_INSTRUMENT_STALE
                elif verdict.level == READY and previous.level in (STALE, UNAVAILABLE):
                    kind = EV_INSTRUMENT_RECOVERED
                elif verdict.level == READY:
                    kind = EV_INSTRUMENT_READY
                elif verdict.level == BLOCKED:
                    kind = EV_INSTRUMENT_BLOCKED
                else:
                    kind = ""
                if kind:
                    self._events.append(
                        {
                            "type": kind,
                            "strategy_id": verdict.strategy_id,
                            "instrument_id": verdict.instrument_id,
                            "reason": verdict.blocking_reasons[0]
                            if verdict.blocking_reasons
                            else "recovered",
                            "timestamp": _utcnow_iso(),
                        }
                    )
            self._last_verdicts[key] = verdict

    def _emit_system_transition(self, final: str) -> None:
        with self._lock:
            previous = self._last_system_final
            if previous is None or previous == final:
                self._last_system_final = final
                return
            kind = EV_TRADING_ALLOWED if final == TRADING_ALLOWED else EV_TRADING_BLOCKED
            self._last_system_final = final
        self._events.append(
            {
                "type": kind,
                "strategy_id": "",
                "instrument_id": "",
                "reason": "system readiness transition",
                "timestamp": _utcnow_iso(),
            }
        )

    # ── subsystem truth access (existing methods only) ──────────────

    def _selected_strategy(self) -> str:
        return str(getattr(getattr(self._live, "config", None), "strategy_name", "") or "")

    def _available_strategies(self) -> tuple[str, ...]:
        try:
            names = self._live.available_strategies()
        except Exception:  # noqa: BLE001
            names = ()
        return tuple(names) if names else ()

    def _universe_symbols(self, strategy_id: str) -> tuple[str, ...]:
        try:
            return tuple(self._live.available_symbols(strategy_id))
        except Exception:  # noqa: BLE001
            return ()

    def _canonical_id_for(self, symbol: str) -> str:
        result = get_instrument_registry().resolve(f"NSE:{symbol.split(':')[-1]}")
        return result.resolved.instrument_id if result.resolved is not None else ""

    def _display_for(self, instrument_id: str) -> str:
        instrument = get_instrument_registry().get(instrument_id)
        return instrument.display if instrument is not None else ""

    def _strategy_disabled(self, strategy_id: str) -> bool:
        try:
            from strategy.registry import get_strategy_registry

            reg = get_strategy_registry()
            if reg.contains(strategy_id):
                return not bool(reg.get(strategy_id).enabled)
        except Exception:  # noqa: BLE001
            pass
        return False

    def _risk_state(self) -> str:
        return str(getattr(self._live, "_risk_status", "") or "").strip().upper()

    def _router_primary(self, router: Any) -> str:
        config = getattr(router, "_config", None)
        ordered = getattr(config, "ordered_providers", None) or getattr(config, "secondaries", None)
        if ordered:
            return str(ordered[0]).upper()
        return ""

    def _execution_config(self) -> tuple[str, ...]:
        config = getattr(self._execution, "_config", None)
        return tuple(getattr(config, "ordered_brokers", ()) or ())

    def _execution_brokers(self) -> tuple[str, ...]:
        return tuple(getattr(self._execution, "_brokers", {}) or ())

    def _subsystem_summary(self, subsystem: Any, label: str) -> str:
        if subsystem is None:
            return "UNAVAILABLE"
        try:
            health = (
                subsystem.provider_health() if label == "MARKET DATA" else subsystem.broker_health()
            )
        except Exception:  # noqa: BLE001
            return "UNAVAILABLE"
        states = [str(entry.get("state", "")).upper() for entry in health.values()]
        if not states:
            return "UNAVAILABLE"
        if all(state == _HEALTHY for state in states):
            return "READY"
        if any(state in (_ERROR, _DISCONNECTED, _DISABLED) for state in states):
            return "DEGRADED"
        return "DEGRADED"

    def _default_reconciliation(self) -> tuple[bool, str]:
        try:
            snap = self._live.snapshot()
        except Exception:  # noqa: BLE001
            return False, "reconciliation state unavailable"
        recon = snap.get("reconciliation", {}) if isinstance(snap, dict) else {}
        if not isinstance(recon, dict):
            return True, "reconciliation clean"
        if recon.get("blocks_live"):
            return False, "reconciliation blocks live"
        return True, "reconciliation clean"

    def _default_safety(self, trading_mode: str) -> tuple[tuple[str, str, str], ...]:
        out: list[tuple[str, str, str]] = []
        try:
            for text in self._live.validate():
                out.append((_map_blocker(text), text, BLOCKING))
        except Exception:  # noqa: BLE001
            out.append((SAFETY_BLOCKER, "safety validation unavailable", BLOCKING))
        if trading_mode == "LIVE":
            try:
                for text in self._live._live_blockers():
                    lower = text.lower()
                    if "venue" in lower or "broker" in lower:
                        out.append((BROKER_UNAVAILABLE, text, BLOCKING))
                    else:
                        out.append((SAFETY_BLOCKER, text, BLOCKING))
            except Exception:  # noqa: BLE001
                pass
        return tuple(out)


def _map_blocker(text: str) -> str:
    """Map existing validate() text to stable codes (never free-form)."""
    lower = text.lower()
    if "no strategy selected" in lower:
        return STRATEGY_NOT_SELECTED
    if "no symbols selected" in lower:
        return UNIVERSE_EMPTY
    if "not in" in lower and "universe" in lower:
        return STRATEGY_MEMBERSHIP_MISSING
    if "no timeframe" in lower:
        return STRATEGY_NOT_CONFIGURED
    if "quantity must be positive" in lower:
        return INVALID_INTENT_CODE
    if "confirmation" in lower or "consent" in lower:
        return LIVE_NOT_ARMED
    return SAFETY_BLOCKER


INVALID_INTENT_CODE = "INVALID_INTENT"

_SUMMARY_BUCKET = {
    READY: "READY",
    DEGRADED: "READY",
    WAITING: "WAITING",
    STALE: "STALE",
    UNAVAILABLE: "UNAVAILABLE",
    RISK_BLOCKED: "RISK_BLOCKED",
    EXECUTION_BLOCKED: "EXECUTION_BLOCKED",
    DISABLED: "BLOCKED",
    ERROR: "BLOCKED",
    BLOCKED: "BLOCKED",
}

__all__ = [
    "EligibilityEngine",
    "EligibilityEngineError",
    "GATE_ORDER",
    "INVALID_INTENT_CODE",
]
