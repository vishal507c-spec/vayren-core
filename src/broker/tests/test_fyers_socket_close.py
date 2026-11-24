"""Bounded FYERS SDK socket close tests (mocks only, no network)."""

from __future__ import annotations

import time

import pytest

from broker.providers.fyers.socket_close import bounded_close_sdk_socket


class _SlowThread:
    def __init__(self, name: str) -> None:
        self.name = name

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float | None = None) -> None:
        if timeout is None:
            time.sleep(10)


def test_bounded_close_neutralises_sleeping_join_targets() -> None:
    joined: list[tuple[str, float | None]] = []

    class _Thread(_SlowThread):
        def join(self, timeout: float | None = None) -> None:
            joined.append((self.name, timeout))
            super().join(timeout)

    class _Client:
        def __init__(self) -> None:
            self.restart_flag = True
            self.ping_thread = _Thread("ping_thread")
            self.t = _Thread("t")
            self.closed = False

        def close_connection(self) -> None:
            self.closed = True
            # The real SDK joins these with no timeout.
            self.ping_thread.join()
            self.t.join()

    client = _Client()
    ping_thread = client.ping_thread
    started = time.monotonic()
    assert bounded_close_sdk_socket(client, timeout_s=1.0, label="TEST") is True
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"bounded close took {elapsed:.2f}s"
    assert client.closed is True
    assert client.restart_flag is False
    assert client.ping_thread is ping_thread, "the real thread must be restored"
    assert ("ping_thread", 2.0) in joined
    assert ("t", 2.0) in joined


def test_bounded_close_force_closes_raw_socket_when_vendor_wedges() -> None:
    class _RawSocket:
        def __init__(self) -> None:
            self.shutdown_called = False
            self.close_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

        def close(self) -> None:
            self.close_called = True

    class _Client:
        def __init__(self) -> None:
            self._FyersOrderSocket__ws_object = _RawSocket()
            self._FyersOrderSocket__ws_run = True

        def close_connection(self) -> None:
            time.sleep(10)

    client = _Client()
    raw = client._FyersOrderSocket__ws_object
    started = time.monotonic()
    assert bounded_close_sdk_socket(client, timeout_s=0.2, label="TEST") is False
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"forced close took {elapsed:.2f}s"
    assert raw.shutdown_called is True
    assert raw.close_called is True
    assert client._FyersOrderSocket__ws_object is None
    assert client._FyersOrderSocket__ws_run is False


def test_bounded_close_reraises_unrelated_attribute_error() -> None:
    class _Client:
        def close_connection(self) -> None:
            raise AttributeError("'Other' object has no attribute 'unrelated'")

    with pytest.raises(AttributeError, match="unrelated"):
        bounded_close_sdk_socket(_Client(), timeout_s=1.0, label="TEST")


def test_bounded_close_supplies_joinable_running_thread_when_sdk_has_none() -> None:
    class _Thread:
        def __init__(self, name: str) -> None:
            self.name = name
            self.joined: list[float | None] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.joined.append(timeout)

    class _Client:
        def __init__(self) -> None:
            self.running_thread = None
            self.t = _Thread("t")
            self.ping_thread = _Thread("ping_thread")
            self.closed = False

        def close_connection(self) -> None:
            self.closed = True
            self.running_thread.join()  # type: ignore[union-attr]

    client = _Client()
    assert bounded_close_sdk_socket(client, timeout_s=1.0, label="TEST") is True
    assert client.closed is True
    assert client.t.joined == [2.0]
    assert client.ping_thread.joined == [2.0]
