"""Focused regression tests for adapter-owned market WebSocket reconnection.

The FYERS SDK's ``reconnect=True`` loop re-entered through singleton state,
never surfaced a close to the adapter, and reset its retry budget on every
open — thousands of overlapping attempts and orphaned threads. These tests
pin the replacement: single-flight scheduling, bounded backoff, bounded
attempts, generation-guarded callbacks, and one bounded check for a handshake
the vendor swallows silently. Scheduling is deterministic (a fake timer), so
nothing here touches a live venue connection.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from broker.providers.fyers.live_market_data import (
    HANDSHAKE_VERIFY_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
    RECONNECT_DELAYS,
    RECONNECT_STABLE_SECONDS,
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    STATE_ERROR,
    STATE_RECONNECTING,
    FyersLiveMarketData,
)


class _FakeTimer:
    """Records a scheduled retry; the test fires it explicitly."""

    def __init__(self, interval: float, function: Any, args: tuple[Any, ...]) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        assert self.started, "timer was never started"
        assert not self.cancelled, "timer was cancelled"
        self.function(*self.args)


class _Timers:
    """Timer factory handed to the adapter in place of ``threading.Timer``."""

    def __init__(self) -> None:
        self.created: list[_FakeTimer] = []

    def __call__(self, interval: float, function: Any, args: tuple[Any, ...] = ()) -> _FakeTimer:
        timer = _FakeTimer(interval, function, args)
        self.created.append(timer)
        return timer

    @property
    def pending(self) -> _FakeTimer:
        assert self.created, "no retry was scheduled"
        return self.created[-1]


class _Socket:
    """Mock ``FyersDataSocket`` with the same callback surface."""

    def __init__(
        self,
        access_token: str,
        on_message: Any = None,
        on_error: Any = None,
        on_connect: Any = None,
        on_close: Any = None,
        **kwargs: Any,
    ) -> None:
        self.access_token = access_token
        self.reconnect_flag = kwargs.get("reconnect")
        self.on_message = on_message
        self.on_error = on_error
        self.on_connect = on_connect
        self.on_close = on_close
        self.connected = False
        self.close_calls = 0
        self.connect_calls = 0
        self.subscribe_calls: list[list[str]] = []

    def connect(self) -> None:
        self.connect_calls += 1
        self.connected = True
        if self.on_connect:
            self.on_connect()

    def subscribe(self, symbols: list[str], data_type: str = "SymbolUpdate") -> None:  # noqa: ARG002
        self.subscribe_calls.append(list(symbols))

    def close_connection(self) -> None:
        self.close_calls += 1
        self.connected = False
        if self.on_close:
            self.on_close()

    # ── venue simulation ────────────────────────────────────────────────

    def drop(self, code: int = 1006) -> None:
        """The venue closes the socket; the SDK reports ``on_close``."""
        self.connected = False
        if self.on_close:
            self.on_close({"code": code})

    def error(self, message: str = "Connection to remote host was lost.") -> None:
        if self.on_error:
            self.on_error(message)


class _ProbeSocket(_Socket):
    """Socket exposing the vendor's ``is_connected()`` handshake probe."""

    def is_connected(self) -> bool:
        return self.connected


class _SilentHandshakeSocket(_ProbeSocket):
    """Mimics the FYERS SDK swallowing a failed handshake.

    ``connect()`` still calls ``on_connect`` (the vendor calls ``on_open``
    unconditionally after its own connect path), while ``is_connected()``
    stays False and neither ``on_error`` nor ``on_close`` ever fires.
    """

    def connect(self) -> None:
        self.connect_calls += 1
        self.connected = False
        if self.on_connect:
            self.on_connect()


class _Harness:
    def __init__(self, factory: Any = None) -> None:
        self.sockets: list[_Socket] = []
        self.timers = _Timers()
        self._factory = factory
        self.md = FyersLiveMarketData(
            app_id="APP123",
            access_token="TOKEN456",
            socket_factory=self._build,
        )
        self.md._timer_factory = self.timers

    def _build(self, **kwargs: Any) -> Any:
        socket = self._factory(**kwargs) if self._factory is not None else _Socket(**kwargs)
        self.sockets.append(socket)
        return socket

    @property
    def socket(self) -> _Socket:
        assert self.sockets, "no socket was created"
        return self.sockets[-1]

    def connect(self) -> _Socket:
        self.md.connect()
        return self.socket


def test_socket_is_built_without_the_vendor_reconnect_loop() -> None:
    """The adapter owns reconnection; the SDK must not reconnect on its own."""
    h = _Harness()
    socket = h.connect()
    assert socket.reconnect_flag is False
    h.md.close()


def test_one_socket_drop_schedules_exactly_one_retry() -> None:
    h = _Harness()
    socket = h.connect()

    socket.drop()

    assert len(h.timers.created) == 1
    assert h.timers.pending.interval == RECONNECT_DELAYS[0]
    assert h.timers.pending.started
    assert h.md.state == STATE_RECONNECTING
    # Scheduling only: the callback thread must not connect inline.
    assert len(h.sockets) == 1

    # A duplicate close from the same dead socket changes nothing.
    socket.drop()
    assert len(h.timers.created) == 1
    h.md.close()


def test_concurrent_close_callbacks_schedule_one_retry() -> None:
    h = _Harness()
    socket = h.connect()
    barrier = threading.Barrier(8)

    def close() -> None:
        barrier.wait()
        socket.drop()

    threads = [threading.Thread(target=close) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(h.timers.created) == 1
    assert h.md.state == STATE_RECONNECTING
    h.md.close()


def test_error_and_close_callbacks_share_one_retry() -> None:
    """``on_error`` followed by ``on_close`` is one drop, not two attempts."""
    h = _Harness()
    socket = h.connect()

    socket.error()
    socket.drop()

    assert len(h.timers.created) == 1
    assert h.md.state == STATE_RECONNECTING
    h.md.close()


def test_silent_handshake_failure_schedules_exactly_one_retry() -> None:
    """No on_error, no on_close, no vendor socket: one bounded check, one retry."""
    h = _Harness(factory=_SilentHandshakeSocket)
    h.connect()

    assert len(h.sockets) == 1
    assert h.md.state == STATE_CONNECTED  # the vendor's on_open claimed success
    assert len(h.timers.created) == 1
    handshake_check = h.timers.created[0]
    assert handshake_check.interval == HANDSHAKE_VERIFY_SECONDS
    assert handshake_check.started
    assert not handshake_check.cancelled

    handshake_check.fire()

    assert len(h.timers.created) == 2
    retry = h.timers.created[1]
    assert retry.interval == RECONNECT_DELAYS[0]
    assert h.md.state == STATE_RECONNECTING
    assert len(h.sockets) == 1, "scheduling only: the retry needs its own timer"

    # A duplicate failure signal cannot arm a second retry.
    handshake_check.fire()
    assert len(h.timers.created) == 2
    h.md.close()


def test_concurrent_silent_handshake_signals_schedule_one_retry() -> None:
    h = _Harness(factory=_SilentHandshakeSocket)
    h.connect()
    handshake_check = h.timers.created[0]
    socket = h.socket
    barrier = threading.Barrier(8)

    def fail() -> None:
        barrier.wait()
        handshake_check.function(*handshake_check.args)  # the handshake check
        socket.drop()  # and a close callback for the same failure

    threads = [threading.Thread(target=fail) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(h.timers.created) == 2, "check + exactly one retry"
    assert h.md.state == STATE_RECONNECTING
    h.md.close()


def test_disconnect_cancels_silent_handshake_recovery() -> None:
    h = _Harness(factory=_SilentHandshakeSocket)
    h.connect()
    handshake_check = h.timers.created[0]

    h.md.disconnect()

    assert handshake_check.cancelled
    assert h.md.state == STATE_DISCONNECTED
    assert h.md._ws_client is None

    # Even a forced run of the cancelled check must not schedule a retry.
    handshake_check.function(*handshake_check.args)
    assert len(h.timers.created) == 1
    h.md.close()


def test_silent_handshake_retries_exhaust_to_error() -> None:
    h = _Harness(factory=_SilentHandshakeSocket)
    h.connect()

    for _ in range(MAX_RECONNECT_ATTEMPTS):
        h.timers.created[-1].fire()  # handshake check → schedules one retry
        h.timers.created[-1].fire()  # retry → teardown + connect (still silent)

    retries = [t for t in h.timers.created if t.function == h.md._run_reconnect]
    checks = [t for t in h.timers.created if t.function == h.md._verify_handshake]
    assert len(checks) == MAX_RECONNECT_ATTEMPTS + 1, "one check per attempt"
    expected = [RECONNECT_DELAYS[0], RECONNECT_DELAYS[1], RECONNECT_DELAYS[2]]
    expected += [RECONNECT_DELAYS[-1]] * (MAX_RECONNECT_ATTEMPTS - 3)
    assert [t.interval for t in retries] == expected

    h.timers.created[-1].fire()  # the 11th silent handshake finds the cap reached

    assert h.md.state == STATE_ERROR
    assert len(h.timers.created) == 1 + 2 * MAX_RECONNECT_ATTEMPTS, "no 11th attempt"
    healthy, reason = h.md.health()
    assert not healthy
    assert "exhausted" in reason
    h.md.close()


def test_handshake_check_stands_down_when_the_socket_connects_late() -> None:
    h = _Harness(factory=_SilentHandshakeSocket)
    h.connect()
    handshake_check = h.timers.created[0]

    h.socket.connected = True  # the socket finished its handshake after connect() returned
    handshake_check.fire()

    assert len(h.timers.created) == 1
    assert h.md.state == STATE_CONNECTED
    h.md.close()


def test_sockets_without_a_probe_keep_callback_driven_behavior() -> None:
    """A socket that cannot report its handshake is trusted via callbacks."""
    h = _Harness()  # the default fake exposes no is_connected() probe
    h.connect()

    assert len(h.timers.created) == 0
    assert h.md.state == STATE_CONNECTED
    h.md.close()


def test_failed_retry_schedules_the_next_backoff() -> None:
    h = _Harness()
    h.connect().drop()
    assert h.timers.pending.interval == RECONNECT_DELAYS[0]

    h.timers.pending.fire()
    assert len(h.sockets) == 2
    assert h.md.state == STATE_CONNECTED

    # The retry opened and immediately dropped: the budget keeps counting.
    h.socket.drop()
    assert len(h.timers.created) == 2
    assert h.timers.pending.interval == RECONNECT_DELAYS[1]
    h.md.close()


def test_retry_that_cannot_connect_schedules_the_next_backoff() -> None:
    created: list[_Socket] = []

    def factory(**kwargs: Any) -> _Socket:
        if created:
            raise OSError("handshake refused")
        socket = _Socket(**kwargs)
        created.append(socket)
        return socket

    h = _Harness(factory=factory)
    h.connect().drop()

    h.timers.pending.fire()

    assert len(h.timers.created) == 2
    assert h.timers.pending.interval == RECONNECT_DELAYS[1]
    assert h.md.state == STATE_RECONNECTING
    h.md.close()


def test_retry_exhaustion_reaches_error_and_stops() -> None:
    h = _Harness()
    h.connect()

    for _ in range(MAX_RECONNECT_ATTEMPTS):
        h.socket.drop()
        h.timers.pending.fire()

    assert len(h.timers.created) == MAX_RECONNECT_ATTEMPTS
    expected = [RECONNECT_DELAYS[0], RECONNECT_DELAYS[1], RECONNECT_DELAYS[2]]
    expected += [RECONNECT_DELAYS[-1]] * (MAX_RECONNECT_ATTEMPTS - 3)
    assert [timer.interval for timer in h.timers.created] == expected

    # The last retry drops too: the cap is reached, no further attempt.
    h.socket.drop()
    assert len(h.timers.created) == MAX_RECONNECT_ATTEMPTS
    assert h.md.state == STATE_ERROR
    healthy, reason = h.md.health()
    assert not healthy
    assert "exhausted" in reason

    # An explicit connect is the recovery action and restores the budget.
    h.md.connect()
    assert h.md.state == STATE_CONNECTED
    h.md.close()


def test_stable_connection_resets_the_retry_budget() -> None:
    h = _Harness()
    h.connect().drop()
    for _ in range(2):
        h.timers.pending.fire()
        h.socket.drop()

    assert len(h.timers.created) == 3
    assert h.timers.pending.interval == RECONNECT_DELAYS[2]

    h.timers.pending.fire()
    # This connection survived the stability window before it dropped.
    h.md._connected_at = time.monotonic() - (RECONNECT_STABLE_SECONDS + 1)
    h.socket.drop()

    assert h.timers.pending.interval == RECONNECT_DELAYS[0]
    h.md.close()


def test_explicit_disconnect_cancels_pending_retry() -> None:
    h = _Harness()
    socket = h.connect()
    socket.drop()
    pending = h.timers.pending

    h.md.disconnect()

    assert pending.cancelled
    assert h.md.state == STATE_DISCONNECTED
    assert h.md._ws_client is None

    # Late callbacks from the closed socket must not resurrect a retry.
    socket.drop()
    socket.error()
    assert len(h.timers.created) == 1

    # Even if a cancelled timer somehow ran, it must not connect.
    pending.function(*pending.args)
    assert len(h.sockets) == 1
    h.md.close()


def test_retry_tears_down_once_and_connects_once() -> None:
    h = _Harness()
    first = h.connect()
    assert first.connect_calls == 1

    first.drop()
    h.timers.pending.fire()

    second = h.socket
    assert first.close_calls == 1, "one teardown per retry"
    assert second.connect_calls == 1, "one connect per retry"
    assert len(h.sockets) == 2
    h.md.close()


def test_shutdown_cancels_pending_retry() -> None:
    h = _Harness()
    h.connect().drop()
    pending = h.timers.pending

    h.md.close()

    assert pending.cancelled
    assert h.md.state == STATE_DISCONNECTED
    assert h.md._ws_client is None
    pending.function(*pending.args)
    assert len(h.sockets) == 1


def test_stale_callbacks_cannot_overwrite_the_new_connection() -> None:
    h = _Harness()
    old = h.connect()
    old.drop()
    assert h.md.state == STATE_RECONNECTING

    h.md.reconnect()
    new = h.socket
    assert new is not old
    assert h.md.state == STATE_CONNECTED
    scheduled_before = len(h.timers.created)

    # Late callbacks from the dead socket: none may change the live state.
    old.error("Connection to remote host was lost.")
    old.drop()
    old.on_connect()

    assert h.md.state == STATE_CONNECTED
    assert len(h.timers.created) == scheduled_before
    h.md.close()


def test_reconnect_restores_subscriptions_exactly_once() -> None:
    h = _Harness()
    first = h.connect()
    h.md.subscribe(("NSE:SBIN-EQ", "NSE:RELIANCE-EQ"))
    assert len(first.subscribe_calls) == 1
    wanted = set(first.subscribe_calls[0])

    first.drop()
    h.timers.pending.fire()
    retried = h.socket
    assert retried is not first
    assert len(retried.subscribe_calls) == 1, "one SUB frame per connection"
    assert set(retried.subscribe_calls[0]) == wanted

    # An explicit reconnect must not add a second SUB frame either.
    h.md.reconnect()
    explicit = h.socket
    assert len(explicit.subscribe_calls) == 1
    assert set(explicit.subscribe_calls[0]) == wanted
    h.md.close()
