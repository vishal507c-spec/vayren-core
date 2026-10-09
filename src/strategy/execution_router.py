"""ExecutionRouter — broker-independent safe order routing (Phase 5).

Routes canonical :class:`OrderIntent` to venue adapters WITHOUT ever
letting strategy/risk see broker ids, SDKs, or payloads. Safety is
structural, not advisory:

- Risk gate first: ``risk_approved=False`` refuses before any broker,
  adapter, or mapping is touched (adapters prove it in tests via call
  logs). Quantity/price pass through EXACTLY (never resized here).
- Idempotency: a ``client_order_id`` routes exactly once — repeats
  return the stored result, never a second submission.
- Timeout discipline (mirrors the engine ``_submit`` rule, one safe
  deviation documented below): transport-uncertain failures become
  UNKNOWN with reconciliation guidance and NEVER retry elsewhere;
  only definitive non-acceptance may try the secondary.
- Ownership: cancel/modify/query always target the ORIGINAL owning
  broker (never failed over); a gone owner yields an explicit
  attention state, never a pretend reroute.
- Modes: PAPER/SANDBOX route only to their named venues (live brokers
  are never a fallback for paper); LIVE follows primary→secondary.

Deviation note: the engine maps every non-network error to REJECTED,
including DUPLICATE_ORDER. The router maps DUPLICATE_ORDER (and any
error without a definitive code) to UNKNOWN instead — a duplicate MAY
exist venue-side, so blind secondary submission could double a live
order. Refusing to guess is the safer behavior; reconciliation (which
already owns restart truth) resolves it.

Feeds/adapters are duck-typed and INJECTED (the TradingFace shape:
place_order/cancel_order/modify_order/health over plain data). The
router never constructs transports, never touches credentials, never
imports broker/execution/risk modules. Real adapters live in
``broker/providers/*`` and ``execution/broker/*``; tests use fakes.

Deliberately NOT here (future phases): smart routing, splitting,
slicing/TWAP/VWAP, execution optimization, risk/sizing redesign,
dynamic universes, explainability.
"""

from __future__ import annotations

import datetime
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from strategy.instrument_registry import get_instrument_registry
from strategy.models.execution import (
    ACKNOWLEDGED,
    ATTENTION_REQUIRED,
    BROKER_MAPPING_CONFLICT,
    BROKER_MAPPING_DISABLED,
    BROKER_MAPPING_MISSING,
    BROKER_UNAVAILABLE,
    CANCELLED,
    DUPLICATE_INTENT,
    FILLED,
    INSTRUMENT_NOT_FOUND,
    INVALID_INTENT,
    MODIFIED,
    NO_READY_BROKER,
    PARTIALLY_FILLED,
    REJECTED,
    REJECTED_SAFE,
    RISK_APPROVAL_MISSING,
    ROUTED_PRIMARY,
    ROUTED_SECONDARY,
    SUBMITTED,
    UNKNOWN,
    UNKNOWN_CLIENT_ORDER,
    NormalizedOrderResult,
    OrderIntent,
    RouterExecutionConfig,
)
from strategy.models.execution import BROKER_NOT_READY as REASON_NOT_READY
from strategy.models.market_data import DISABLED, DISCONNECTED, ERROR
from strategy.provider_mapping import ProviderMappingRegistry

log = logging.getLogger(__name__)

#: Broker error codes that mean "definitely NOT accepted" (secondary may
#: be tried). Everything else — transport uncertainty, unknown codes,
#: missing codes, OS-level failures — is UNKNOWN (never retried blind).
#: Mirrors the engine `_submit` rule, plus DUPLICATE_ORDER → UNKNOWN
#: (a duplicate may exist venue-side; see module docstring).
DEFINITIVE_CODES = frozenset(
    {
        "INVALID_SYMBOL",
        "INVALID_REQUEST",
        "AUTHENTICATION_FAILED",
        "CREDENTIALS_NOT_READY",
        "CAPABILITY_UNSUPPORTED",
        "RATE_LIMITED",
        "NOT_CONNECTED",
        "DISCONNECTED",
        "PROVIDER_UNAVAILABLE",
        "NOT_REGISTERED",
    }
)

#: Raw venue statuses → OrderState values (mirrors the FYERS numeric map
#: in session_adapter plus kite-style strings; unknown stays UNKNOWN).
_STATUS_TABLE = (
    ({"FILLED", "COMPLETE", "TRADED", "DONE"}, FILLED),
    ({"REJECTED", "REJECT"}, REJECTED),
    ({"CANCELLED", "CANCEL"}, CANCELLED),
    ({"MODIFIED", "MODIFY"}, MODIFIED),
    ({"PARTIALLY_FILLED", "PARTIAL", "PART"}, PARTIALLY_FILLED),
    (
        {"ACKNOWLEDGED", "ACK", "OPEN", "PENDING", "TRIGGER PENDING", "WORKING", "PLACED"},
        ACKNOWLEDGED,
    ),
    ({"SUBMITTED", "SENT", "NEW"}, SUBMITTED),
)
_FYERS_NUMERIC_STATUS = {1: CANCELLED, 5: REJECTED, 6: CANCELLED}

#: Structured routing event names (bounded journal + logging).
BROKER_CONNECTED = "BROKER_CONNECTED"
BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
BROKER_READY = "BROKER_READY"
BROKER_NOT_READY = "BROKER_NOT_READY"
ORDER_ROUTING_STARTED = "ORDER_ROUTING_STARTED"
ORDER_ROUTED_PRIMARY = "ORDER_ROUTED_PRIMARY"
ORDER_ROUTED_SECONDARY = "ORDER_ROUTED_SECONDARY"
ORDER_SUBMITTED = "ORDER_SUBMITTED"
ORDER_ACCEPTED = "ORDER_ACCEPTED"
ORDER_REJECTED = "ORDER_REJECTED"
ORDER_STATUS_UNKNOWN = "ORDER_STATUS_UNKNOWN"
ORDER_CANCEL_REQUESTED = "ORDER_CANCEL_REQUESTED"
ORDER_MODIFY_REQUESTED = "ORDER_MODIFY_REQUESTED"


@runtime_checkable
class BrokerExecutionAdapter(Protocol):
    """Structural execution-adapter contract (TradingFace shape, duck-typed).

    ``place_order`` takes a plan-shaped object (``symbol/side/quantity/
    order_type/limit_price/stop_price/time_in_force`` attributes) plus the
    VAYREN ``client_order_id`` and returns the venue order id. Query is
    optional (``query_order`` preferred, ``stream_events`` scan fallback).
    """

    def place_order(self, plan: Any, client_order_id: str) -> str: ...
    def cancel_order(self, broker_order_id: str) -> bool: ...
    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool: ...
    def health(self) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class _VenuePlan:
    """Adapter-facing plan shim: venue symbol + EXACT intent economics."""

    symbol: str
    side: str
    quantity: float
    order_type: str
    limit_price: float | None
    stop_price: float | None
    time_in_force: str


@dataclass
class _OwnedOrder:
    """Router-side ownership record (in-memory; reconciliation owns restarts)."""

    client_order_id: str
    instrument_id: str
    strategy_id: str
    broker: str
    broker_order_id: str
    submitted_quantity: float
    status: str
    routing: str
    filled_quantity: float = 0.0
    average_price: float | None = None


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class ExecutionRouterError(RuntimeError):
    """Refused router wiring (bad broker, bad config)."""


class _RefusedError(Exception):
    """Internal marker for router-side refusals masquerading as errors."""


class ExecutionRouter:
    """Safe broker routing for canonical order intents (Phase 5)."""

    def __init__(
        self,
        mappings: ProviderMappingRegistry,
        config: RouterExecutionConfig | None = None,
        brokers: dict[str, Any] | None = None,
        event_cap: int = 500,
    ) -> None:
        self._mappings = mappings
        self._config = config or RouterExecutionConfig()
        self._brokers: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._owned: dict[str, _OwnedOrder] = {}
        self._broker_error: dict[str, str] = {}
        self._broker_state: dict[str, str] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, event_cap))
        for name, adapter in (brokers or {}).items():
            self.add_broker(name, adapter)

    # ── wiring ──────────────────────────────────────────────────────

    def add_broker(self, name: str, adapter: Any) -> None:
        """Attach one named adapter (structural contract enforced)."""
        broker = str(name or "").strip().upper()
        if not broker:
            raise ExecutionRouterError("broker name must be non-empty")
        if not isinstance(adapter, BrokerExecutionAdapter):
            raise ExecutionRouterError(f"adapter for {broker!r} violates the execution contract")
        with self._lock:
            self._brokers[broker] = adapter
            self._broker_error.pop(broker, None)
        healthy, reason = self._probe(broker)
        self._record(BROKER_CONNECTED if healthy else BROKER_DISCONNECTED, "", broker, reason)

    # ── routing ─────────────────────────────────────────────────────

    def route(self, intent: OrderIntent) -> NormalizedOrderResult:
        """Route one canonical intent → normalized result (fail-closed)."""
        self._record(ORDER_ROUTING_STARTED, intent.instrument_id, "", intent.client_order_id)
        refusal = intent.check()
        if refusal is not None:
            return self._refuse(intent, "", refusal)
        if not intent.risk_approved:
            return self._refuse(intent, "", RISK_APPROVAL_MISSING)
        with self._lock:
            if intent.client_order_id in self._owned:
                owned = self._owned[intent.client_order_id]
                return self._result_from_owned(owned, owned.routing, DUPLICATE_INTENT)
        if get_instrument_registry().get(intent.instrument_id) is None:
            return self._refuse(intent, "", INSTRUMENT_NOT_FOUND)
        mode = str(intent.mode or "").strip().upper()
        if mode in ("PAPER", "SANDBOX"):
            return self._route_venue(intent, mode)
        return self._route_live(intent)

    def cancel_order(self, client_order_id: str) -> NormalizedOrderResult:
        """Cancel via the OWNING broker only (never failed over)."""
        self._record(ORDER_CANCEL_REQUESTED, "", "", client_order_id)
        return self._lifecycle(client_order_id, "cancel")

    def modify_order(
        self,
        client_order_id: str,
        quantity: float | None = None,
        price: float | None = None,
    ) -> NormalizedOrderResult:
        """Modify via the OWNING broker only (values validated first)."""
        self._record(ORDER_MODIFY_REQUESTED, "", "", client_order_id)
        if quantity is not None and not _positive_number(quantity):
            return self._refuse_owned(client_order_id, f"{INVALID_INTENT}: quantity invalid")
        if price is not None and not _positive_number(price):
            return self._refuse_owned(client_order_id, f"{INVALID_INTENT}: price invalid")
        if quantity is None and price is None:
            return self._refuse_owned(client_order_id, f"{INVALID_INTENT}: nothing to modify")
        return self._lifecycle(client_order_id, "modify", quantity=quantity, price=price)

    def query_order(self, client_order_id: str) -> NormalizedOrderResult:
        """Query via the OWNING broker; raw status normalized (never guessed)."""
        owned = self._owned.get(client_order_id)
        if owned is None:
            return self._unknown_order_result(client_order_id)
        adapter = self._brokers.get(owned.broker)
        if adapter is None:
            return self._attention(owned, f"owning broker {owned.broker!r} unavailable")
        healthy, reason = self._probe(owned.broker)
        if not healthy:
            return self._attention(owned, f"owning broker not ready: {reason}")
        raw = self._query_adapter(adapter, owned.broker_order_id)
        if raw is None:
            return self._attention(owned, "broker cannot report status — reconcile")
        status = _normalize_status(raw.get("status") if isinstance(raw, dict) else raw)
        with self._lock:
            owned.status = status
            if isinstance(raw, dict):
                filled = _safe_float(raw.get("filled_qty", raw.get("filled_quantity")))
                if filled is not None:
                    owned.filled_quantity = filled
                average = _safe_float(raw.get("average_price", raw.get("avg_price")))
                if average is not None:
                    owned.average_price = average
        return self._result_from_owned(owned, owned.routing, "")

    # ── diagnostics ─────────────────────────────────────────────────

    def broker_health(self, broker: str | None = None) -> dict[str, dict[str, str]]:
        """Broker health snapshot (HEALTHY/DISCONNECTED/ERROR/DISABLED)."""
        with self._lock:
            names = (
                [broker.strip().upper()]
                if broker is not None
                else sorted(set(self._brokers) | {self._config.primary, self._config.secondary})
            )
            return {name: self._broker_health(name) for name in names}

    def owned_orders(self) -> tuple[dict[str, Any], ...]:
        """Ownership table snapshot (client id → broker + venue id)."""
        with self._lock:
            return tuple(
                {
                    "client_order_id": owned.client_order_id,
                    "instrument_id": owned.instrument_id,
                    "strategy_id": owned.strategy_id,
                    "broker": owned.broker,
                    "broker_order_id": owned.broker_order_id,
                    "status": owned.status,
                }
                for owned in sorted(self._owned.values(), key=lambda o: o.client_order_id)
            )

    def events(self) -> tuple[dict[str, Any], ...]:
        """Bounded structured routing journal (debugging, not telemetry)."""
        with self._lock:
            return tuple(self._events)

    def diagnostics(self) -> dict[str, Any]:
        """Combined routing visibility (brokers + owned orders + config)."""
        return {
            "primary": self._config.primary,
            "secondary": self._config.secondary,
            "brokers": self.broker_health(),
            "owned_orders": len(self.owned_orders()),
        }

    # ── internals ───────────────────────────────────────────────────

    def _record(self, kind: str, instrument_id: str, broker: str, reason: str) -> None:
        self._events.append(
            {
                "type": kind,
                "instrument": instrument_id,
                "broker": broker,
                "reason": reason,
                "timestamp": _utcnow_iso(),
            }
        )

    def _probe(self, broker: str) -> tuple[bool, str]:
        adapter = self._brokers.get(broker)
        if adapter is None:
            return False, f"no {broker} adapter attached"
        if self._broker_error.get(broker):
            return False, self._broker_error[broker]
        try:
            healthy, reason = adapter.health()
        except Exception as exc:  # noqa: BLE001
            return False, f"health check failed: {exc}"
        return (True, str(reason or "ready")) if healthy else (False, str(reason or "not ready"))

    def _broker_health(self, broker: str) -> dict[str, str]:
        if broker not in self._brokers:
            return {"state": DISABLED, "reason": "no adapter attached"}
        if self._broker_error.get(broker):
            return {"state": ERROR, "reason": self._broker_error[broker]}
        try:
            healthy, reason = self._brokers[broker].health()
        except Exception as exc:  # noqa: BLE001
            return {"state": ERROR, "reason": f"health check failed: {exc}"}
        if healthy:
            return {"state": "HEALTHY", "reason": str(reason or "ready")}
        text = str(reason or "not ready").lower()
        if "connect" in text:
            return {"state": DISCONNECTED, "reason": str(reason or "disconnected")}
        return {"state": ERROR, "reason": str(reason or "error")}

    def _note_broker_state(self, broker: str, healthy: bool, reason: str) -> None:
        state = "HEALTHY" if healthy else "ERROR"
        with self._lock:
            previous = self._broker_state.get(broker)
            self._broker_state[broker] = state
        if previous != state:
            self._record(BROKER_READY if healthy else BROKER_NOT_READY, "", broker, reason)
            log.info("execution broker %s %s: %s", broker, state, reason)

    def _refuse(self, intent: OrderIntent, broker: str, reason: str) -> NormalizedOrderResult:
        self._record(ORDER_REJECTED, intent.instrument_id, broker, reason)
        return NormalizedOrderResult(
            client_order_id=intent.client_order_id,
            instrument_id=intent.instrument_id,
            strategy_id=intent.strategy_id,
            broker=broker,
            broker_order_id="",
            status=REJECTED,
            routing=REJECTED_SAFE,
            submitted_quantity=intent.quantity,
            rejection_reason=reason,
        )

    def _refuse_owned(self, client_order_id: str, reason: str) -> NormalizedOrderResult:
        owned = self._owned.get(client_order_id)
        if owned is None:
            return self._unknown_order_result(client_order_id, reason)
        return self._result_from_owned(owned, REJECTED_SAFE, reason)

    def _unknown_order_result(
        self, client_order_id: str, reason: str = UNKNOWN_CLIENT_ORDER
    ) -> NormalizedOrderResult:
        self._record(ORDER_REJECTED, "", "", f"{reason} ({client_order_id})")
        return NormalizedOrderResult(
            client_order_id=client_order_id,
            instrument_id="",
            strategy_id="",
            broker="",
            broker_order_id="",
            status=UNKNOWN,
            routing=REJECTED_SAFE,
            submitted_quantity=0.0,
            rejection_reason=reason,
        )

    def _result_from_owned(
        self, owned: _OwnedOrder, routing: str, reason: str
    ) -> NormalizedOrderResult:
        return NormalizedOrderResult(
            client_order_id=owned.client_order_id,
            instrument_id=owned.instrument_id,
            strategy_id=owned.strategy_id,
            broker=owned.broker,
            broker_order_id=owned.broker_order_id,
            status=owned.status,
            routing=routing,
            submitted_quantity=owned.submitted_quantity,
            filled_quantity=owned.filled_quantity,
            average_price=owned.average_price,
            rejection_reason=reason,
            attention_required=(routing == ATTENTION_REQUIRED),
        )

    def _attention(self, owned: _OwnedOrder, reason: str) -> NormalizedOrderResult:
        self._record(ORDER_STATUS_UNKNOWN, owned.instrument_id, owned.broker, reason)
        return self._result_from_owned(owned, ATTENTION_REQUIRED, reason)

    def _route_venue(self, intent: OrderIntent, venue: str) -> NormalizedOrderResult:
        """PAPER/SANDBOX: named venue only — live brokers are never fallback."""
        adapter = self._brokers.get(venue)
        if adapter is None:
            return self._refuse(
                intent, "", f"no {venue} venue configured — live brokers never used for {venue}"
            )
        healthy, reason = self._probe(venue)
        self._note_broker_state(venue, healthy, reason)
        if not healthy:
            return self._refuse(intent, venue, f"{REASON_NOT_READY}: {reason}")
        # Single eligible venue: it IS the first-choice venue for this mode.
        return self._submit(intent, venue, adapter, True, allow_failover=False)

    def _route_live(self, intent: OrderIntent) -> NormalizedOrderResult:
        """LIVE: primary→secondary over mapping + readiness (fail-closed)."""
        failures: list[tuple[str, str]] = []
        for broker in self._config.ordered_brokers:
            mapping = self._mappings.get_mapping(intent.instrument_id, broker)
            if mapping.status != "FOUND" or mapping.provider_instrument is None:
                failures.append((broker, _mapping_refusal(mapping.status)))
                continue
            if broker not in self._brokers:
                failures.append((broker, BROKER_UNAVAILABLE))
                continue
            healthy, reason = self._probe(broker)
            self._note_broker_state(broker, healthy, reason)
            if not healthy:
                failures.append((broker, f"{REASON_NOT_READY}: {reason}"))
                continue
            venue_symbol = _venue_symbol(broker, mapping.provider_instrument)
            return self._submit(
                intent,
                broker,
                self._brokers[broker],
                broker == self._config.primary,
                allow_failover=True,
                venue_symbol=venue_symbol,
                failures=failures,
            )
        reason = failures[0][1] if failures else NO_READY_BROKER
        return self._refuse(intent, "", reason)

    def _submit(
        self,
        intent: OrderIntent,
        broker: str,
        adapter: Any,
        is_primary: bool,
        allow_failover: bool,
        venue_symbol: str = "",
        failures: list[tuple[str, str]] | None = None,
    ) -> NormalizedOrderResult:
        plan = _VenuePlan(
            symbol=venue_symbol or intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            time_in_force=intent.time_in_force,
        )
        self._record(
            ORDER_ROUTED_PRIMARY if is_primary else ORDER_ROUTED_SECONDARY,
            intent.instrument_id,
            broker,
            intent.client_order_id,
        )
        try:
            broker_order_id = adapter.place_order(plan, intent.client_order_id)
        except Exception as exc:  # noqa: BLE001
            return self._submit_failed(intent, broker, exc, allow_failover, failures or [])
        broker_id = str(broker_order_id or "").strip()
        if not broker_id:
            return self._submit_failed(
                intent,
                broker,
                _RefusedError("empty broker order id"),
                allow_failover,
                failures or [],
            )
        owned = _OwnedOrder(
            client_order_id=intent.client_order_id,
            instrument_id=intent.instrument_id,
            strategy_id=intent.strategy_id,
            broker=broker,
            broker_order_id=broker_id,
            submitted_quantity=intent.quantity,
            status=SUBMITTED,
            routing=ROUTED_PRIMARY if is_primary else ROUTED_SECONDARY,
        )
        with self._lock:
            self._owned[intent.client_order_id] = owned
            self._broker_error.pop(broker, None)
        self._record(ORDER_SUBMITTED, intent.instrument_id, broker, broker_id)
        self._record(ORDER_ACCEPTED, intent.instrument_id, broker, broker_id)
        log.info(
            "order routed %s via %s broker_order_id=%s", intent.client_order_id, broker, broker_id
        )
        return self._result_from_owned(owned, owned.routing, "")

    def _submit_failed(
        self,
        intent: OrderIntent,
        broker: str,
        exc: BaseException,
        allow_failover: bool,
        failures: list[tuple[str, str]],
    ) -> NormalizedOrderResult:
        code = str(getattr(exc, "code", "") or "").strip().upper()
        if _acceptance_unknown(exc, code):
            # Transport uncertainty: acceptance UNKNOWN — reconcile against
            # the ORIGINAL broker, never blind-submit elsewhere.
            owned = _OwnedOrder(
                client_order_id=intent.client_order_id,
                instrument_id=intent.instrument_id,
                strategy_id=intent.strategy_id,
                broker=broker,
                broker_order_id="",
                submitted_quantity=intent.quantity,
                status=UNKNOWN,
                routing=REJECTED_SAFE,
            )
            with self._lock:
                self._owned[intent.client_order_id] = owned
            self._record(
                ORDER_STATUS_UNKNOWN,
                intent.instrument_id,
                broker,
                f"{code or type(exc).__name__}: {exc}",
            )
            log.warning(
                "order %s acceptance UNKNOWN via %s: %s", intent.client_order_id, broker, exc
            )
            result = self._result_from_owned(owned, REJECTED_SAFE, f"acceptance unknown: {exc}")
            return NormalizedOrderResult(
                client_order_id=result.client_order_id,
                instrument_id=result.instrument_id,
                strategy_id=result.strategy_id,
                broker=result.broker,
                broker_order_id=result.broker_order_id,
                status=result.status,
                routing=result.routing,
                submitted_quantity=result.submitted_quantity,
                rejection_reason=result.rejection_reason,
                attention_required=True,
            )
        reason = f"{code + ': ' if code else ''}{exc}".strip()
        self._record(ORDER_REJECTED, intent.instrument_id, broker, reason)
        if allow_failover:
            return self._route_live_failover(intent, broker, reason, failures)
        return self._refuse(intent, broker, reason)

    def _route_live_failover(
        self,
        intent: OrderIntent,
        failed_broker: str,
        reason: str,
        failures: list[tuple[str, str]],
    ) -> NormalizedOrderResult:
        """Secondary attempt after DEFINITIVE non-acceptance (never UNKNOWN)."""
        failures = [*failures, (failed_broker, reason)]
        for broker in self._config.ordered_brokers:
            if broker == failed_broker:
                continue
            mapping = self._mappings.get_mapping(intent.instrument_id, broker)
            if mapping.status != "FOUND" or mapping.provider_instrument is None:
                failures.append((broker, _mapping_refusal(mapping.status)))
                continue
            if broker not in self._brokers:
                failures.append((broker, BROKER_UNAVAILABLE))
                continue
            healthy, health_reason = self._probe(broker)
            self._note_broker_state(broker, healthy, health_reason)
            if not healthy:
                failures.append((broker, f"{REASON_NOT_READY}: {health_reason}"))
                continue
            venue_symbol = _venue_symbol(broker, mapping.provider_instrument)
            return self._submit(
                intent,
                broker,
                self._brokers[broker],
                broker == self._config.primary,
                allow_failover=False,
                venue_symbol=venue_symbol,
                failures=failures,
            )
        return self._refuse(intent, "", failures[0][1] if failures else NO_READY_BROKER)

    def _lifecycle(
        self,
        client_order_id: str,
        action: str,
        quantity: float | None = None,
        price: float | None = None,
    ) -> NormalizedOrderResult:
        owned = self._owned.get(client_order_id)
        if owned is None:
            return self._unknown_order_result(client_order_id)
        adapter = self._brokers.get(owned.broker)
        if adapter is None:
            return self._attention(owned, f"owning broker {owned.broker!r} unavailable")
        # Lifecycle actions ATTEMPT the owning broker even when it looks
        # unhealthy — a struggling venue may still accept a cancel (the
        # safety action must try), and any failure converts to an explicit
        # attention state here. Failover to another broker never happens.
        try:
            if action == "cancel":
                ok = adapter.cancel_order(owned.broker_order_id)
                event, status = ORDER_CANCEL_REQUESTED, CANCELLED
            else:
                ok = adapter.modify_order(owned.broker_order_id, quantity, price)
                event, status = ORDER_MODIFY_REQUESTED, MODIFIED
        except Exception as exc:  # noqa: BLE001
            return self._attention(owned, f"{action} failed: {exc}")
        self._record(event, owned.instrument_id, owned.broker, owned.broker_order_id)
        if not ok:
            return self._attention(owned, f"broker declined {action} — reconcile")
        with self._lock:
            owned.status = status
        return self._result_from_owned(owned, owned.routing, "")

    def _query_adapter(self, adapter: Any, broker_order_id: str) -> Any | None:
        query = getattr(adapter, "query_order", None)
        if callable(query):
            try:
                return query(broker_order_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("broker query failed: %s", exc)
                return None
        stream = getattr(adapter, "stream_events", None)
        if callable(stream):
            try:
                events = stream()
            except Exception as exc:  # noqa: BLE001
                log.warning("broker stream scan failed: %s", exc)
                return None
            if not isinstance(events, (tuple, list)):
                return None
            for event in events:
                if not isinstance(event, dict):
                    continue
                if str(event.get("broker_order_id", "")) == broker_order_id:
                    return event
        return None


def _mapping_refusal(status: str) -> str:
    if status == DISABLED:
        return BROKER_MAPPING_DISABLED
    if status == "CONFLICT":
        return BROKER_MAPPING_CONFLICT
    return BROKER_MAPPING_MISSING


def _venue_symbol(provider: str, record: Any) -> str:
    """Per-broker order symbol (mapping-verified, never guessed).

    FYERS addresses the mapping id itself (``NSE:SBIN-EQ`` passthrough);
    Zerodha Kite needs the bare tradingsymbol; anything else uses the
    mapping id (documented default).
    """
    name = str(provider or "").strip().upper()
    if name == "ZERODHA":
        return str(getattr(record, "symbol", "") or "").strip()
    return str(getattr(record, "provider_instrument_id", "") or "").strip()


def _acceptance_unknown(exc: BaseException, code: str) -> bool:
    """True when acceptance is uncertain (timeout/transport/uncoded)."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, (ConnectionError, OSError)) and not isinstance(exc, _RefusedError):
        return True
    return code in ("", "UNKNOWN", "NETWORK_ERROR", "NETWORK", "TIMEOUT", "DUPLICATE_ORDER")


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
        and value not in (float("inf"), float("-inf"))
        and value > 0
    )


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _normalize_status(raw: Any) -> str:
    """Raw venue status → OrderState value (unknown stays UNKNOWN)."""
    if isinstance(raw, bool):
        return UNKNOWN
    if isinstance(raw, int):
        return _FYERS_NUMERIC_STATUS.get(raw, UNKNOWN)
    text = str(raw or "").strip().upper()
    if not text:
        return UNKNOWN
    for vocabulary, status in _STATUS_TABLE:
        if text in vocabulary:
            return status
    return UNKNOWN


__all__ = ["ExecutionRouter", "BrokerExecutionAdapter", "ExecutionRouterError"]
