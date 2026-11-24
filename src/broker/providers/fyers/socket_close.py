"""Bounded teardown for FYERS SDK websocket clients.

The official SDK ``close_connection()`` implementations close the socket and
then join their worker / message / ping threads with **no timeout**. The ping
thread sleeps for 10 seconds between pings, so a vendor close can outlive the
safe-disconnect budget even though the TCP socket is already closed. That is
exactly how a completed teardown was reported as ``disconnect timed out after
10s``.

This helper keeps the SDK's own close path (so vendor state is updated) but:

* neutralises the known join targets with a no-op while the SDK closes, so a
  sleeping ping thread cannot stall the caller;
* runs ``close_connection()`` on a bounded daemon thread;
* force-closes the underlying websocket if the SDK still wedges;
* restores and briefly joins the real threads afterwards.

The socket is the safety-critical resource; the helper never reports a close
it did not attempt. A forced close is logged and returns ``False`` so callers
can distinguish a clean SDK close from a bounded recovery.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

#: Vendor close budget per socket. Safe disconnect tears down two sockets
#: (order + market), so this leaves margin inside the manager's 10s bound.
DEFAULT_CLOSE_TIMEOUT_S = 3.0

#: Attributes used by the official order/data sockets for their join targets.
_THREAD_SLOTS = ("running_thread", "ping_thread", "message_thread", "ws_thread", "t")


class _NoOpJoin:
    """Stand-in for a live thread while the SDK performs its close."""

    def join(self, timeout: float | None = None) -> None:
        pass

    def is_alive(self) -> bool:
        return False


def _is_live_thread(value: Any) -> bool:
    join = getattr(value, "join", None)
    if not callable(join):
        return False
    is_alive = getattr(value, "is_alive", None)
    if callable(is_alive):
        with contextlib.suppress(Exception):
            return bool(is_alive())
    return True


def _neutralise_threads(client: Any) -> dict[str, Any]:
    """Replace live join targets with no-ops; return the real threads."""
    saved: dict[str, Any] = {}
    for name in _THREAD_SLOTS:
        if not hasattr(client, name):
            continue
        value = getattr(client, name)
        if value is None:
            # The order SDK raises AttributeError when running_thread is None;
            # supplying a joinable stand-in keeps its close path intact.
            if name == "running_thread":
                with contextlib.suppress(Exception):
                    setattr(client, name, _NoOpJoin())
            continue
        if not _is_live_thread(value):
            continue
        saved[name] = value
        with contextlib.suppress(Exception):
            setattr(client, name, _NoOpJoin())
    return saved


def _restore_threads(client: Any, saved: dict[str, Any]) -> None:
    for name, value in saved.items():
        with contextlib.suppress(Exception):
            setattr(client, name, value)


def _join_threads(saved: dict[str, Any], timeout_s: float) -> None:
    current = threading.current_thread()
    for name, value in saved.items():
        if value is current:
            continue
        if not _is_live_thread(value):
            continue
        with contextlib.suppress(Exception):
            value.join(timeout=timeout_s)
        if _is_live_thread(value):
            logger.warning("FYERS_WS_THREAD_STILL_ALIVE thread=%s timeout_s=%.1f", name, timeout_s)


def _force_close_private_socket(client: Any) -> bool:
    """Release the SDK's private websocket even when its close path wedges."""
    raw = None
    for key, value in vars(client).items():
        if key.endswith("__ws_object"):
            raw = value
            break
    if raw is not None:

        def _close_raw() -> None:
            for method in ("shutdown", "close"):
                fn = getattr(raw, method, None)
                if callable(fn):
                    with contextlib.suppress(Exception):
                        fn()

        closer = threading.Thread(
            target=_close_raw,
            name="vayren-fyers-raw-socket-close",
            daemon=True,
        )
        closer.start()
        closer.join(timeout=1.0)
    # Detach the dead object so the SDK's is_connected()/ping loop cannot
    # keep treating it as live, and stop its run flag where present.
    for key in list(vars(client)):
        if key.endswith("__ws_object"):
            with contextlib.suppress(Exception):
                setattr(client, key, None)
        elif key.endswith("__ws_run"):
            with contextlib.suppress(Exception):
                setattr(client, key, False)
    return raw is not None


def _is_sdk_none_join_error(exc: BaseException) -> bool:
    if not isinstance(exc, AttributeError):
        return False
    text = str(exc)
    return "'NoneType' object has no attribute 'join'" in text or getattr(exc, "name", "") == "join"


def bounded_close_sdk_socket(
    client: Any,
    *,
    timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S,
    label: str = "FYERS",
) -> bool:
    """Close one FYERS SDK socket within ``timeout_s``.

    Returns ``True`` when the SDK close completed, ``False`` when the helper
    had to force-close the raw websocket. Unrelated exceptions are re-raised;
    the SDK's known ``running_thread is None`` AttributeError is handled.
    """
    if client is None:
        return True
    if hasattr(client, "restart_flag"):
        with contextlib.suppress(Exception):
            client.restart_flag = False

    saved = _neutralise_threads(client)
    done = threading.Event()
    errors: list[BaseException] = []

    def _close() -> None:
        try:
            client.close_connection()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller
            errors.append(exc)
        finally:
            done.set()

    closer = threading.Thread(
        target=_close,
        name=f"vayren-{label.lower()}-ws-close",
        daemon=True,
    )
    closer.start()
    completed = done.wait(timeout=max(0.1, float(timeout_s)))
    _restore_threads(client, saved)

    if not completed:
        forced = _force_close_private_socket(client)
        logger.warning(
            "%s_WS_CLOSE_BOUNDED broker=FYERS timeout_s=%.1f forced=%s",
            label,
            timeout_s,
            forced,
        )
        _join_threads(saved, 0.5)
        return False

    if errors:
        exc = errors[0]
        if _is_sdk_none_join_error(exc):
            logger.info(
                "%s_WS_CLOSE_HANDLED broker=FYERS reason=SDK running_thread None bypassed",
                label,
            )
            _join_threads(saved, 2.0)
            return True
        raise exc

    _join_threads(saved, 2.0)
    return True
