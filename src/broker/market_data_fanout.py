"""Broker-neutral market-data fan-out over one upstream connection.

A venue market-data face is often a *shared* object (``StaticPlugin`` returns
the same FYERS WebSocket adapter for every symbol). Its ``poll()`` was
destructive: whichever session polled first drained every quote, handed the
foreign symbols to its own candle builder, and those were discarded. With
22 sessions polling sequentially at 1 Hz, only the first symbol in the list
could ever receive ticks.

This module is the safe adapter for that shape. One upstream face is pumped
exactly once into a bounded append-only history; every consumer keeps an
independent cursor and reads only its subscribed symbols. Polling order is
therefore irrelevant, a slow consumer cannot steal or destroy another's
events, and overflow is counted per consumer instead of being silent.

Design constraints:

* no broker SDK or execution-domain imports — the face is duck-typed;
* one pump path, bounded memory, non-blocking ``poll``;
* per-consumer drop/lag counters and a machine-readable ``stats()``;
* the upstream is venue-owned: teardown unsubscribes and disconnects it, and
  only an explicitly owning fan-out may ``close()`` it.

A consumer is typically one symbol (the live service creates one candle
provider per symbol), but the API does not assume that.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _symbol_matches(subscribed: str, quote_symbol: str) -> bool:
    """Exact symbol, or the ``-EQ``/bare equivalence the venues emit."""
    if subscribed == quote_symbol:
        return True
    if quote_symbol.endswith("-EQ") and quote_symbol[:-3] == subscribed:
        return True
    if subscribed.endswith("-EQ"):
        return subscribed[:-3] == quote_symbol
    return False


@dataclass
class ConsumerState:
    """One consumer's independent position in the shared history."""

    consumer_id: str
    symbols: set[str] = field(default_factory=set)
    cursor: int = 0
    delivered: int = 0
    dropped: int = 0
    last_delivery_epoch: float | None = None
    paused: bool = False


class MarketDataFanout:
    """Fan one destructively-polled market-data face out to N consumers."""

    def __init__(
        self,
        upstream: Any,
        *,
        max_buffer: int = 50_000,
        clock: Any = None,
        close_upstream_when_empty: bool = False,
    ) -> None:
        if max_buffer < 1:
            raise ValueError("max_buffer must be positive")
        self._upstream = upstream
        self._max_buffer = int(max_buffer)
        self._clock = clock or time.time
        # The face normally belongs to the venue registration (StaticPlugin
        # hands the same object to every run), not to this adapter. Tearing
        # down the fan-out must leave that face reusable; only an owner that
        # explicitly opts in may close it.
        self._close_upstream_when_empty = bool(close_upstream_when_empty)
        self._history: deque[tuple[int, dict[str, Any], float]] = deque(maxlen=self._max_buffer)
        self._seq = 0
        self._consumers: dict[str, ConsumerState] = {}
        self._state_lock = threading.RLock()
        self._pump_lock = threading.Lock()
        self._pump_errors = 0
        self._last_error = ""
        self._overflow_dropped = 0
        self._closed = False
        self._connected = False
        self._connect_lock = threading.Lock()
        self._last_reconnect_epoch = 0.0
        self._reconnect_lock = threading.Lock()

    # ── consumer lifecycle ──────────────────────────────────────────────

    def subscription(
        self, consumer_id: str, symbols: tuple[str, ...] = ()
    ) -> MarketDataSubscription:
        """Create one independent consumer view over the shared stream."""
        if not consumer_id:
            raise ValueError("consumer_id must be non-empty")
        with self._state_lock:
            if self._closed:
                raise RuntimeError("fan-out is closed")
            if consumer_id not in self._consumers:
                self._consumers[consumer_id] = ConsumerState(
                    consumer_id=consumer_id,
                    cursor=self._seq,
                )
        # Symbols supplied at creation must still be forwarded upstream. The
        # old code pre-populated ``state.symbols`` here, so ``subscribe()``
        # below computed an empty delta and the shared face was never told to
        # subscribe at all.
        if symbols:
            self.subscribe(consumer_id, symbols)
        return MarketDataSubscription(self, consumer_id)

    def consumer(self, consumer_id: str) -> ConsumerState:
        with self._state_lock:
            try:
                return self._consumers[consumer_id]
            except KeyError as exc:
                raise KeyError(f"unknown market-data consumer: {consumer_id}") from exc

    def remove_consumer(self, consumer_id: str) -> None:
        """Detach one consumer; stop the shared transport when none remain."""
        should_close = False
        with self._state_lock:
            state = self._consumers.pop(consumer_id, None)
            if state is None:
                return
            still_used = set().union(*(c.symbols for c in self._consumers.values()))
            released = tuple(sorted(state.symbols - still_used))
            should_close = not self._consumers
        if released:
            self._call_upstream("unsubscribe", released)
        if should_close:
            self.close()

    # ── upstream connection ─────────────────────────────────────────────

    def connect(self) -> None:
        """Connect the shared upstream once (idempotent at this layer).

        Unlike teardown calls, a connect failure is propagated: the feed
        provider must fail closed instead of believing it holds a live
        socket because a fan-out swallowed the venue's exception.
        """
        with self._connect_lock:
            with self._state_lock:
                if self._closed:
                    raise RuntimeError("fan-out is closed")
                already = self._connected
                self._connected = True
            if not already:
                try:
                    self._call_upstream_strict("connect")
                except Exception:
                    with self._state_lock:
                        self._connected = False
                    raise

    def disconnect(self) -> None:
        """Disconnect the shared upstream (operator/session teardown)."""
        with self._state_lock:
            self._connected = False
        self._call_upstream("disconnect")

    def pause(self, consumer_id: str) -> None:
        """Pause one consumer without touching any other consumer or the socket."""
        with self._state_lock:
            state = self._consumers.get(consumer_id)
            if state is not None:
                state.paused = True

    def resume(self, consumer_id: str) -> None:
        """Resume one consumer at the live head, without replaying its pause gap.

        Market data is perishable: a consumer that was paused through a
        reconnect must not receive quotes from before the pause as if they
        were current. The skipped rows stay in the shared history for other
        consumers and are counted as lag for this one.
        """
        with self._state_lock:
            state = self._consumers.get(consumer_id)
            if state is None:
                raise KeyError(f"unknown market-data consumer: {consumer_id}")
            target = self._history[-1][0] if self._history else self._seq
            if state.cursor < target:
                state.dropped += target - state.cursor
                state.cursor = target
            state.paused = False

    def reconnect(self) -> None:
        """Single-flight explicit recovery; rapid per-consumer calls coalesce.

        A failed upstream reconnect is reported to the caller and resets the
        coalescing window so the next session can genuinely retry instead of
        being told a socket is live when it is not.
        """
        with self._reconnect_lock:
            now = time.monotonic()
            if now - self._last_reconnect_epoch < 1.0:
                return
            self._last_reconnect_epoch = now
            try:
                self._call_upstream_strict("reconnect")
            except Exception as exc:
                self._last_reconnect_epoch = 0.0
                with self._state_lock:
                    self._connected = False
                    self._last_error = f"upstream reconnect failed: {exc}"
                raise
            self._last_reconnect_epoch = time.monotonic()
        with self._state_lock:
            self._connected = True
            self._last_error = ""

    def close(self) -> None:
        """Release every cursor and stop the shared transport.

        Symbols this fan-out subscribed are released first, so a venue-owned
        face is clean for the next run. By default the face is disconnected,
        not closed: a StaticPlugin hands the same object to the next START.
        """
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._connected = False
            symbols = (
                sorted(set().union(*(state.symbols for state in self._consumers.values())))
                if self._consumers
                else []
            )
            self._consumers.clear()
            self._history.clear()
        if symbols:
            self._call_upstream("unsubscribe", tuple(symbols))
        if self._close_upstream_when_empty:
            self._call_upstream("close")
        else:
            self._call_upstream("disconnect")

    # ── subscriptions ───────────────────────────────────────────────────

    def subscribe(self, consumer_id: str, symbols: tuple[str, ...]) -> None:
        """Subscribe one consumer and forward only newly needed upstream symbols."""
        with self._state_lock:
            state = self._consumers.get(consumer_id)
            if state is None:
                raise KeyError(f"unknown market-data consumer: {consumer_id}")
            before = set().union(*(c.symbols for c in self._consumers.values()))
            state.symbols.update(symbols)
            after = set().union(*(c.symbols for c in self._consumers.values()))
            needed = tuple(sorted(after - before))
        if needed:
            self._call_upstream("subscribe", needed)

    def unsubscribe(self, consumer_id: str, symbols: tuple[str, ...]) -> None:
        """Remove symbols from one consumer; drop upstream only when unneeded."""
        with self._state_lock:
            state = self._consumers.get(consumer_id)
            if state is None:
                return
            state.symbols.difference_update(symbols)
            still_used = set().union(*(c.symbols for c in self._consumers.values()))
            released = tuple(sorted(set(symbols) - still_used))
        if released:
            self._call_upstream("unsubscribe", released)

    # ── delivery ────────────────────────────────────────────────────────

    def poll(self, consumer_id: str) -> tuple[dict[str, Any], ...]:
        """Pump the upstream once, then return only this consumer's new quotes."""
        self._pump()
        with self._state_lock:
            state = self._consumers.get(consumer_id)
            if state is None or state.paused:
                return ()
            oldest_seq = self._history[0][0] if self._history else self._seq + 1
            if state.cursor < oldest_seq - 1:
                missed = oldest_seq - 1 - state.cursor
                state.dropped += missed
                state.cursor = oldest_seq - 1
            quotes: list[dict[str, Any]] = []
            for seq, quote, received_at in self._history:
                if seq <= state.cursor:
                    continue
                symbol = str(quote.get("symbol", "") or "")
                if any(_symbol_matches(wanted, symbol) for wanted in state.symbols):
                    quotes.append(quote)
                    state.delivered += 1
                    state.last_delivery_epoch = received_at
                state.cursor = seq
            if self._history:
                state.cursor = max(state.cursor, self._history[-1][0])
            return tuple(quotes)

    def _pump(self) -> None:
        """One non-blocking drain of the upstream, appended to shared history."""
        with self._pump_lock:
            with self._state_lock:
                if self._closed:
                    return
            try:
                quotes = self._upstream.poll() or ()
            except Exception as exc:  # noqa: BLE001 - upstream is untrusted
                with self._state_lock:
                    self._pump_errors += 1
                    self._last_error = f"upstream poll failed: {exc}"
                return
            if not quotes:
                return
            received_at = self._clock()
            with self._state_lock:
                if self._closed:
                    return
                for quote in quotes:
                    if not isinstance(quote, dict):
                        continue
                    if len(self._history) >= self._max_buffer:
                        self._overflow_dropped += 1
                    self._seq += 1
                    self._history.append((self._seq, quote, received_at))

    # ── health / observability ──────────────────────────────────────────

    def health(self) -> tuple[bool, str]:
        """Upstream transport health only; candle freshness stays downstream."""
        try:
            return self._upstream.health()
        except Exception as exc:  # noqa: BLE001
            return False, f"upstream health failed: {exc}"

    def stats(self) -> dict[str, Any]:
        """Machine-readable delivery/lag counters for the live snapshot."""
        with self._state_lock:
            consumers = {
                cid: {
                    "symbols": sorted(state.symbols),
                    "cursor": state.cursor,
                    "delivered": state.delivered,
                    "dropped": state.dropped,
                    "paused": state.paused,
                    "last_delivery_epoch": state.last_delivery_epoch,
                }
                for cid, state in self._consumers.items()
            }
            return {
                "published": self._seq,
                "buffer_depth": len(self._history),
                "max_buffer": self._max_buffer,
                "overflow_dropped": self._overflow_dropped,
                "pump_errors": self._pump_errors,
                "last_error": self._last_error,
                "closed": self._closed,
                "consumers": consumers,
            }

    def _call_upstream(self, method: str, *args: Any) -> None:
        try:
            self._call_upstream_strict(method, *args)
        except Exception as exc:  # noqa: BLE001 - never let teardown crash a session
            with self._state_lock:
                self._last_error = f"upstream {method} failed: {exc}"
            logger.warning("MARKET_FANOUT_UPSTREAM_%s_FAILED reason=%s", method.upper(), exc)

    def _call_upstream_strict(self, method: str, *args: Any) -> None:
        fn = getattr(self._upstream, method, None)
        if not callable(fn):
            return
        fn(*args)


class MarketDataSubscription:
    """One consumer's face-shaped view; safe to hand to a feed provider."""

    def __init__(self, fanout: MarketDataFanout, consumer_id: str) -> None:
        self._fanout = fanout
        self.consumer_id = consumer_id
        self._closed = False
        self._removed = False

    @property
    def name(self) -> str:
        return f"fanout:{getattr(self._fanout._upstream, 'name', 'market-data')}"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(getattr(self._fanout._upstream, "capabilities", ()))

    @property
    def state(self) -> str:
        return str(getattr(self._fanout._upstream, "state", ""))

    def connect(self) -> None:
        self._fanout.connect()

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        self._fanout.subscribe(self.consumer_id, symbols)

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        self._fanout.unsubscribe(self.consumer_id, symbols)

    def poll(self) -> tuple[dict[str, Any], ...]:
        return self._fanout.poll(self.consumer_id)

    def health(self) -> tuple[bool, str]:
        return self._fanout.health()

    def disconnect(self) -> None:
        """Pause this consumer without disconnecting every other consumer."""
        self._closed = True
        self._fanout.pause(self.consumer_id)

    def reconnect(self) -> None:
        if self._removed:
            raise RuntimeError("market-data subscription is closed")
        self._closed = False
        self._fanout.resume(self.consumer_id)
        self._fanout.reconnect()

    def close(self) -> None:
        if self._removed:
            return
        self._removed = True
        self._closed = True
        self._fanout.remove_consumer(self.consumer_id)

    def stats(self) -> dict[str, Any]:
        return self._fanout.stats()

    def __repr__(self) -> str:
        return f"MarketDataSubscription({self.consumer_id!r})"
