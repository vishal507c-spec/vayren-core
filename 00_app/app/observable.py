"""Application-level observable primitives for services (Signal, WorkerThread, IntervalTimer).

Provides Qt-free, stdlib-only thread marshalling and observable signals:
- :class:`Signal` — observable descriptor with connect/disconnect/emit;
- :class:`WorkerThread` — restartable background worker thread;
- :class:`IntervalTimer` — periodic timer with main-thread event delivery;
- :func:`pump_events` — processes queued cross-thread signal emissions.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import weakref
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("app.observable")

_MAIN_THREAD = threading.main_thread()
_QUEUE: queue.SimpleQueue = queue.SimpleQueue()
_BOUND_ATTR = "__vayren_observable_bound__"
_FALLBACK_STORES: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()


def _is_main_thread() -> bool:
    """True when called from the process main thread (the UI thread)."""
    return threading.current_thread() is _MAIN_THREAD


def _invoke(handlers: tuple[Callable[..., Any], ...], args: tuple[Any, ...]) -> None:
    """Run handlers in order; one failing handler never stops the others."""
    for handler in handlers:
        try:
            handler(*args)
        except Exception:  # noqa: BLE001
            logger.exception("observable handler raised; continuing")


def _bound_store(instance: Any) -> dict[str, Any]:
    """Per-instance store for bound signals."""
    try:
        instance_dict = instance.__dict__
    except Exception:  # pragma: no cover
        return _FALLBACK_STORES.setdefault(instance, {})
    store = instance_dict.get(_BOUND_ATTR)
    if store is None:
        store = {}
        instance_dict[_BOUND_ATTR] = store
    return store


class _BoundSignal:
    """The per-instance handler list behind one :class:`Signal`."""

    __slots__ = ("_handlers", "_lock")

    def __init__(self) -> None:
        self._handlers: list[Callable[..., Any]] = []
        self._lock = threading.Lock()

    def connect(self, handler: Callable[..., Any]) -> None:
        """Subscribe ``handler``; duplicate connects are ignored."""
        if handler is None:
            return
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def disconnect(self, handler: Callable[..., Any]) -> None:
        """Unsubscribe ``handler``; unknown handlers are a no-op."""
        with self._lock, contextlib.suppress(ValueError):
            self._handlers.remove(handler)

    def emit(self, *args: Any) -> None:
        """Deliver ``args`` to every connected handler."""
        with self._lock:
            handlers = tuple(self._handlers)
        if not handlers:
            return
        if _is_main_thread():
            _invoke(handlers, args)
        else:
            _QUEUE.put((handlers, args))

    @property
    def connected(self) -> int:
        with self._lock:
            return len(self._handlers)


class Signal:
    """Class-level observable (descriptor) with connect/emit semantics."""

    __slots__ = ("_types", "_key", "_name")

    def __init__(self, *types: type) -> None:
        self._types = tuple(types)
        self._name = "signal"
        self._key = f"signal_{id(self)}"

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"{owner.__name__}.{name}"
        self._key = f"signal:{owner.__qualname__}:{name}"

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        store = _bound_store(instance)
        bound = store.get(self._key)
        if bound is None:
            bound = _BoundSignal()
            store[self._key] = bound
        return bound


class WorkerThread:
    """Managed worker thread with restart semantics."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None

    def run(self) -> None:
        """Thread body — subclasses override this."""
        raise NotImplementedError("WorkerThread subclasses must override run()")

    def start(self) -> None:
        """Run this worker's body on a fresh thread (no-op while running)."""
        existing = self._thread
        if existing is not None and existing.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_guard,
            name=type(self).__name__,
            daemon=False,
        )
        self._thread.start()

    def _run_guard(self) -> None:
        try:
            self.run()
        except Exception:  # noqa: BLE001
            logger.exception("%s run() raised; thread ending", type(self).__name__)

    def is_running(self) -> bool:
        """True while the worker thread is alive."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def wait(self, timeout_ms: int) -> bool:
        """Join the worker thread; True when it finished within the timeout."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout_ms / 1000.0)
        return not thread.is_alive()


class IntervalTimer:
    """Repeating timer with main-thread delivery."""

    def __init__(self, interval_ms: int, on_tick: Callable[[], None]) -> None:
        self._interval_s = max(1, int(interval_ms)) / 1000.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fired = _BoundSignal()
        self._fired.connect(on_tick)

    def start(self) -> None:
        """Begin ticking (no-op while already ticking)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"IntervalTimer({self._interval_s * 1000:.0f}ms)",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop scheduling ticks."""
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(self._interval_s):
                return
            self._fired.emit()


def pump_events(limit: int = 4096) -> int:
    """Deliver queued cross-thread emissions on the main thread."""
    if not _is_main_thread():
        return 0
    processed = 0
    while processed < limit:
        try:
            handlers, args = _QUEUE.get_nowait()
        except queue.Empty:
            break
        _invoke(handlers, args)
        processed += 1
    return processed


def pending_events() -> int:
    """Queued-but-undelivered cross-thread emissions."""
    return _QUEUE.qsize()
