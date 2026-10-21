"""FyersLiveMarketData — real-time market data WebSocket adapter for FYERS.

Streams live ticks using FYERS API v3 DataSocket and normalizes them to
broker-neutral quote dicts ``{symbol, price, timestamp[, volume, ...]}`` —
the same contract as the Zerodha market-data face, folded exactly once by
the execution ``BrokerFeedProvider`` into its own closed-candle buckets.
No execution-domain objects are built here (provider SDKs never import
execution); tracks transport and heartbeat health. Reconnection is owned by
this adapter (single flight, bounded backoff), never by the vendor socket.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.vocab import BrokerError, ErrorCode

logger = logging.getLogger(__name__)

#: A stream that WAS delivering and then goes quiet this long is stale.
#: A socket that never delivered (market closed) is not stale — that is a
#: different, honest state.
MARKET_DATA_STALE_SECONDS = 120.0

MARKET_DATA_CAPABILITIES: CapabilitySet = capability_set(
    {
        Domain.MARKET_DATA: (
            Caps.MD_CANDLE_STREAM,
            Caps.MD_QUOTES,
            Caps.MD_DEPTH,
            Caps.MD_HEARTBEAT,
        )
    }
)

# FYERS DataSocket connection states
STATE_DISCONNECTED = "DISCONNECTED"
STATE_CONNECTING = "CONNECTING"
STATE_CONNECTED = "CONNECTED"
STATE_RECONNECTING = "RECONNECTING"
STATE_ERROR = "ERROR"

#: Bounded reconnect backoff in seconds; the last value repeats.
RECONNECT_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0, 30.0)
#: Attempts scheduled before the adapter stops and reports STATE_ERROR.
MAX_RECONNECT_ATTEMPTS = 10
#: A connection that stayed up this long resets the retry budget when it drops.
RECONNECT_STABLE_SECONDS = 30.0
#: One-shot check for a handshake the vendor may swallow without a callback.
HANDSHAKE_VERIFY_SECONDS = 5.0


class FyersLiveMarketData:
    """Live FYERS market data adapter implementing MarketDataFace & MarketDataProvider."""

    name = "fyers"

    def __init__(
        self,
        app_id: str,
        access_token: str,
        socket_factory: Callable[..., Any] | None = None,
        max_queue_size: int = 50_000,
    ) -> None:
        self._app_id = str(app_id or "").strip()
        self._token = str(access_token or "").strip()
        self._socket_factory = socket_factory
        self._max_queue_size = max_queue_size
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue_size)
        self._subscriptions: set[str] = set()
        self._timeframe: str = "1m"
        self._state = STATE_DISCONNECTED
        self._last_error_reason: str = ""
        self._ws_client: Any = None
        self._is_closed = False
        # Reconnection is adapter-owned. The vendor socket's own loop
        # (``reconnect=True``) re-enters through singleton state, never tells
        # the adapter that a socket closed, and resets its retry counter on
        # every open — which produced thousands of overlapping attempts.
        self._generation = 0
        self._retry_attempts = 0
        self._retry_timer: Any = None
        self._handshake_timer: Any = None
        self._reconnect_suspended = False
        self._connected_at: float | None = None
        self._reconnect_lock = threading.Lock()
        # Serializes teardown+connect so a timer and an explicit connect can
        # never tear down each other's socket.
        self._attempt_lock = threading.RLock()
        # Indirection so tests can drive the schedule deterministically.
        self._timer_factory: Callable[..., Any] = threading.Timer
        # Data-flow evidence, tracked separately from transport state: a
        # connected socket proves nothing about delivery. These count the
        # messages actually received and when the last one arrived, so a
        # silent stream can never be reported as streaming.
        self._messages_received = 0
        self._last_message_at: float | None = None

    @property
    def capabilities(self) -> CapabilitySet:
        return MARKET_DATA_CAPABILITIES

    @property
    def state(self) -> str:
        return self._state

    def connect(self) -> None:
        """Establish WebSocket connection to FYERS data stream (idempotent).

        An explicit connect is also the recovery action: it clears an
        exhausted retry budget and re-enables automatic reconnection.
        """
        if self._is_closed:
            raise BrokerError("FyersLiveMarketData is closed", code=ErrorCode.NOT_CONNECTED)
        with self._attempt_lock:
            if self._state == STATE_CONNECTED and self._ws_client is not None:
                return

            self._retry_attempts = 0
            self._establish()

    def _establish(self) -> None:
        """Create one socket and connect it; the retry policy belongs to the caller."""
        if not self._app_id or not self._token:
            self._state = STATE_ERROR
            self._last_error_reason = "credentials or session token missing"
            raise BrokerError(self._last_error_reason, code=ErrorCode.CREDENTIALS_NOT_READY)

        self._cancel_pending_timers()
        self._reconnect_suspended = False
        # One connection attempt owns one generation: callbacks from an older
        # socket are ignored instead of overwriting this connection's state.
        self._generation += 1
        generation = self._generation
        self._state = STATE_CONNECTING
        logger.info("MARKET_WS_CONNECTING broker=FYERS")

        # Format token required by FyersDataSocket: appId:token or token
        token_str = (
            f"{self._app_id}:{self._token}"
            if not self._token.startswith(f"{self._app_id}:")
            else self._token
        )

        try:
            self._ws_client = self._build_socket(token_str, generation)
            if hasattr(self._ws_client, "connect"):
                self._ws_client.connect()
        except Exception as exc:
            self._last_error_reason = f"websocket connection failure: {exc}"
            logger.error("MARKET_WS_ERROR broker=FYERS reason=%s", exc)
            self._teardown_socket()
            self._state = STATE_ERROR
            raise BrokerError(self._last_error_reason, code=ErrorCode.NETWORK_ERROR) from exc

        # The vendor's connect() returns without proof of a live socket: it can
        # swallow a failed token/handshake step and still call on_open. When the
        # socket exposes no live connection, arm one bounded handshake check.
        self._watch_handshake(generation)

    def _build_socket(self, token_str: str, generation: int) -> Any:
        """Build the vendor socket with callbacks bound to one generation.

        ``reconnect=False`` is deliberate: the vendor's own loop re-enters
        through singleton state, never surfaces a usable close event, and
        resets its retry budget on every open. The adapter owns reconnection.
        """
        callbacks: dict[str, Any] = {
            "on_message": lambda message: self._on_message(message, _gen=generation),
            "on_error": lambda message: self._on_error(message, _gen=generation),
            "on_connect": lambda: self._on_connect(_gen=generation),
            "on_close": lambda *args: self._on_close(*args, _gen=generation),
        }
        if self._socket_factory is not None:
            return self._socket_factory(access_token=token_str, reconnect=False, **callbacks)

        import importlib

        data_ws_mod = importlib.import_module("fyers_apiv3.FyersWebsocket.data_ws")
        fyers_data_socket_cls = data_ws_mod.FyersDataSocket
        return fyers_data_socket_cls(
            access_token=token_str,
            litemode=False,
            reconnect=False,
            **callbacks,
        )

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:
        """Start streaming for symbols (idempotent)."""
        self._timeframe = timeframe or "1m"
        self.connect()
        if symbols:
            self.subscribe(symbols)

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        """Add symbols to active subscriptions and subscribe via socket."""
        to_add = [self._normalize_symbol(s) for s in symbols if s]
        if not to_add:
            return
        self._subscriptions.update(to_add)

        if self._state == STATE_CONNECTED and self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "subscribe"):
                    self._ws_client.subscribe(symbols=to_add, data_type="SymbolUpdate")
                    logger.info("MARKET_WS_SUBSCRIBED broker=FYERS symbols=%s", to_add)
            except Exception as exc:
                logger.error("MARKET_WS_SUBSCRIBE_FAILED broker=FYERS reason=%s", exc)

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        """Remove symbols from active subscriptions."""
        to_remove = [self._normalize_symbol(s) for s in symbols if s]
        if not to_remove:
            return
        self._subscriptions.difference_update(to_remove)

        if self._state == STATE_CONNECTED and self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "unsubscribe"):
                    self._ws_client.unsubscribe(symbols=to_remove)
                    logger.info("MARKET_WS_UNSUBSCRIBED broker=FYERS symbols=%s", to_remove)
            except Exception as exc:
                logger.error("MARKET_WS_UNSUBSCRIBE_FAILED broker=FYERS reason=%s", exc)

    def poll(self) -> tuple[dict[str, Any], ...]:
        """Drain currently available normalized quote dicts (non-blocking)."""
        quotes: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                quotes.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return tuple(quotes)

    def health(self) -> tuple[bool, str]:
        """(healthy, reason) — transport health AND genuine data flow.

        Transport and delivery are different facts. A connected socket
        that has never delivered a tick (outside market hours, or not
        subscribed) is reported as awaiting data, NOT as streaming, and a
        stream that was live and then went quiet is reported STALE — which
        is a real degradation, unlike the closed-market case.
        """
        if self._is_closed:
            return False, "closed"
        if not self._app_id or not self._token:
            return False, "CREDENTIALS not ready"
        if self._state == STATE_CONNECTED:
            age = self.data_age_s
            if self._messages_received == 0:
                return (
                    True,
                    "connected — no market data received yet (market closed or unsubscribed)",
                )
            if age is not None and age > MARKET_DATA_STALE_SECONDS:
                return False, f"stale — no market data for {int(age)}s"
            return True, f"streaming — {self._messages_received} msgs, last {int(age or 0)}s ago"
        if self._state == STATE_CONNECTING:
            return False, "connecting"
        if self._state == STATE_RECONNECTING:
            return False, "reconnecting"
        if self._state == STATE_ERROR:
            return False, f"error: {self._last_error_reason}"
        return False, "disconnected"

    @property
    def messages_received(self) -> int:
        """Messages actually delivered by the stream (not a connect claim)."""
        return self._messages_received

    @property
    def data_age_s(self) -> float | None:
        """Seconds since the last real message, or None if never received."""
        if self._last_message_at is None:
            return None
        return max(0.0, time.monotonic() - self._last_message_at)

    def disconnect(self) -> None:
        """Drop transport, cancel any pending retry, stay down until connect().

        Subscription memory survives so an explicit reconnect can restore it.
        """
        self._cancel_pending_timers()
        self._reconnect_suspended = True
        with self._attempt_lock:
            self._teardown_socket()
            self._state = STATE_DISCONNECTED
        logger.info("MARKET_WS_DISCONNECTED broker=FYERS")

    def reconnect(self) -> None:
        """Explicit recovery: exactly one teardown/connect, subscriptions restore on connect."""
        self._state = STATE_RECONNECTING
        logger.info("MARKET_WS_RECONNECTING broker=FYERS")
        self.disconnect()
        self.connect()

    # ── Reconnect scheduler (adapter-owned, single flight) ─────────────

    def _schedule_reconnect(self, reason: str) -> None:
        """Schedule exactly one bounded retry; never reconnect inside a callback."""
        with self._reconnect_lock:
            if self._is_closed or self._reconnect_suspended:
                return
            if self._retry_timer is not None:
                return  # single flight: a retry is already pending
            if self._retry_attempts >= MAX_RECONNECT_ATTEMPTS:
                self._state = STATE_ERROR
                self._last_error_reason = (
                    f"reconnect attempts exhausted after {MAX_RECONNECT_ATTEMPTS} "
                    f"attempts: {reason}"
                )
                logger.error(
                    "MARKET_WS_RECONNECT_EXHAUSTED broker=FYERS attempts=%d reason=%s",
                    MAX_RECONNECT_ATTEMPTS,
                    reason,
                )
                return

            delay = RECONNECT_DELAYS[min(self._retry_attempts, len(RECONNECT_DELAYS) - 1)]
            self._retry_attempts += 1
            attempt = self._retry_attempts
            timer = self._timer_factory(delay, self._run_reconnect, (self._generation,))
            timer.daemon = True
            self._retry_timer = timer
            self._state = STATE_RECONNECTING
            logger.warning(
                "MARKET_WS_RECONNECT_SCHEDULED broker=FYERS attempt=%d delay_s=%.0f reason=%s",
                attempt,
                delay,
                reason,
            )
        timer.start()

    def _run_reconnect(self, generation: int) -> None:
        """Timer entry point: one teardown plus one connect attempt."""
        with self._reconnect_lock:
            self._retry_timer = None
            superseded = generation != self._generation
            stopped = self._is_closed or self._reconnect_suspended
        if superseded or stopped:
            return

        with self._attempt_lock:
            if generation != self._generation:
                return  # an explicit connect/reconnect superseded this retry
            self._teardown_socket()
            try:
                self._establish()
            except Exception as exc:
                self._last_error_reason = str(exc)
                self._schedule_reconnect("reconnect failed")

    def _watch_handshake(self, generation: int) -> None:
        """Arm one bounded check for a handshake the vendor may never report.

        The FYERS SDK swallows token/handshake failures inside its own connect
        path: it can call ``on_open`` (so the adapter looks connected) while no
        socket exists, and it emits neither ``on_error`` nor ``on_close``. One
        one-shot probe per attempt turns that silence into a scheduled retry
        instead of a dead stream; sockets that expose no probe keep the
        callback-driven behavior unchanged.
        """
        if self._socket_is_connected():
            return
        with self._reconnect_lock:
            if self._is_closed or self._reconnect_suspended:
                return
            if self._handshake_timer is not None:
                return  # single flight: a check is already pending
            timer = self._timer_factory(
                HANDSHAKE_VERIFY_SECONDS, self._verify_handshake, (generation,)
            )
            timer.daemon = True
            self._handshake_timer = timer
        timer.start()

    def _verify_handshake(self, generation: int) -> None:
        """One-shot check: still no vendor socket means the handshake failed."""
        with self._reconnect_lock:
            self._handshake_timer = None
            superseded = generation != self._generation
            stopped = self._is_closed or self._reconnect_suspended
        if superseded or stopped:
            return
        if self._socket_is_connected():
            return
        self._last_error_reason = "handshake did not complete (no vendor connection)"
        logger.warning("MARKET_WS_HANDSHAKE_INCOMPLETE broker=FYERS")
        self._schedule_reconnect("handshake incomplete")

    def _socket_is_connected(self) -> bool:
        """Vendor truth about the handshake, when the socket can report it.

        ``FyersDataSocket.is_connected()`` is False while no WebSocket object
        exists — including the window after a swallowed token/handshake failure
        in which the SDK still called ``on_open``. Sockets without a probe are
        trusted via callbacks, exactly as before.
        """
        probe = getattr(self._ws_client, "is_connected", None)
        if not callable(probe):
            return True
        try:
            return bool(probe())
        except Exception as exc:
            logger.warning("MARKET_WS_HANDSHAKE_PROBE_WARN broker=FYERS reason=%s", exc)
            return True

    def _recovery_pending(self) -> bool:
        """True while a scheduled retry or a handshake check is still pending."""
        return self._retry_timer is not None or self._handshake_timer is not None

    def _cancel_pending_timers(self) -> None:
        """Cancel any pending retry or handshake check; never leave either armed."""
        with self._reconnect_lock:
            retry, self._retry_timer = self._retry_timer, None
            handshake, self._handshake_timer = self._handshake_timer, None
        for timer in (retry, handshake):
            if timer is not None:
                cancel = getattr(timer, "cancel", None)
                if callable(cancel):
                    cancel()

    def _teardown_socket(self) -> None:
        """Close and forget the current socket, invalidating its callbacks."""
        self._generation += 1
        self._connected_at = None
        client, self._ws_client = self._ws_client, None
        if client is None:
            return
        try:
            if hasattr(client, "close_connection"):
                client.close_connection()
        except Exception as exc:
            logger.warning("MARKET_WS_DISCONNECT_WARN broker=FYERS reason=%s", exc)

    def close(self) -> None:
        """Stop streaming and release all resources."""
        self._is_closed = True
        self.disconnect()
        # Drain remaining queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    # ── Socket callbacks ───────────────────────────────────────────────

    def _on_connect(self, _gen: int | None = None) -> None:
        if _gen is not None and _gen != self._generation:
            return  # stale socket opened after a newer one took ownership
        self._cancel_pending_timers()
        self._state = STATE_CONNECTED
        self._last_error_reason = ""
        self._connected_at = time.monotonic()
        logger.info("MARKET_WS_CONNECTED broker=FYERS")
        # Resubscribe registered symbols exactly once for this connection.
        if self._subscriptions and self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "subscribe"):
                    self._ws_client.subscribe(
                        symbols=list(self._subscriptions), data_type="SymbolUpdate"
                    )
                    logger.info(
                        "MARKET_WS_RESUBSCRIBED broker=FYERS symbols=%s", list(self._subscriptions)
                    )
            except Exception as exc:
                logger.error("MARKET_WS_RESUBSCRIBE_ON_CONNECT_FAILED reason=%s", exc)

    def _on_close(self, *args: Any, _gen: int | None = None) -> None:
        if _gen is not None and _gen != self._generation:
            return  # stale close from a socket that no longer owns the state
        logger.info("MARKET_WS_CLOSED broker=FYERS args=%s", args)
        if self._is_closed or self._reconnect_suspended:
            self._connected_at = None
            if not self._is_closed:
                self._state = STATE_DISCONNECTED
            return

        connected_at = self._connected_at
        self._connected_at = None
        if connected_at is not None and (
            time.monotonic() - connected_at >= RECONNECT_STABLE_SECONDS
        ):
            # Only a connection that actually survived resets the budget; a
            # socket that opens and drops again keeps counting towards the cap.
            self._retry_attempts = 0
        self._schedule_reconnect("socket closed")

    def _on_error(self, message: Any, _gen: int | None = None) -> None:
        if _gen is not None and _gen != self._generation:
            return
        self._last_error_reason = str(message)
        logger.error("MARKET_WS_ERROR broker=FYERS error=%s", message)
        if not self._recovery_pending():
            self._state = STATE_ERROR

    def _on_message(self, message: Any, _gen: int | None = None) -> None:
        """Ingest one raw tick dict from FYERS DataSocket into a normalized quote."""
        if _gen is not None and _gen != self._generation:
            return
        if not isinstance(message, dict):
            return

        normalized_events = self.normalize_tick(message)
        if normalized_events:
            # Delivery evidence: count what actually reached us, so health
            # can distinguish "streaming" from "socket open but silent".
            self._messages_received += len(normalized_events)
            self._last_message_at = time.monotonic()
        for ev in normalized_events:
            try:
                self._queue.put_nowait(ev)
            except queue.Full:
                logger.warning("MARKET_QUEUE_OVERFLOW broker=FYERS dropping oldest tick")
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(ev)
                except Exception:
                    pass

    # ── Payload normalization ──────────────────────────────────────────

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def normalize_tick(self, msg: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """Convert one FYERS raw tick into a normalized quote dict.

        Exactly ONE dict per tick — ``{symbol, price, timestamp[, volume,
        bid/ask legs, OHLC legs]}`` — so the execution ``BrokerFeedProvider``
        folds each tick once into its own closed-candle buckets (emitting a
        trade dict plus a candle dict for the same tick would double-fold).
        A tick without a positive price yields nothing: there is no honest
        quote to fold.
        """
        symbol = str(msg.get("symbol") or "").strip()
        if not symbol:
            return ()
        ts_str = self._extract_timestamp(msg)
        price = self._safe_float(msg.get("ltp"))
        bid = self._safe_float(msg.get("bid_price") or msg.get("bidPrice1"))
        ask = self._safe_float(msg.get("ask_price") or msg.get("askPrice1"))
        if price <= 0:
            if bid > 0 and ask > 0:
                price = (bid + ask) / 2.0
            elif bid > 0:
                price = bid
            elif ask > 0:
                price = ask
        if price <= 0:
            return ()
        try:
            volume = float(msg.get("vol_traded_today") or msg.get("last_traded_qty") or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        quote: dict[str, Any] = {
            "symbol": symbol,
            "price": price,
            "timestamp": ts_str,
            "volume": volume,
        }
        bid_qty = self._safe_float(msg.get("bid_size") or msg.get("bidQty1"))
        ask_qty = self._safe_float(msg.get("ask_size") or msg.get("askQty1"))
        if bid > 0 or ask > 0:
            quote.update(bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty)
        open_p = self._safe_float(msg.get("open_price"))
        high_p = self._safe_float(msg.get("high_price"))
        low_p = self._safe_float(msg.get("low_price"))
        close_p = self._safe_float(msg.get("prev_close_price") or msg.get("ltp"))
        if open_p > 0 and high_p > 0 and low_p > 0 and close_p > 0:
            quote.update(open=open_p, high=high_p, low=low_p, close=close_p)
        return (quote,)

    def _extract_timestamp(self, msg: dict[str, Any]) -> str:
        """Parse tick timestamp to ISO-8601 UTC string."""
        raw_ts = msg.get("last_traded_time") or msg.get("exch_feed_time") or msg.get("timestamp")
        if raw_ts is not None:
            try:
                # FYERS sometimes sends Unix epoch timestamp in seconds
                epoch = float(raw_ts)
                if epoch > 0:
                    dt = datetime.fromtimestamp(epoch, tz=UTC)
                    return dt.isoformat()
            except (ValueError, TypeError, OverflowError):
                pass

        # Fallback to current UTC time
        return datetime.now(UTC).isoformat()

    def _normalize_symbol(self, symbol: str) -> str:
        s = str(symbol or "").strip()
        if not s:
            return ""
        if ":" not in s:
            return f"NSE:{s}-EQ"
        exchange, _, ticker = s.partition(":")
        if not ticker.endswith("-EQ") and "-" not in ticker:
            return f"{exchange}:{ticker}-EQ"
        return s


__all__ = [
    "FyersLiveMarketData",
    "HANDSHAKE_VERIFY_SECONDS",
    "MARKET_DATA_CAPABILITIES",
    "MAX_RECONNECT_ATTEMPTS",
    "RECONNECT_DELAYS",
    "RECONNECT_STABLE_SECONDS",
    "STATE_CONNECTED",
    "STATE_CONNECTING",
    "STATE_DISCONNECTED",
    "STATE_ERROR",
    "STATE_RECONNECTING",
]
