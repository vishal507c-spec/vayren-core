"""FyersSessionAdapter — authenticated FYERS session and live order execution.

Owns the FYERS API v3 transport for one verified session: ``connect``,
``health``, ``account``, ``funds``, ``positions``, ``open_orders``, and live
order execution: ``place_order``, ``cancel_order``, ``modify_order``,
``stream_events``.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.common.api_governance import APIGovernor, BrokerGateway, CallClass, CircuitOpenError
from broker.providers.fyers.live_auth import (
    API_BASE,
    MARKET_DATA_BASE,
    AuthError,
    HttpTransport,
    _UrllibTransport,
)
from broker.vocab import BrokerError, Environment, ErrorCode

logger = logging.getLogger(__name__)

TRADING_CAPABILITIES: CapabilitySet = capability_set(
    {
        Domain.TRADING: (
            Caps.ORDERS_MARKET,
            Caps.ORDERS_LIMIT,
            Caps.ORDERS_CANCEL,
            Caps.ORDERS_MODIFY,
            Caps.ACCOUNT_POSITIONS,
            Caps.ACCOUNT_OPEN_ORDERS,
            Caps.ACCOUNT_FUNDS,
            Caps.STREAM_FILLS,
        )
    }
)

#: FYERS order status codes — the venue's own order-status table:
#: ``1`` Cancelled · ``2`` Traded/Filled · ``3`` (not used) · ``4`` Transit ·
#: ``5`` Rejected · ``6`` Pending · ``7`` Expired.
#:
#: The mapping is used by BOTH the order socket and the REST order book, so
#: the two surfaces can never disagree about the same order. ``6`` is
#: PENDING — the venue still works such an order — and ``7`` EXPIRED is
#: terminal. The previous reading (``6`` as cancelled) contradicted the
#: order-socket parser and made a pending order look finished, which hid a
#: live order from the duplicate check and from the protective-stop
#: cancellation evidence.
_STATUS_CANCELLED = 1
_STATUS_TRADED = 2
_STATUS_TRANSIT = 4
_STATUS_REJECTED = 5
_STATUS_PENDING = 6
_STATUS_EXPIRED = 7

#: Venue-neutral order status names, used by every surface that reports an
#: order's state (REST order book, order socket, cancellation evidence).
STATUS_OPEN = "OPEN"
STATUS_CANCELLED = "CANCELLED"
STATUS_FILLED = "FILLED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_UNKNOWN = "UNKNOWN"

#: THE table: venue status code → venue-neutral name. Every surface reads
#: this one, so the order socket and the REST order book can never disagree
#: about the same order.
_ORDER_STATUS_BY_CODE: dict[int, str] = {
    _STATUS_CANCELLED: STATUS_CANCELLED,
    _STATUS_TRADED: STATUS_FILLED,
    _STATUS_TRANSIT: STATUS_OPEN,
    _STATUS_REJECTED: STATUS_REJECTED,
    _STATUS_PENDING: STATUS_OPEN,
    _STATUS_EXPIRED: STATUS_EXPIRED,
}

#: Statuses where the order is FINISHED at the venue: cancelled, traded,
#: rejected or expired. Everything else (4 transit, 6 pending) means the
#: venue still works the order.
_TERMINAL_STATUSES = frozenset(
    {_STATUS_CANCELLED, _STATUS_TRADED, _STATUS_REJECTED, _STATUS_EXPIRED}
)

#: The same set in venue-neutral names — derived, so the two can never drift.
_TERMINAL_STATUS_NAMES = frozenset(_ORDER_STATUS_BY_CODE[code] for code in _TERMINAL_STATUSES)

#: FYERS prefixes a user order tag with ``1:`` (and its own tags with
#: ``2:``). The prefix is stripped when the tag is reported back, so the
#: engine can match a venue order with its own client order id.
_VENUE_TAG_PREFIXES = ("1:", "2:")


def _client_order_id(venue_tag: Any) -> str:
    """Our client order id behind a venue order tag (``1:tag`` -> ``tag``)."""
    text = str(venue_tag or "")
    for prefix in _VENUE_TAG_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _order_status(raw: Any) -> str:
    """The venue-neutral status of one venue order.

    Numeric FYERS codes and their string forms map onto the same names the
    engine and the strategy router already use, so every surface reports an
    order identically.
    """
    if isinstance(raw, bool):
        return STATUS_UNKNOWN
    if isinstance(raw, int):
        return _ORDER_STATUS_BY_CODE.get(raw, STATUS_UNKNOWN)
    text = str(raw or "").strip().upper()
    if text in ("CANCELLED", "CANCELED", "CANCEL"):
        return STATUS_CANCELLED
    if text in ("FILLED", "TRADED", "COMPLETE"):
        return STATUS_FILLED
    if text in ("REJECTED", "REJECT"):
        return STATUS_REJECTED
    if text in ("EXPIRED", "EXPIRE"):
        return STATUS_EXPIRED
    if text in ("OPEN", "PENDING", "TRANSIT", "ACKNOWLEDGED", "ACK", "WORKING", "PLACED"):
        return STATUS_OPEN
    if text.isdigit():
        return _order_status(int(text))
    return STATUS_UNKNOWN


#: FYERS envelope codes that prove the session/token is no longer usable.
#: These must become AuthError, never an empty successful-looking read.
_AUTH_ERROR_CODES = frozenset({-8, -15, -16, -17, -99})

#: The execution engine marks its stop-protection orders with an ``sl:``
#: intent id (``sl:<symbol>:<seq>``) and a matching client order id
#: (``sl:<symbol>:<seq>:o1``) — see ``execution/runtime/session.py``
#: (``_place_protective_stop``). The engine itself routes protective-stop
#: fills by ``intent_id.startswith("sl:")``. The adapter reuses that SAME
#: convention to recognise a protective exit, so the intent comes from the
#: existing execution logic instead of a new, parallel signal.
#:
#: The shape is deliberately narrow. A strategy plan never matches it: the
#: execution planner only emits MARKET/LIMIT plans and never sets a stop
#: price, the strategy router's plan carries no intent id at all, and the
#: operator order-path test uses ``order-path-test:`` ids. Only the engine's
#: own protective order produces ``sl:<symbol>:<seq>`` + a STOP type + a
#: stop price + the matching client order id.
_PROTECTIVE_INTENT_RE = re.compile(r"^sl:(?P<symbol>.+):(?P<seq>\d+)$")
#: Order types the engine uses for a protective stop. A strategy entry is
#: MARKET/LIMIT, so a stop type alone never qualifies an order.
_PROTECTIVE_ORDER_TYPES = frozenset({"STOP_MARKET", "STOP_LIMIT", "STOP"})

#: Upper bound for the order-intent memory. The oldest intents are dropped
#: first; a dropped intent only means a later cancel of that order is
#: treated as ambiguous — the safe direction (it stays circuit-gated).
_ORDER_INTENT_MEMORY_LIMIT = 2000

#: Vendor order-socket subscription keys (``FyersOrderSocket.socket_type``).
#: The SDK splits ``data_type`` on "," and looks each token up in
#: ``socket_type`` (``OnOrders``/``OnTrades``/``OnPositions``/``OnGeneral``),
#: so the legacy ``"orders,trades"`` raised ``KeyError`` inside the SDK and
#: the subscription was never sent. Orders + trades only — positions stay on
#: the REST snapshot path.
ORDER_WS_SUBSCRIPTION = "OnOrders,OnTrades"

#: A connection shorter than this never carried an order update.
ORDER_WS_STABLE_SECONDS = 30.0
#: Consecutive short-lived connections before the socket is abandoned.
ORDER_WS_FLAP_LIMIT = 3
#: After abandoning the socket, wait this long before a cold start again.
#: Without it, health() would immediately recreate the socket and the cycle
#: would simply repeat at a slower cadence.
ORDER_WS_COOLDOWN_SECONDS = 900.0
#: A stream that WAS delivering updates and then goes quiet this long is
#: stale. A socket that never delivered (no orders in flight) is not stale —
#: that is a different, honest state.
ORDER_WS_STALE_SECONDS = 300.0


STATE_DISCONNECTED = "DISCONNECTED"
STATE_CONNECTING = "CONNECTING"
STATE_CONNECTED = "CONNECTED"
STATE_RECONNECTING = "RECONNECTING"
STATE_ERROR = "ERROR"

#: Account-level reads are shared by every per-symbol LiveSession attached to
#: this one authenticated adapter. Reconciliation asks each session in turn,
#: so without coalescing a 22-symbol fleet fires 44 REST reads inside one
#: sub-second loop and the venue answers HTTP 429. The snapshot is broker truth
#: from the first caller in the window; failures are cached and re-raised
#: unchanged (a 429 still blocks every session, it just is not hammered 22x).
ACCOUNT_SNAPSHOT_CACHE_SECONDS = 5.0
#: How long a caller that JOINS an in-flight account read waits for it. The
#: bound exists so a wedged venue read can never park callers forever: on
#: expiry the joiner fails closed (blocking transport error) instead of
#: holding a queue that would otherwise grow without limit.
ACCOUNT_SNAPSHOT_WAIT_SECONDS = 30.0


@dataclass(frozen=True)
class _AccountSnapshotCacheEntry:
    fetched_at: float
    positions: tuple[dict[str, Any], ...] = ()
    orders: tuple[dict[str, Any], ...] = ()
    error: Exception | None = None


class _AccountSnapshotFetch:
    """In-flight ticket for the one shared account read (single-flight).

    Exactly one caller (the owner) performs the venue reads; every other
    caller joins this ticket and waits on ``event`` WITHOUT holding
    ``_account_snapshot_lock``, then reads the owner's published rows (or
    re-raised failure). The lock therefore only ever guards bookkeeping — it
    is never held across broker REST, so one slow venue response cannot
    convoy unrelated callers behind a mutex.
    """

    def __init__(self) -> None:
        self.event = threading.Event()
        self.epoch = 0
        self.positions: tuple[dict[str, Any], ...] = ()
        self.orders: tuple[dict[str, Any], ...] = ()
        self.error: Exception | None = None


class FyersSessionAdapter:
    """FYERS trading adapter bound to one verified access token."""

    name = "fyers"
    environment = Environment.LIVE

    def __init__(
        self,
        app_id: str,
        access_token: str,
        transport: HttpTransport | None = None,
        poll_interval_s: float = 1.0,
        order_socket_factory: Callable[..., Any] | None = None,
        enable_order_ws: bool = True,
        account_snapshot_cache_s: float = ACCOUNT_SNAPSHOT_CACHE_SECONDS,
        account_snapshot_wait_s: float = ACCOUNT_SNAPSHOT_WAIT_SECONDS,
    ) -> None:
        self._app_id = str(app_id or "")
        self._token = str(access_token or "")
        self._http = transport if transport is not None else _UrllibTransport()
        # One authenticated adapter is shared by every per-symbol LiveSession.
        # Its governor, token bucket, circuit, historical cache and in-flight
        # coordination are therefore shared by the whole symbol fleet too.
        self._governor = APIGovernor(log=logger)
        self._gateway = BrokerGateway(self._governor)
        self._connected = False
        self._poll_interval_s = max(0.0, float(poll_interval_s))
        self._last_poll_epoch = 0.0
        self._last_orders: dict[str, dict[str, Any]] = {}
        self._seen_client_ids: dict[str, str] = {}
        #: broker order id -> "protective" | "entry". The intent of every order
        #: this adapter placed, so a later cancel/modify of a KNOWN order can
        #: be recognised as risk-reducing. An unknown id stays ambiguous and
        #: is treated as an ordinary (gated) operation.
        self._order_intent: dict[str, str] = {}
        self._cached_snapshot: tuple[dict[str, Any], ...] = ()

        # Short-lived profile cache to avoid duplicate burst requests to /profile
        self._profile_cache: tuple[float, dict[str, Any]] | None = None
        self._profile_cache_ttl_s: float = 5.0

        # One account snapshot serves all per-symbol reconciliation callers.
        self._account_snapshot_lock = threading.Lock()
        self._account_snapshot_cache: _AccountSnapshotCacheEntry | None = None
        self._account_snapshot_cache_s = max(0.0, float(account_snapshot_cache_s))
        self._account_snapshot_fetch: _AccountSnapshotFetch | None = None
        self._account_snapshot_epoch = 0
        self._account_snapshot_wait_s = max(0.1, float(account_snapshot_wait_s))

        # Order WebSocket and stream state
        self._order_socket_factory = order_socket_factory
        self._enable_order_ws = enable_order_ws
        self._order_ws: Any = None
        self._order_ws_state = STATE_DISCONNECTED
        self._order_ws_error = ""
        # Consecutive repeats of the SAME socket error, so a retrying
        # socket logs once instead of flooding (818 identical lines in 6h).
        self._order_ws_error_count = 0
        # Rate-limiting key kept separately from the DISPLAYED reason: the
        # displayed reason is overwritten when the socket is abandoned, which
        # would otherwise re-open the log flood every cycle.
        self._order_ws_last_error = ""
        # Flap breaker: when the venue never keeps the order socket open
        # (typically outside market hours) the vendor client reconnects
        # forever. These track short-lived connections so the adapter can
        # stop that loop instead of spinning for hours.
        self._order_ws_connected_at: float | None = None
        self._order_ws_flaps = 0
        # One connection may emit several error/close callbacks. Count it
        # once so a single failure cannot trip the breaker by itself.
        self._order_ws_flap_counted = False
        # Order-update delivery evidence, tracked apart from socket state.
        self._order_ws_messages_received = 0
        self._order_ws_last_message_at: float | None = None
        # Epoch until which the order socket stays abandoned (venue not
        # serving the stream). Cleared by an explicit connect().
        self._order_ws_suspended_until = 0.0
        # Serializes socket creation/teardown and bounded START-time proofs so
        # two callers can never create competing reconnect drivers.
        self._order_ws_guard = threading.RLock()
        self._order_ws_verify_lock = threading.Lock()
        self._order_event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10_000)
        self._order_events: list[dict[str, Any]] = []
        self._order_events_seq = 0
        self._consumer_cursors: dict[str, int] = {}
        self._order_event_lock = threading.Lock()
        self._order_snapshot_cache: tuple[float, tuple[dict[str, Any], ...]] | None = None
        self._order_snapshot_cache_ttl_s: float = 2.0
        self._seen_fills: set[str] = set()  # Dedup key: f"{order_id}:{trade_no or fill_qty}"
        self._order_cum_filled: dict[
            str, float
        ] = {}  # Track cum filled per order_id for delta calc

    # ── capabilities & environment ─────────────────────────────────────

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (
            "orders.market",
            "orders.limit",
            "orders.cancel",
            "orders.modify",
            "account.positions",
            "account.open_orders",
            "account.funds",
            "stream.fills",
        )

    @property
    def order_ws_state(self) -> str:
        return self._order_ws_state

    @property
    def connected(self) -> bool:
        """True when the adapter has an active, verified session."""
        return self._connected

    @property
    def governor(self) -> APIGovernor:
        """The one API governor shared by every session using this adapter."""
        return self._governor

    def api_metrics(self) -> dict[str, Any]:
        """Current call/error/latency/throttle/cache counters (read-only copy)."""
        return self._governor.metrics.snapshot()

    # ── lifecycle ──────────────────────────────────────────────────────

    def _get_profile(self, force: bool = False) -> dict[str, Any]:
        """Fetch profile from venue or use short-lived cache (prevents duplicate bursts)."""
        now = time.monotonic()
        if not force and self._profile_cache is not None:
            cached_at, cached_body = self._profile_cache
            if now - cached_at < self._profile_cache_ttl_s:
                self._governor.metrics.record_cache_hit()
                return cached_body
        self._governor.metrics.record_cache_miss()
        body = self._get("profile")
        if isinstance(body, dict) and body.get("s") == "ok":
            self._profile_cache = (now, body)
        return body

    def connect(self) -> None:
        """Verify the session with one read-only profile call and start order WS if enabled."""
        try:
            body = self._get_profile(force=True)
            if not isinstance(body, dict) or body.get("s") != "ok":
                msg = body.get("message") if isinstance(body, dict) else "session check failed"
                raise AuthError(f"FYERS session rejected: {msg}", code="AUTH")
        except (AuthError, BrokerError, CircuitOpenError):
            raise
        except Exception as exc:
            raise AuthError(f"FYERS session rejected: {exc}", code="AUTH") from None
        self._connected = True
        self._invalidate_account_snapshot()
        # An explicit (re)connect is the operator saying "try again".
        self._order_ws_suspended_until = 0.0
        self._order_ws_flaps = 0

        if self._enable_order_ws:
            self._start_order_ws()

    def disconnect(self) -> None:
        self._profile_cache = None
        self._invalidate_account_snapshot()
        self._stop_order_ws()
        with self._order_event_lock:
            self._order_events.clear()
            self._consumer_cursors.clear()
        while not self._order_event_queue.empty():
            try:
                self._order_event_queue.get_nowait()
            except queue.Empty:
                break
        self._connected = False

    def health(self) -> tuple[bool, str]:
        """Connection-only health (never implies LIVE readiness)."""
        if not self._app_id or not self._token:
            return False, "CREDENTIALS not ready"
        try:
            body = self._get_profile()
        except AuthError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"venue unreachable: {exc}"
        if not isinstance(body, dict) or body.get("s") != "ok":
            return False, "session check failed"

        # If order WS is enabled and degraded, attempt reconnection without failing
        # the verified REST session
        if self._enable_order_ws and self._order_ws is None:
            # Only a COLD start belongs to us. While a socket exists the
            # SDK is reconnecting on its own, so calling _start_order_ws
            # here would race it and multiply the connection attempts.
            try:
                self._start_order_ws()
            except Exception as exc:
                logger.warning("ORDER_WS_RESTART_FAILED broker=FYERS reason=%s", exc)
        return True, "ok"

    def order_stream_health(self) -> tuple[bool, str]:
        """Dedicated health check for the order WebSocket stream.

        A socket that exists but is not yet connected is RECOVERING, not
        broken: the SDK reconnects on its own, so the honest word is
        "reconnecting" (the manager shows a degraded leg) instead of a hard
        error that would make the leg look permanently failed.

        Connected and silent is reported as connected-but-awaiting, NOT as
        streaming: an open socket that has never carried an order update is
        not evidence that order updates work. A stream that WAS delivering
        and then goes quiet is reported STALE, which is a real degradation.
        """
        if not self._enable_order_ws:
            return True, "order socket disabled (rest polling fallback active)"
        if self._order_ws_state == STATE_CONNECTED:
            if self._order_ws_messages_received == 0:
                return True, "connected — no order updates received yet (none in flight)"
            age = self.order_stream_age_s
            if age is not None and age > ORDER_WS_STALE_SECONDS:
                return False, f"stale — no order update for {int(age)}s"
            return True, (
                f"streaming — {self._order_ws_messages_received} updates, last {int(age or 0)}s ago"
            )
        if self._order_ws is not None:
            return False, "reconnecting"
        if self._order_ws_state == STATE_ERROR:
            return False, f"error: {self._order_ws_error}"
        return False, "disconnected"

    def verify_order_stream(self, timeout_s: float = 5.0) -> tuple[bool, str]:
        """Bounded, authoritative proof that the order stream is usable NOW.

        START must not treat a cached ``CONNECTED`` snapshot as evidence.
        This waits at most ``timeout_s`` for the existing socket to reach
        CONNECTED and then applies the same delivery/staleness rules as
        :meth:`order_stream_health`.

        Reconnect ownership stays with the vendor SDK: an existing socket is
        never replaced or restarted here, and a cold start happens exactly
        once when no socket exists. A failure is returned to the caller (which
        fails closed) instead of triggering an internal retry loop.
        """
        if not self._enable_order_ws:
            return True, "order socket disabled (REST polling fallback active)"
        timeout = max(0.0, float(timeout_s))
        with self._order_ws_verify_lock:
            if self._order_ws_state == STATE_CONNECTED:
                return self.order_stream_health()
            if time.monotonic() < self._order_ws_suspended_until:
                return False, (
                    "order stream cooldown active — the venue closed it repeatedly; "
                    "reconnect in SYSTEM → BROKERS"
                )
            if self._order_ws is None:
                # One cold start only. If it fails there is no retry here:
                # the flap breaker exists precisely to stop that loop.
                self._start_order_ws()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._order_ws_state == STATE_CONNECTED:
                    return self.order_stream_health()
                if self._order_ws is None:
                    return False, self._order_ws_error or "order stream unavailable"
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            return False, (
                f"order stream not connected within {timeout:.1f}s (state: {self._order_ws_state})"
            )

    @property
    def order_stream_messages_received(self) -> int:
        """Order/trade updates actually delivered (not a connect claim)."""
        return self._order_ws_messages_received

    @property
    def order_stream_age_s(self) -> float | None:
        """Seconds since the last real order update, or None if never."""
        if self._order_ws_last_message_at is None:
            return None
        return max(0.0, time.monotonic() - self._order_ws_last_message_at)

    def _start_order_ws(self) -> None:
        """Create at most one order socket at a time (guarded cold start)."""
        with self._order_ws_guard:
            self._start_order_ws_locked()

    def _start_order_ws_locked(self) -> None:
        """Initialize and connect FyersOrderSocket.

        Reconnects are OWNED BY THE SDK (its ``On_close`` calls ``connect()``
        again). This method is only the cold start, so it must never fight
        that loop: a socket that already exists is left alone, and any
        socket we replace is closed first. Skipping the close leaked a live
        socket per attempt, and two competing reconnect drivers produced a
        storm (10 connects in 600ms, each erroring "Connection to remote
        host was lost").
        """
        if self._order_ws is not None:
            # The SDK is already (re)connecting on its own; a second driver
            # is what caused the thrash.
            return
        if self._order_ws_state == STATE_CONNECTED:
            return
        if time.monotonic() < self._order_ws_suspended_until:
            # Recently abandoned: the venue is not serving this stream, so
            # stay down until the cooldown expires (REST fallback covers
            # order state) instead of restarting the loop.
            return

        self._order_ws_state = STATE_CONNECTING
        logger.info("ORDER_WS_CONNECTING broker=FYERS")

        token_str = (
            f"{self._app_id}:{self._token}"
            if not self._token.startswith(f"{self._app_id}:")
            else self._token
        )

        try:
            if self._order_socket_factory is not None:
                self._order_ws = self._order_socket_factory(
                    access_token=token_str,
                    on_orders=self._on_ws_orders,
                    on_trades=self._on_ws_trades,
                    on_error=self._on_ws_error,
                    on_connect=self._on_ws_connect,
                    on_close=self._on_ws_close,
                )
            else:
                import importlib

                order_ws_mod = importlib.import_module("fyers_apiv3.FyersWebsocket.order_ws")
                fyers_order_socket_cls = order_ws_mod.FyersOrderSocket

                self._order_ws = fyers_order_socket_cls(
                    access_token=token_str,
                    on_orders=self._on_ws_orders,
                    on_trades=self._on_ws_trades,
                    on_error=self._on_ws_error,
                    on_connect=self._on_ws_connect,
                    on_close=self._on_ws_close,
                )

            if hasattr(self._order_ws, "connect"):
                self._order_ws.connect()
            # Subscription happens exactly once, in _on_ws_connect: the
            # socket is not usable before that callback, and subscribing
            # here as well sent a duplicate subscription on every attempt.
        except Exception as exc:
            self._order_ws_state = STATE_ERROR
            self._order_ws_error = str(exc)
            self._order_ws = None
            logger.error("ORDER_WS_INIT_FAILED broker=FYERS reason=%s", exc)

    def _stop_order_ws(self) -> None:
        """Disconnect and cleanup FyersOrderSocket (guarded)."""
        with self._order_ws_guard:
            self._stop_order_ws_locked()

    def _stop_order_ws_locked(self) -> None:
        """Disconnect and cleanup FyersOrderSocket."""
        if self._order_ws is not None:
            try:
                if hasattr(self._order_ws, "close_connection"):
                    self._order_ws.close_connection()
            except Exception as exc:
                logger.warning("ORDER_WS_CLOSE_WARN broker=FYERS reason=%s", exc)
        self._order_ws = None
        self._order_ws_state = STATE_DISCONNECTED
        self._order_ws_connected_at = None
        logger.info("ORDER_WS_DISCONNECTED broker=FYERS")

    def _on_ws_connect(self) -> None:
        self._order_ws_state = STATE_CONNECTED
        self._order_ws_error = ""
        self._order_ws_error_count = 0
        self._order_ws_last_error = ""
        self._order_ws_connected_at = time.monotonic()
        self._order_ws_flap_counted = False
        # A reconnect may have missed order/position transitions: never let a
        # snapshot taken before the gap satisfy the next reconciliation.
        self._invalidate_account_snapshot()
        logger.info("ORDER_WS_CONNECTED broker=FYERS")
        if self._order_ws is not None and hasattr(self._order_ws, "subscribe"):
            try:
                self._order_ws.subscribe(data_type=ORDER_WS_SUBSCRIPTION)
            except Exception as exc:
                logger.error("ORDER_WS_SUBSCRIBE_FAILED broker=FYERS reason=%s", exc)

    def _on_ws_close(self, *args: Any, **_kwargs: Any) -> None:
        if self._connected:
            self._order_ws_state = STATE_DISCONNECTED
        logger.info("ORDER_WS_CLOSED broker=FYERS args=%s", args)
        self._note_ws_flap()

    def _note_ws_flap(self) -> None:
        """Break the SDK's infinite reconnect loop on a socket that never stays.

        The vendor socket reconnects forever on its own (``On_close`` →
        ``connect``). When the venue refuses/closes the stream — the order
        socket is not served outside market hours — that produced an
        unbroken 2-second CONNECTED/ERROR cycle (700+ pairs in a minute),
        burning CPU and burying real logs. After a few connections that
        never survived ``ORDER_WS_STABLE_SECONDS`` the socket is stopped for
        good and the leg reports honestly DISCONNECTED; REST polling still
        covers order state, and the manager keeps the leg degraded.
        """
        connected_at = self._order_ws_connected_at
        if connected_at is not None and (
            time.monotonic() - connected_at >= ORDER_WS_STABLE_SECONDS
        ):
            self._order_ws_flaps = 0
            return
        if connected_at is None:
            return  # never actually connected: not a flap, just a failure
        if self._order_ws_flap_counted:
            return  # this connection already counted once
        self._order_ws_flap_counted = True
        self._order_ws_flaps += 1
        if self._order_ws_flaps < ORDER_WS_FLAP_LIMIT:
            return
        logger.warning(
            "ORDER_WS_ABANDONED broker=FYERS flaps=%d — venue is not keeping the "
            "order stream open (market closed?); REST polling remains active",
            self._order_ws_flaps,
        )
        self._order_ws_flaps = 0
        self._stop_order_ws()
        self._order_ws_suspended_until = time.monotonic() + ORDER_WS_COOLDOWN_SECONDS
        self._order_ws_error = (
            "order stream unavailable — venue closes the socket (REST fallback active)"
        )

    def _on_ws_error(self, message: Any) -> None:
        """Record a socket error WITHOUT flooding the log.

        A dropped order socket retries, so the same error arrives many
        times per minute; logging each one at ERROR buried every other
        diagnostic (800+ identical lines in 6h). The FIRST error is an
        error, repeats are warnings, and the state/health still report the
        degraded leg.
        """
        self._order_ws_state = STATE_ERROR
        text = str(message)
        # Count the failed connection BEFORE the log-dedup branch. The vendor
        # alternates "host lost" / "Handshake 403" and every new connect
        # resets ``_order_ws_last_error``, so the old placement only counted
        # repeated identical strings and let an expired-token 403 loop run
        # for hours (144k log lines).
        self._note_ws_flap()
        if text != self._order_ws_last_error:
            self._order_ws_last_error = text
            self._order_ws_error_count = 1
            self._order_ws_error = text
            logger.error("ORDER_WS_ERROR broker=FYERS error=%s", text)
            return
        self._order_ws_error_count += 1
        if self._order_ws_error_count <= 3:
            logger.warning("ORDER_WS_ERROR broker=FYERS error=%s (repeat)", text)
        elif self._order_ws_error_count % 50 == 0:
            logger.warning(
                "ORDER_WS_ERROR broker=FYERS error=%s (x%d, still reconnecting)",
                text,
                self._order_ws_error_count,
            )

    def _on_ws_orders(self, msg: Any) -> None:
        """Handle normalized order update from FyersOrderSocket."""
        if not isinstance(msg, dict):
            return
        order_dict = msg.get("orders") if "orders" in msg else msg
        if not isinstance(order_dict, dict):
            return

        events = self._parse_ws_order_event(order_dict)
        # Delivery evidence: a connected order socket that has never
        # delivered an update proves nothing about order-update flow.
        self._order_ws_messages_received += 1
        self._order_ws_last_message_at = time.monotonic()
        if events:
            # Broker truth changed; the shared account snapshot is now stale.
            self._invalidate_account_snapshot()
        with self._order_event_lock:
            for ev in events:
                self._order_events_seq += 1
                ev["_stream_seq"] = self._order_events_seq
                self._order_events.append(dict(ev))
                if len(self._order_events) > 2000:
                    self._order_events = self._order_events[-1000:]
                try:
                    self._order_event_queue.put_nowait(ev)
                except queue.Full:
                    logger.warning("ORDER_EVENT_QUEUE_FULL dropping event")

    def _on_ws_trades(self, msg: Any) -> None:
        """Handle normalized trade update from FyersOrderSocket."""
        if not isinstance(msg, dict):
            return
        trade_dict = msg.get("trades") if "trades" in msg else msg
        if not isinstance(trade_dict, dict):
            return

        ev = self._parse_ws_trade_event(trade_dict)
        self._order_ws_messages_received += 1
        self._order_ws_last_message_at = time.monotonic()
        if ev is not None:
            # A fill changes account truth; invalidate the shared snapshot
            # immediately, not only when the app next drains stream_events().
            self._invalidate_account_snapshot()
            with self._order_event_lock:
                self._order_events_seq += 1
                ev["_stream_seq"] = self._order_events_seq
                self._order_events.append(dict(ev))
                if len(self._order_events) > 2000:
                    self._order_events = self._order_events[-1000:]
                try:
                    self._order_event_queue.put_nowait(ev)
                except queue.Full:
                    logger.warning("ORDER_EVENT_QUEUE_FULL dropping trade event")

    def _parse_ws_order_event(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse FYERS WebSocket order dict into VAYREN events."""
        events: list[dict[str, Any]] = []
        tag = _client_order_id(order.get("orderTag") or order.get("ordertag") or "")
        order_id = str(order.get("id") or order.get("id_fyers") or "")
        if not tag and order_id in self._seen_client_ids.values():
            # Invert lookup
            for k, v in self._seen_client_ids.items():
                if v == order_id:
                    tag = k
                    break

        if not tag:
            return []

        status = order.get("status")
        state = _order_status(status)
        # The order socket and the REST order book read the SAME status table:
        # 4 transit and 6 pending mean the venue still works the order.
        if state == STATUS_OPEN:
            events.append(
                {
                    "type": "ack",
                    "client_order_id": tag,
                    "broker_order_id": order_id,
                }
            )

        # Check fills from order update if trades wasn't received
        cum_filled = 0.0
        try:
            cum_filled = float(order.get("filledQty") or order.get("qty_filled") or 0.0)
        except (TypeError, ValueError):
            cum_filled = 0.0

        prev_cum = self._order_cum_filled.get(order_id, 0.0)
        if cum_filled > prev_cum:
            delta_qty = cum_filled - prev_cum
            self._order_cum_filled[order_id] = cum_filled
            fill_dedup_key = f"{order_id}:cum_{cum_filled}"
            if fill_dedup_key not in self._seen_fills:
                self._seen_fills.add(fill_dedup_key)
                try:
                    price = float(order.get("tradedPrice") or order.get("price_traded") or 0.0)
                except (TypeError, ValueError):
                    price = 0.0
                side_val = order.get("side") or order.get("tran_side")
                side = "BUY" if side_val in (1, "BUY") else "SELL"
                partial = state not in _TERMINAL_STATUS_NAMES
                events.append(
                    {
                        "type": "fill",
                        "client_order_id": tag,
                        "broker_order_id": order_id,
                        "fill": SimpleNamespace(
                            client_order_id=tag,
                            broker_order_id=order_id,
                            symbol=str(order.get("symbol") or ""),
                            side=side,
                            fill_qty=delta_qty,
                            fill_price=price,
                            commission=0.0,
                            timestamp=str(
                                order.get("orderDateTime") or order.get("time_oms") or ""
                            ),
                            partial=partial,
                        ),
                    }
                )

        if state == STATUS_REJECTED:
            events.append(
                {
                    "type": "reject",
                    "client_order_id": tag,
                    "broker_order_id": order_id,
                    "reason": str(order.get("message") or order.get("oms_msg") or "order rejected"),
                }
            )
        elif state == STATUS_CANCELLED:
            events.append(
                {
                    "type": "cancel",
                    "client_order_id": tag,
                    "broker_order_id": order_id,
                }
            )
        elif state == STATUS_EXPIRED:
            events.append(
                {
                    "type": "expired",
                    "client_order_id": tag,
                    "broker_order_id": order_id,
                    "reason": str(order.get("message") or order.get("oms_msg") or "order expired"),
                }
            )

        return events

    def _parse_ws_trade_event(self, trade: dict[str, Any]) -> dict[str, Any] | None:
        """Parse FYERS WebSocket trade/execution print into a VAYREN fill event."""
        order_id = str(trade.get("orderNumber") or trade.get("id") or "")
        trade_id = str(trade.get("tradeNumber") or trade.get("id_fill") or "")
        tag = _client_order_id(trade.get("orderTag") or trade.get("ordertag") or "")

        if not tag and order_id in self._seen_client_ids.values():
            for k, v in self._seen_client_ids.items():
                if v == order_id:
                    tag = k
                    break

        if not tag:
            return None

        # Deduplicate trade execution by tradeNumber
        if trade_id:
            dedup_key = f"{order_id}:trade_{trade_id}"
        else:
            dedup_key = f"{order_id}:trade_{time.time()}"
        if trade_id and dedup_key in self._seen_fills:
            return None
        self._seen_fills.add(dedup_key)

        try:
            qty = float(trade.get("tradedQty") or trade.get("qty_traded") or 0.0)
            price = float(trade.get("tradePrice") or trade.get("price_traded") or 0.0)
        except (TypeError, ValueError):
            return None

        if qty <= 0:
            return None

        # Update cum filled
        prev_cum = self._order_cum_filled.get(order_id, 0.0)
        self._order_cum_filled[order_id] = prev_cum + qty

        side_val = trade.get("side") or trade.get("tran_side")
        side = "BUY" if side_val in (1, "BUY") else "SELL"

        return {
            "type": "fill",
            "client_order_id": tag,
            "broker_order_id": order_id,
            "fill": SimpleNamespace(
                client_order_id=tag,
                broker_order_id=order_id,
                symbol=str(trade.get("symbol") or ""),
                side=side,
                fill_qty=qty,
                fill_price=price,
                commission=0.0,
                timestamp=str(trade.get("orderDateTime") or trade.get("fill_time") or ""),
                # Conservative: LiveSession tracks remaining quantity via order state.
                partial=True,
            ),
        }

    # ── read-only account surface (manager health checks) ──────────────

    def account(self) -> dict[str, Any]:
        """Minimal identity dict (``account_id``/``user_id``/``client_id``)."""
        body = self._get_profile()
        data = body.get("data", {}) if isinstance(body, dict) else {}
        if not isinstance(data, dict):
            raise AuthError("venue returned an unreadable profile", code="AUTH")
        identity = (
            str(data.get("fy_id", "") or "")
            or str(data.get("client_id", "") or "")
            or str(data.get("id", "") or "")
        )
        if not identity:
            raise AuthError("venue returned no account identity", code="AUTH")
        return {
            "account_id": identity,
            "user_id": identity,
            "client_id": identity,
            "name": str(data.get("name", "") or ""),
        }

    def funds(self) -> dict[str, float]:
        """Extract verified funds from FYERS API v3 fund_limit response.

        FYERS returns a list of limit items:
          id 10 / 'Available Balance': actual available balance for trading
          id 1 / 'Total Balance': total balance
          id 2 / 'Utilized Amount': utilized margin
          id 3 / 'Clear Balance': clear balance
        Never sums across rows, preserving exact field semantics.
        """
        body = self._get("funds")
        limits = body.get("fund_limit", []) if isinstance(body, dict) else []
        if not isinstance(limits, list) or not limits:
            raise AuthError("venue returned empty fund_limit", code="BAD_RESPONSE")

        available: float | None = None
        used: float = 0.0
        total: float | None = None

        for row in limits:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "") or "").strip().lower()
            row_id = row.get("id")
            eq_amt = _safe_amount(row.get("equityAmount"))

            if row_id == 10 or title == "available balance":
                available = eq_amt
            elif row_id == 2 or title == "utilized amount":
                used = eq_amt
            elif row_id == 1 or title == "total balance":
                total = eq_amt
            elif (row_id == 3 or title == "clear balance") and available is None:
                available = eq_amt

        if available is None and total is not None:
            available = total - used

        if available is None:
            raise AuthError(
                "venue returned no available balance in fund_limit", code="BAD_RESPONSE"
            )

        tot = total if total is not None else (available + used)
        return {"available": available, "used": used, "total": tot, "equity": tot}

    def positions(self) -> list[dict[str, Any]]:
        """Net positions (empty when flat; honest failure otherwise)."""
        return self._read_positions()

    def open_orders(self) -> list[dict[str, Any]]:
        """The venue's WORKING orders (empty when none).

        FYERS' order book holds every order of the trading day — cancelled,
        filled, rejected and expired ones included — so only the orders the
        venue still works are "open".
        """
        return [row for row in self._read_orders() if row["status"] == STATUS_OPEN]

    def reconciliation_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """One account snapshot shared by every per-symbol reconciliation.

        The live service runs one ``LiveSession`` per configured symbol against
        this single authenticated adapter. Each session reconciles the same
        account independently, so a 22-symbol fleet would otherwise issue 44
        REST reads (positions + orders) inside one sub-second loop. FYERS
        rate-limits that burst with HTTP 429, which every later session
        correctly records as a blocking transport error.

        Single-flight: exactly one caller performs the reads while every other
        caller joins the in-flight ticket and waits on its event — bounded and
        WITHOUT holding ``_account_snapshot_lock``. The lock only guards
        bookkeeping, never a network read, so a slow venue response stalls
        neither later reconciles nor cache invalidation behind a mutex.

        A short-lived cache makes every session compare the SAME broker truth
        and lets the venue see at most one pair of reads per window. Failures
        are cached for the same window and re-raised unchanged: a 429 is
        shared, never suppressed or converted into a successful reconcile.
        Generic ``positions()`` / ``open_orders()`` stay uncached so callers
        that need a fresh read still get one.
        """
        if self._account_snapshot_cache_s <= 0:
            return self._read_positions(), self._read_orders()

        now = time.monotonic()
        owner_fetch: _AccountSnapshotFetch | None = None
        joined: _AccountSnapshotFetch | None = None
        with self._account_snapshot_lock:
            cached = self._account_snapshot_cache
            if cached is not None and now - cached.fetched_at < self._account_snapshot_cache_s:
                if cached.error is not None:
                    raise cached.error
                return (
                    [dict(row) for row in cached.positions],
                    [dict(row) for row in cached.orders],
                )

            in_flight = self._account_snapshot_fetch
            if in_flight is None:
                owner_fetch = _AccountSnapshotFetch()
                owner_fetch.epoch = self._account_snapshot_epoch
                self._account_snapshot_fetch = owner_fetch
            else:
                joined = in_flight

        if owner_fetch is None:
            assert joined is not None  # a joiner implies a registered fetch
            if not joined.event.wait(timeout=self._account_snapshot_wait_s):
                self._abandon_account_snapshot_fetch(joined)
                raise BrokerError(
                    "reconciliation snapshot read did not complete within "
                    f"{self._account_snapshot_wait_s:.0f}s",
                    code=ErrorCode.NETWORK_ERROR,
                )
            if joined.error is not None:
                raise joined.error
            return (
                [dict(row) for row in joined.positions],
                [dict(row) for row in joined.orders],
            )

        # Owner: run the venue reads with NO lock held, then publish.
        try:
            positions = self._read_positions()
            orders = self._read_orders()
        except Exception as exc:
            self._publish_account_snapshot(owner_fetch, error=exc)
            raise
        self._publish_account_snapshot(owner_fetch, positions=positions, orders=orders)
        return positions, orders

    def _publish_account_snapshot(
        self,
        fetch: _AccountSnapshotFetch,
        *,
        positions: list[dict[str, Any]] | None = None,
        orders: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Hand the owner's verified result (or failure) to the joiners.

        The TTL cache is only touched while this ticket is still the current
        in-flight fetch AND no account-changing operation invalidated the
        snapshot mid-read: a read that may predate such an operation must
        never shadow post-operation truth with a stale window.
        """
        rows_positions = tuple(dict(row) for row in positions or ())
        rows_orders = tuple(dict(row) for row in orders or ())
        with self._account_snapshot_lock:
            current = self._account_snapshot_fetch is fetch
            if current:
                self._account_snapshot_fetch = None
            fetch.positions = rows_positions
            fetch.orders = rows_orders
            fetch.error = error
            if current and fetch.epoch == self._account_snapshot_epoch:
                self._account_snapshot_cache = _AccountSnapshotCacheEntry(
                    fetched_at=time.monotonic(),
                    positions=rows_positions,
                    orders=rows_orders,
                    error=error,
                )
        fetch.event.set()

    def _abandon_account_snapshot_fetch(self, fetch: _AccountSnapshotFetch) -> None:
        """Deregister a fetch nobody published within the wait bound.

        The next caller becomes a fresh owner instead of joining a dead
        ticket forever. A late publish from the abandoned owner is refused by
        the ``is fetch`` guard, so it can neither clear nor overwrite newer
        truth.
        """
        with self._account_snapshot_lock:
            if self._account_snapshot_fetch is fetch:
                self._account_snapshot_fetch = None

    def _invalidate_account_snapshot(self) -> None:
        """Drop the shared account snapshot after any account-changing operation.

        Only account truth (positions/orders) is invalidated. Historical
        candles are venue history and stay valid until their own 60 s TTL.
        The epoch bump additionally stops an in-flight read that began BEFORE
        the operation from publishing its (possibly pre-operation) snapshot.
        """
        with self._account_snapshot_lock:
            self._account_snapshot_cache = None
            self._account_snapshot_epoch += 1
            self._order_snapshot_cache = None

    def _required_rows(self, body: dict[str, Any], key: str, context: str) -> list[Any]:
        """A missing/!list account field is an unreadable response, not \"flat\"."""
        rows = body.get(key)
        if not isinstance(rows, list):
            raise BrokerError(f"{context} response missing {key}", code=ErrorCode.NETWORK_ERROR)
        return rows

    def _read_positions(self) -> list[dict[str, Any]]:
        body = self._get("positions")
        rows = self._required_rows(body, "netPositions", "positions")
        return [row for row in rows if isinstance(row, dict)]

    def _read_orders(self) -> list[dict[str, Any]]:
        """The venue's order book, normalized (client id, broker id, status).

        Every row keeps the venue's own status AND its venue-neutral name, so
        the reconciliation can match a broker order with the client order id
        it belongs to, and every surface agrees on what "still working" means.
        """
        return self._order_snapshot()

    def _normalized_order(self, row: dict[str, Any]) -> dict[str, Any]:
        """One venue order row in the broker-neutral shape."""
        venue_status = row.get("status")
        return {
            "client_order_id": _client_order_id(row.get("orderTag") or row.get("ordertag") or ""),
            "broker_order_id": str(row.get("id") or row.get("id_fyers") or ""),
            "status": _order_status(venue_status),
            "venue_status": venue_status,
            "symbol": str(row.get("symbol", "")),
            "side": row.get("side", 1),
            "quantity": row.get("qty", 0),
            "filled_qty": _safe_amount(row.get("filledQty")),
            "remaining_qty": row.get("remainingQuantity", 0),
            "traded_price": _safe_amount(row.get("tradedPrice")),
            "order_date_time": str(row.get("orderDateTime", "")),
            "message": str(row.get("message", "") or ""),
        }

    # ── market data (read-only, /data base) ───────────────────────────

    def quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Live quotes for authorized instruments (read-only).

        Served from the vendor's ``/data`` base, NOT ``/api/v3`` (which
        answers 404 for quotes). Rows are returned exactly as the venue
        reports them — nothing is defaulted or synthesised, so an absent
        price stays absent.
        """
        if not symbols:
            return {}
        # Request each symbol in the form the venue quotes, and hand the rows
        # back keyed by what the CALLER asked for, so a registry that stores
        # ``NSE:KAYNES`` still receives its own key back.
        requested = [str(s) for s in symbols]
        venue_for = {req: self.to_venue_symbol(req) for req in requested}
        query = urllib.parse.urlencode({"symbols": ",".join(venue_for[req] for req in requested)})
        body = self._get_data(f"quotes?{query}")
        rows = body.get("d") or body.get("data") or []
        back = {venue: req for req, venue in venue_for.items()}
        out: dict[str, dict[str, Any]] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("n") or row.get("symbol") or "")
            values = row.get("v")
            if symbol and isinstance(values, dict):
                out[back.get(symbol, symbol)] = dict(values)
        return out

    def last_price(self, symbol: str) -> float | None:
        """Venue last price for one symbol, or None when not reported."""
        quote = self.quotes([symbol]).get(symbol, {})
        value = quote.get("lp")
        if value is None:
            return None
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        return price if price > 0 else None

    def history(
        self,
        symbol: str,
        resolution: str,
        range_from_epoch: int,
        range_to_epoch: int,
    ) -> list[tuple[int, float, float, float, float, int]]:
        """Historical candles for one symbol (read-only, `/data/history`).

        Rows come back exactly as the venue reports them, as
        ``(epoch, open, high, low, close, volume)``. Nothing is interpolated,
        forward-filled or re-stamped: a gap in the venue's data stays a gap.

        Requests are parameter-keyed through the shared governor. Concurrent
        callers for the same symbol/interval/window wait for one real fetch
        instead of each hitting the venue; successful pages live for 60 s.
        A failed fetch is re-raised to every waiter and is never cached.
        """
        venue = self.to_venue_symbol(symbol)
        if not venue:
            raise BrokerError("history symbol required", code=ErrorCode.INVALID_SYMBOL)
        resolution_text = str(resolution)

        def _fetch(
            venue_symbol: str,
            from_epoch: int,
            to_epoch: int,
            interval: str,
        ) -> list[tuple[int, float, float, float, float, int]]:
            query = urllib.parse.urlencode(
                {
                    "symbol": venue_symbol,
                    "resolution": interval,
                    "date_format": "0",
                    "range_from": int(from_epoch),
                    "range_to": int(to_epoch),
                    "cont_flag": "1",
                }
            )
            body = self._raw_get_data(f"history?{query}")
            if body.get("s") not in (None, "ok"):
                raise BrokerError(
                    f"history rejected by venue: {body.get('message', '')}",
                    code=ErrorCode.NETWORK_ERROR,
                )
            rows = body.get("candles") or []
            out: list[tuple[int, float, float, float, float, int]] = []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, (list, tuple)) or len(row) < 6:
                    continue
                try:
                    out.append(
                        (
                            int(row[0]),
                            float(row[1]),
                            float(row[2]),
                            float(row[3]),
                            float(row[4]),
                            int(row[5]),
                        )
                    )
                except (TypeError, ValueError):
                    continue
            return out

        return self._gateway.historical(
            _fetch,
            venue,
            int(range_from_epoch),
            int(range_to_epoch),
            resolution_text,
        )

    def market_status(self) -> dict[str, str]:
        """Provider market status per exchange segment (authoritative)."""
        body = self._get_data("marketStatus")
        rows = body.get("marketStatus") or []
        out: dict[str, str] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            key = f"{row.get('segment')}:{row.get('market_type')}"
            out[key] = str(row.get("status") or "")
        return out

    def _get_data(
        self,
        path: str,
        *,
        call_class: CallClass = CallClass.READ,
        bypass_circuit: bool = False,
    ) -> dict[str, Any]:
        """Governed GET under the market-data base; a 404 is surfaced, never hidden."""
        metric_name = f"GET data {path.split('?', 1)[0]}"
        return self._gateway.call(
            metric_name,
            self._raw_get_data,
            path,
            call_class=call_class,
            bypass_circuit=bypass_circuit,
        )

    def _raw_get_data(self, path: str) -> dict[str, Any]:
        url = f"{MARKET_DATA_BASE}/{path}"
        try:
            body = self._http.get_json(url, headers=self._headers())
        except (AuthError, BrokerError):
            # Keep a 429/auth failure classified as such; only generic
            # transport failures are wrapped as a network error.
            raise
        except Exception as exc:
            raise BrokerError(
                f"market data request failed: {exc}", code=ErrorCode.NETWORK_ERROR
            ) from None
        if not isinstance(body, dict):
            raise BrokerError("market data response unreadable", code=ErrorCode.NETWORK_ERROR)
        self._raise_on_venue_error(body, "market data request")
        code = body.get("code")
        if isinstance(code, int) and code >= 400:
            raise BrokerError(
                f"market data rejected by venue (code {code}): {body.get('message', '')}",
                code=ErrorCode.NETWORK_ERROR,
            )
        return body

    # ── trading surface ────────────────────────────────────────────────

    def place_order(
        self, plan: Any, client_order_id: str, idempotency_key: str | None = None
    ) -> str:
        """Submit one order to FYERS API v3. Returns the real broker order id.

        Duplicate ``client_order_id`` adopts existing broker order.
        Timeouts reconcile by tag before raising.
        Structured logging records requests and responses without secrets.

        The order's INTENT decides how the API circuit treats it. A protective
        exit (the execution engine's own stop-protection order) is a
        risk-reducing operation and is always ATTEMPTED, even while the read
        circuit is open. Everything else is a new entry and stays gated by
        both circuits — an entry may only be sent when the venue is healthy.
        """
        _ = idempotency_key
        self._require_usable()
        self._invalidate_account_snapshot()

        if not client_order_id:
            raise BrokerError("client order id required", code=ErrorCode.INVALID_REQUEST)

        protective = self._is_protective_exit(plan, client_order_id)
        call_class = CallClass.RISK_REDUCING if protective else CallClass.ENTRY

        # Idempotency check 1: in-memory cache
        if client_order_id in self._seen_client_ids:
            return self._seen_client_ids[client_order_id]

        # Idempotency check 2: active open orders tag scan. The scan is a
        # read: for a protective exit it is attempted even when the read
        # circuit is open (bypass_circuit), because the duplicate check must
        # not silently disappear — an unreadable order book still fails the
        # protective order, it just gets the chance to be sent.
        adopted = self._find_by_tag(client_order_id, bypass_circuit=protective)
        if adopted is not None:
            self._seen_client_ids[client_order_id] = adopted
            self._remember_order_intent(adopted, "protective" if protective else "entry")
            return adopted

        # Format and validate parameters
        symbol = self._venue_symbol(plan)
        side_val, side_str = self._venue_side(plan)
        qty = self._venue_quantity(plan)
        order_type_val, order_type_str, limit_price, stop_price = self._venue_price_and_type(plan)
        product_type = self._venue_product(plan)
        validity = str(getattr(plan, "time_in_force", "DAY") or "DAY").upper()
        if validity not in ("DAY", "IOC"):
            validity = "DAY"

        # Log ORDER_REQUEST safely (never credentials)
        logger.info(
            "ORDER_REQUEST broker=FYERS mode=LIVE symbol=%s side=%s qty=%d order_type=%s",
            symbol,
            side_str,
            qty,
            order_type_str,
        )

        payload: dict[str, Any] = {
            "symbol": symbol,
            "qty": qty,
            "type": order_type_val,
            "side": side_val,
            "productType": product_type,
            "limitPrice": limit_price,
            "stopPrice": stop_price,
            "validity": validity,
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag": str(client_order_id),
        }

        try:
            resp = self._post("orders/sync", payload, call_class=call_class)
        except Exception as exc:
            # Check if order landed despite network timeout
            resolved = self._resolve_unknown_tag(client_order_id, exc, bypass_circuit=protective)
            if resolved is not None:
                self._invalidate_account_snapshot()
                self._remember_order_intent(resolved, "protective" if protective else "entry")
                return resolved

            logger.error("ORDER_REJECTED broker=FYERS reason=%s", str(exc))
            if isinstance(exc, (BrokerError, CircuitOpenError)):
                raise
            raise self._mapped(exc, "order submission failed") from None

        # Verify FYERS response structure
        # Success: {"s": "ok", "code": 1101, "message": "Order submitted successfully", "id": "..."}
        # Failure: {"s": "error", "code": -..., "message": "..."}
        if not isinstance(resp, dict):
            logger.error("ORDER_REJECTED broker=FYERS reason=malformed broker response")
            raise BrokerError("malformed broker response", code=ErrorCode.NETWORK_ERROR)

        status_flag = resp.get("s")
        if status_flag != "ok":
            msg = str(resp.get("message") or "order rejected by venue")
            code_num = resp.get("code")
            logger.error("ORDER_REJECTED broker=FYERS reason=%s (code=%s)", msg, code_num)
            raise self._error_from_fyers_response(resp)

        broker_id = str(resp.get("id") or "").strip()
        if not broker_id:
            logger.error("ORDER_REJECTED broker=FYERS reason=no broker order id in response")
            raise BrokerError(
                "FYERS response missing broker order id", code=ErrorCode.NETWORK_ERROR
            )

        logger.info(
            "ORDER_RESPONSE broker=FYERS broker_order_id=%s status=ACCEPTED",
            broker_id,
        )

        self._seen_client_ids[client_order_id] = broker_id
        self._remember_order_intent(broker_id, "protective" if protective else "entry")
        self._invalidate_account_snapshot()
        return broker_id

    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order on FYERS API v3.

        A cancellation never increases exposure, so it is a risk-reducing
        operation and is ATTEMPTED even while a circuit is open — for the
        orders this adapter placed (a pending entry or a protective stop).
        A cancellation of an order this adapter did NOT place (unknown
        broker id) is ambiguous, so it stays gated by the circuits.
        """
        self._require_usable()
        if not broker_order_id:
            raise BrokerError("broker order id required", code=ErrorCode.INVALID_REQUEST)
        self._invalidate_account_snapshot()

        payload = {"id": str(broker_order_id)}
        try:
            resp = self._delete(
                "orders/sync",
                payload,
                call_class=self._cancel_call_class(broker_order_id),
            )
        except Exception as exc:
            if self._is_terminal_miss(exc):
                return False
            if isinstance(exc, CircuitOpenError):
                # Refused by the circuit, not by the venue: nothing was sent.
                raise
            raise self._mapped(exc, "cancel failed") from None

        if isinstance(resp, dict) and resp.get("s") == "ok":
            self._invalidate_account_snapshot()
            return True
        if isinstance(resp, dict) and self._is_terminal_miss_msg(str(resp.get("message", ""))):
            self._invalidate_account_snapshot()
            return False
        raise self._error_from_fyers_response(resp)

    def order_state(self, broker_order_id: str) -> dict[str, Any]:
        """The venue's own view of one order — the evidence a cancel needs.

        Venue-neutral, normalized result:

        ``broker_order_id``
            the id that was asked about;
        ``client_order_id``
            our client order id (empty when the venue does not report one);
        ``status``
            the venue's own status, in the shared vocabulary: ``OPEN`` (the
            venue still works the order), ``CANCELLED``, ``FILLED``,
            ``REJECTED``, ``EXPIRED`` or ``UNKNOWN`` (not in the order book);
        ``venue_status``
            the raw status code the venue reported;
        ``listed``
            True while the venue still works the order;
        ``filled_qty``
            the venue's filled quantity for that order.

        The status is the evidence, not the absence of the order: FYERS'
        order book holds the whole trading day, so an order that is not
        listed may simply not be in today's book at all. A status of
        ``CANCELLED`` is what proves a cancellation; ``OPEN`` proves the
        venue still works it.

        Raises when the venue cannot be asked: the answer is then UNKNOWN and
        the caller must not read it as "not working". The check is attempted
        even while the read circuit is open (it is still rate limited),
        because its only purpose is to settle a cancel decision.
        """
        order_id = str(broker_order_id)
        for row in self._order_snapshot(bypass_circuit=True):
            if row["broker_order_id"] != order_id:
                continue
            return {
                "broker_order_id": order_id,
                "client_order_id": str(row.get("client_order_id", "")),
                "status": str(row.get("status", STATUS_UNKNOWN)),
                "venue_status": row.get("venue_status"),
                "listed": row.get("status") == STATUS_OPEN,
                "filled_qty": float(row.get("filled_qty", 0.0) or 0.0),
            }
        return {
            "broker_order_id": order_id,
            "client_order_id": "",
            "status": STATUS_UNKNOWN,
            "venue_status": None,
            "listed": False,
            "filled_qty": 0.0,
        }

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool:
        """Modify an active order on FYERS API v3.

        A modify of a PROTECTIVE order is protective maintenance (it keeps
        or corrects the stop that reduces risk) and is attempted even while a
        circuit is open. A modify of anything else can INCREASE exposure, so
        it stays gated by the circuits like any other new exposure.
        """
        self._require_usable()
        if not broker_order_id:
            raise BrokerError("broker order id required", code=ErrorCode.INVALID_REQUEST)
        self._invalidate_account_snapshot()

        payload: dict[str, Any] = {"id": str(broker_order_id)}
        if quantity is not None:
            if quantity <= 0 or float(quantity) != int(float(quantity)):
                raise BrokerError(
                    "modify quantity must be a positive whole unit",
                    code=ErrorCode.INVALID_REQUEST,
                )
            payload["qty"] = int(float(quantity))

        if price is not None:
            if price <= 0:
                raise BrokerError("modify price must be positive", code=ErrorCode.INVALID_REQUEST)
            payload["limitPrice"] = float(price)
            payload["type"] = 1  # Limit

        call_class = (
            CallClass.RISK_REDUCING
            if self._order_intent.get(str(broker_order_id)) == "protective"
            else CallClass.ENTRY
        )
        try:
            resp = self._put("orders/sync", payload, call_class=call_class)
        except Exception as exc:
            if self._is_terminal_miss(exc):
                return False
            if isinstance(exc, CircuitOpenError):
                # Refused by the circuit, not by the venue: nothing was sent.
                raise
            raise self._mapped(exc, "modify failed") from None

        if isinstance(resp, dict) and resp.get("s") == "ok":
            self._invalidate_account_snapshot()
            return True
        if isinstance(resp, dict) and self._is_terminal_miss_msg(str(resp.get("message", ""))):
            self._invalidate_account_snapshot()
            return False
        raise self._error_from_fyers_response(resp)

    def stream_events(self, consumer_id: str | None = None) -> tuple[dict[str, Any], ...]:
        """Drain real-time WebSocket order/trade events first; fallback to REST poll."""
        self._require_usable()

        # 1. Drain WebSocket order/trade events
        with self._order_event_lock:
            if consumer_id is not None:
                last_seq = self._consumer_cursors.get(consumer_id, 0)
                ws_events = [e for e in self._order_events if e.get("_stream_seq", 0) > last_seq]
                self._consumer_cursors[consumer_id] = self._order_events_seq
            else:
                ws_events = []
                while not self._order_event_queue.empty():
                    try:
                        ws_events.append(self._order_event_queue.get_nowait())
                    except queue.Empty:
                        break
                if not ws_events and self._order_events:
                    ws_events = [dict(e) for e in self._order_events]
                self._order_events.clear()

        if ws_events:
            self._invalidate_account_snapshot()
            return tuple(ws_events)

        # When order WS is connected, updates arrive via WebSocket push — no REST polling.
        if self._enable_order_ws and self._order_ws_state == STATE_CONNECTED:
            return ()

        # 2. Polling fallback if order WS not connected or disabled
        now = time.time()
        if now - self._last_poll_epoch < self._poll_interval_s:
            return ()
        self._last_poll_epoch = now

        try:
            raw_orders = self._order_snapshot()
        except Exception:
            return ()

        snapshot = {order["broker_order_id"]: order for order in raw_orders}
        events: list[dict[str, Any]] = []
        for order_id, order in snapshot.items():
            previous = self._last_orders.get(order_id)
            events.extend(self._diff_order(previous, order))

        self._last_orders = snapshot
        self._cached_snapshot = tuple(snapshot.values())
        if events:
            self._invalidate_account_snapshot()
        return tuple(events)

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        """Live venues ignore reference-price settlement."""

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        return None

    # ── mapping helpers ────────────────────────────────────────────────

    def _venue_symbol(self, plan: Any) -> str:
        sym = str(getattr(plan, "symbol", "") or "").strip()
        if not sym:
            raise BrokerError("order symbol required", code=ErrorCode.INVALID_SYMBOL)
        return self.to_venue_symbol(sym)

    @staticmethod
    def to_venue_symbol(symbol: str) -> str:
        """Canonical instrument → the form the venue actually quotes/trades.

        The venue distinguishes ``NSE:KAYNES`` (no series suffix, resolves to
        nothing) from ``NSE:KAYNES-EQ``. Callers hold whichever form their
        registry uses, so every outbound symbol is normalised HERE, at the
        provider boundary — a bare or unsuffixed symbol otherwise silently
        yields a zero price instead of an error.
        """
        sym = str(symbol or "").strip()
        if not sym:
            return sym
        if ":" not in sym:
            return f"NSE:{sym}-EQ"
        exchange, ticker = sym.split(":", 1)
        if not ticker.endswith("-EQ") and "-" not in ticker:
            return f"{exchange.upper()}:{ticker}-EQ"
        return sym

    def _venue_side(self, plan: Any) -> tuple[int, str]:
        raw_side = str(getattr(plan, "side", "") or "").strip().upper()
        if raw_side in ("1", "BUY"):
            return 1, "BUY"
        if raw_side in ("-1", "SELL", "CLOSE"):
            return -1, "SELL"
        raise BrokerError(f"invalid order side: {raw_side}", code=ErrorCode.INVALID_REQUEST)

    def _venue_quantity(self, plan: Any) -> int:
        try:
            quantity = float(plan.quantity)
        except (TypeError, ValueError):
            raise BrokerError("order quantity unreadable", code=ErrorCode.INVALID_REQUEST) from None
        if quantity <= 0:
            raise BrokerError("order quantity must be positive", code=ErrorCode.INVALID_REQUEST)
        if float(quantity) != int(float(quantity)):
            raise BrokerError(
                f"venue needs whole units, got {quantity}", code=ErrorCode.INVALID_REQUEST
            )
        return int(float(quantity))

    def _venue_price_and_type(self, plan: Any) -> tuple[int, str, float, float]:
        order_type = str(getattr(plan, "order_type", "MARKET") or "MARKET").upper()
        # Look for trigger/stop price on plan
        trigger = getattr(plan, "trigger_price", None)
        if trigger is None:
            trigger = getattr(plan, "stop_price", None)
        # Check bracket for stop price if present
        if trigger is None and hasattr(plan, "bracket"):
            bracket = getattr(plan, "bracket", ())
            if isinstance(bracket, (list, tuple)):
                for leg in bracket:
                    if isinstance(leg, dict) and "stop_price" in leg:
                        trigger = leg.get("stop_price")
                        break

        stop_price = 0.0
        if trigger is not None:
            try:
                stop_price = float(trigger)
            except (TypeError, ValueError):
                stop_price = 0.0

        if order_type in ("1", "LIMIT"):
            try:
                price = float(getattr(plan, "limit_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                raise BrokerError(
                    "limit price unreadable", code=ErrorCode.INVALID_REQUEST
                ) from None
            if price <= 0:
                raise BrokerError("limit price must be positive", code=ErrorCode.INVALID_REQUEST)
            if stop_price > 0:
                return 4, "STOP_LIMIT", round(price, 2), round(stop_price, 2)
            return 1, "LIMIT", round(price, 2), 0.0

        if order_type in ("2", "MARKET"):
            if stop_price > 0:
                return 3, "STOP_MARKET", 0.0, round(stop_price, 2)
            return 2, "MARKET", 0.0, 0.0

        if order_type in ("3", "SLM", "STOP_MARKET"):
            if stop_price <= 0:
                raise BrokerError("stop price required for SL-M", code=ErrorCode.INVALID_REQUEST)
            return 3, "STOP_MARKET", 0.0, round(stop_price, 2)

        if order_type in ("4", "SLL", "STOP_LIMIT"):
            try:
                price = float(getattr(plan, "limit_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                raise BrokerError(
                    "limit price unreadable", code=ErrorCode.INVALID_REQUEST
                ) from None
            if price <= 0 or stop_price <= 0:
                raise BrokerError(
                    "limit price and stop price required for SL-L", code=ErrorCode.INVALID_REQUEST
                )
            return 4, "STOP_LIMIT", round(price, 2), round(stop_price, 2)

        raise BrokerError(
            f"unsupported order type: {order_type}", code=ErrorCode.CAPABILITY_UNSUPPORTED
        )

    def _venue_product(self, plan: Any) -> str:
        product = str(getattr(plan, "product", "") or getattr(plan, "product_type", "")).upper()
        if product in ("CNC", "DELIVERY"):
            return "CNC"
        if product in ("MARGIN", "NRML"):
            return "MARGIN"
        return "INTRADAY"

    def _order_snapshot(self, *, bypass_circuit: bool = False) -> list[dict[str, Any]]:
        """Every order of the trading day, in the broker-neutral shape.

        The venue's order book is the whole day (cancelled, filled, rejected
        and expired orders included) — it is NOT a working-orders list, so
        every consumer decides for itself what "still working" means.
        """
        body = self._get("orders", bypass_circuit=bypass_circuit)
        rows = self._required_rows(body, "orderBook", "orders")
        return [self._normalized_order(row) for row in rows if isinstance(row, dict)]

    def _find_by_tag(
        self, tag: str, *, bypass_circuit: bool = False, terminal_ok: bool = False
    ) -> str | None:
        """Return an active broker order for ``tag``; read failures propagate.

        This is the pre-submit duplicate check. Treating an unreadable order
        book as "no existing order" could submit a second order after a
        timeout; fail closed instead and let the caller decide.

        An order counts as existing unless the venue says it is FINISHED. A
        pending order (status 6) is still worked by the venue, so it is
        found here — the previous status mapping treated it as terminal and
        let a duplicate order through.

        ``terminal_ok`` is for the timeout path only: a FINISHED order still
        proves the tag reached the venue, so the resolver adopts its id
        instead of sending a second order for a possibly-filled first one.

        ``bypass_circuit`` is used only for a risk-reducing order: its
        duplicate check must still run (and still fail loudly), it just may
        not be refused by an already-open read circuit.
        """
        best: str | None = None
        for order in self._order_snapshot(bypass_circuit=bypass_circuit):
            if order["client_order_id"] != tag:
                continue
            if order["status"] in _TERMINAL_STATUS_NAMES:
                if terminal_ok and best is None:
                    best = order["broker_order_id"]
                continue
            return order["broker_order_id"]
        return best

    def _is_protective_exit(self, plan: Any, client_order_id: str) -> bool:
        """True when this order is the execution engine's protective stop.

        The engine builds its stop-protection orders as
        ``intent_id = "sl:<symbol>:<seq>"`` with the matching client order id
        ``"sl:<symbol>:<seq>:o1"`` and a STOP order type carrying the
        strategy's own stop price. All of it must be present:

        * a strategy entry never carries the engine's ``sl:`` intent id;
        * the intent's symbol must be the order's own symbol;
        * a strategy entry is MARKET/LIMIT, not a stop order, and the
          execution planner never sets a stop price;
        * an ambiguous order is treated as an ordinary entry (fail safe).

        A protective stop reduces risk, so it is allowed past an open read
        circuit; a new entry is not.
        """
        intent_id = str(getattr(plan, "intent_id", "") or "")
        match = _PROTECTIVE_INTENT_RE.match(intent_id)
        if match is None:
            return False
        symbol = str(getattr(plan, "symbol", "") or "")
        if symbol and match.group("symbol") != symbol:
            return False
        if str(client_order_id or "") != f"{intent_id}:o1":
            return False
        order_type = str(getattr(plan, "order_type", "") or "").upper()
        if order_type not in _PROTECTIVE_ORDER_TYPES:
            return False
        stop_price = getattr(plan, "stop_price", None)
        if stop_price is None:
            return False
        try:
            return float(stop_price) > 0.0
        except (TypeError, ValueError):
            return False

    def _cancel_call_class(self, broker_order_id: str) -> CallClass:
        """A cancel of a KNOWN order is risk-reducing; an unknown one is not.

        The adapter remembers the intent of every order it placed. Cancelling
        a pending entry removes pending exposure and cancelling a protective
        stop is protective maintenance — neither may be refused by an open
        circuit. A broker id this adapter never placed is ambiguous, so it
        keeps the ordinary (gated) class.
        """
        if str(broker_order_id) in self._order_intent:
            return CallClass.RISK_REDUCING
        return CallClass.ENTRY

    def _remember_order_intent(self, broker_order_id: str, intent: str) -> None:
        """Record what kind of order this broker id is (bounded memory)."""
        self._order_intent[str(broker_order_id)] = intent
        while len(self._order_intent) > _ORDER_INTENT_MEMORY_LIMIT:
            oldest = next(iter(self._order_intent))
            del self._order_intent[oldest]

    def _resolve_unknown_tag(
        self, tag: str, cause: Exception, *, bypass_circuit: bool = False
    ) -> str | None:
        name = type(cause).__name__
        msg = str(cause).lower()
        if "timeout" not in msg and "network" not in name.lower() and "oserror" not in name.lower():
            return None
        try:
            found = self._find_by_tag(tag, bypass_circuit=bypass_circuit, terminal_ok=True)
        except Exception:
            return None
        if found is not None:
            self._seen_client_ids[tag] = found
        return found

    def _diff_order(
        self, previous: dict[str, Any] | None, order: dict[str, Any]
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        tag = order["client_order_id"]
        if not tag:
            self._last_orders[order["broker_order_id"]] = order
            return events

        prev_filled = float(previous.get("filled_qty", 0) or 0) if previous else 0.0
        try:
            filled = float(order.get("filled_qty", 0) or 0)
        except (TypeError, ValueError):
            filled = 0.0

        if filled > prev_filled:
            try:
                price = float(order.get("traded_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            side = "BUY" if order.get("side") == 1 else "SELL"
            partial = order.get("status") not in _TERMINAL_STATUS_NAMES
            events.append(
                {
                    "type": "fill",
                    "client_order_id": tag,
                    "broker_order_id": order["broker_order_id"],
                    "fill": SimpleNamespace(
                        client_order_id=tag,
                        broker_order_id=order["broker_order_id"],
                        symbol=order["symbol"],
                        side=side,
                        fill_qty=filled - prev_filled,
                        fill_price=price,
                        commission=0.0,
                        timestamp=order.get("order_date_time", ""),
                        partial=partial,
                    ),
                }
            )

        # The REST order book uses the SAME status table as the order socket:
        # 6 is PENDING (the venue still works the order), 1 is CANCELLED and
        # 7 is EXPIRED. Reading 6 as "cancelled" made a live pending order
        # look finished and produced a cancel event that never happened.
        status = order.get("status")
        prev_status = previous.get("status") if previous else None
        if status == STATUS_REJECTED and prev_status != STATUS_REJECTED:
            events.append(
                {
                    "type": "reject",
                    "client_order_id": tag,
                    "broker_order_id": order["broker_order_id"],
                    "reason": order.get("message", "venue rejected order"),
                }
            )
        if status == STATUS_CANCELLED and prev_status != STATUS_CANCELLED:
            events.append(
                {
                    "type": "cancel",
                    "client_order_id": tag,
                    "broker_order_id": order["broker_order_id"],
                }
            )
        if status == STATUS_EXPIRED and prev_status != STATUS_EXPIRED:
            events.append(
                {
                    "type": "expired",
                    "client_order_id": tag,
                    "broker_order_id": order["broker_order_id"],
                    "reason": order.get("message", "venue expired the order"),
                }
            )
        return events

    def _require_usable(self) -> None:
        if not self._connected:
            raise BrokerError("FYERS venue not connected", code=ErrorCode.NOT_CONNECTED)
        if not self._app_id or not self._token:
            raise BrokerError(
                "FYERS credentials/session missing", code=ErrorCode.CREDENTIALS_NOT_READY
            )

    def _error_from_fyers_response(self, resp: Any) -> BrokerError:
        if not isinstance(resp, dict):
            return BrokerError("malformed broker response", code=ErrorCode.NETWORK_ERROR)
        msg = str(resp.get("message") or "venue error").strip()
        code = resp.get("code")
        lower_msg = msg.lower()
        if code in (-8, -15, -16, -17) or "unauthorized" in lower_msg or "expired" in lower_msg:
            return BrokerError(f"auth failure: {msg}", code=ErrorCode.AUTHENTICATION_FAILED)
        if "insufficient" in lower_msg or "fund" in lower_msg or "margin" in lower_msg:
            return BrokerError(f"insufficient funds: {msg}", code=ErrorCode.INVALID_REQUEST)
        if "symbol" in lower_msg or ("token" in lower_msg and "invalid token" in lower_msg):
            return BrokerError(f"invalid symbol/token: {msg}", code=ErrorCode.INVALID_SYMBOL)
        if "closed" in lower_msg or "market" in lower_msg:
            return BrokerError(f"market closed: {msg}", code=ErrorCode.INVALID_REQUEST)
        return BrokerError(f"venue error: {msg}", code=ErrorCode.INVALID_REQUEST)

    def _mapped(self, exc: Exception, fallback: str) -> BrokerError:
        if isinstance(exc, BrokerError):
            return exc
        text = str(exc)
        lower_text = text.lower()
        exc_type = type(exc).__name__.lower()
        if "auth" in lower_text or "unauthorized" in lower_text or "401" in text:
            return BrokerError(
                f"authentication failure: {text}", code=ErrorCode.AUTHENTICATION_FAILED
            )
        if (
            "timeout" in lower_text
            or "timeout" in exc_type
            or "network" in lower_text
            or "unreachable" in lower_text
            or "connection" in lower_text
        ):
            return BrokerError(f"network failure: {text}", code=ErrorCode.NETWORK_ERROR)
        return BrokerError(f"{fallback}: {text}", code=ErrorCode.INVALID_REQUEST)

    def _is_terminal_miss(self, exc: Exception) -> bool:
        return self._is_terminal_miss_msg(str(exc))

    def _is_terminal_miss_msg(self, text: str) -> bool:
        lower = text.lower()
        return any(
            token in lower
            for token in ("complete", "cancelled", "rejected", "invalid order", "not found")
        )

    # ── internals ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"{self._app_id}:{self._token}"}

    def _get(
        self,
        path: str,
        *,
        call_class: CallClass = CallClass.READ,
        bypass_circuit: bool = False,
    ) -> dict[str, Any]:
        metric_name = f"GET {path.split('?', 1)[0]}"
        return self._gateway.call(
            metric_name,
            self._raw_get,
            path,
            call_class=call_class,
            bypass_circuit=bypass_circuit,
        )

    def _raw_get(self, path: str) -> dict[str, Any]:
        body = self._http.get_json(f"{API_BASE}/{path}", headers=self._headers())
        if not isinstance(body, dict):
            raise AuthError(f"venue returned an unreadable {path} response", code="NETWORK")
        self._raise_on_venue_error(body, f"FYERS {path} request")
        return body

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        call_class: CallClass = CallClass.ENTRY,
    ) -> dict[str, Any]:
        metric_name = f"POST {path}"
        return self._gateway.call(metric_name, self._raw_post, path, payload, call_class=call_class)

    def _raw_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._http.post_json(f"{API_BASE}/{path}", payload=payload, headers=self._headers())
        if not isinstance(body, dict):
            raise BrokerError(
                f"venue returned an unreadable {path} response", code=ErrorCode.NETWORK_ERROR
            )
        return body

    def _put(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        call_class: CallClass = CallClass.ENTRY,
    ) -> dict[str, Any]:
        metric_name = f"PUT {path}"
        return self._gateway.call(metric_name, self._raw_put, path, payload, call_class=call_class)

    def _raw_put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._http.put_json(f"{API_BASE}/{path}", payload=payload, headers=self._headers())
        if not isinstance(body, dict):
            raise BrokerError(
                f"venue returned an unreadable {path} response", code=ErrorCode.NETWORK_ERROR
            )
        return body

    def _delete(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        call_class: CallClass = CallClass.ENTRY,
    ) -> dict[str, Any]:
        metric_name = f"DELETE {path}"
        return self._gateway.call(
            metric_name, self._raw_delete, path, payload, call_class=call_class
        )

    def _raw_delete(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._http.delete_json(
            f"{API_BASE}/{path}", payload=payload, headers=self._headers()
        )
        if not isinstance(body, dict):
            raise BrokerError(
                f"venue returned an unreadable {path} response", code=ErrorCode.NETWORK_ERROR
            )
        return body

    def _raise_on_venue_error(self, body: dict[str, Any], context: str) -> None:
        """Never let an explicit venue error envelope look like empty account truth."""
        if body.get("s") != "error":
            return
        message = str(body.get("message") or "venue rejected the request")
        lowered = message.lower()
        code = body.get("code")
        if code in _AUTH_ERROR_CODES or any(
            token in lowered
            for token in (
                "token is invalid",
                "invalid token",
                "token expired",
                "unauthorized",
                "session expired",
                "authentication",
            )
        ):
            raise AuthError(f"{context}: {message}", code="AUTH_EXPIRED", http_status=401)
        raise BrokerError(f"{context}: {message}", code=ErrorCode.NETWORK_ERROR)


def _safe_amount(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return number


__all__ = ["FyersSessionAdapter", "ORDER_WS_SUBSCRIPTION", "TRADING_CAPABILITIES"]
