"""Observable primitives — Qt-free Signal/WorkerThread/IntervalTimer parity."""

from __future__ import annotations

import threading
import time
from typing import Any

from core.observable import (
    IntervalTimer,
    Signal,
    WorkerThread,
    pending_events,
    pump_events,
)


class _Probe:
    """Plain host class exposing one signal (descriptor binding exercise)."""

    finished = Signal(str)
    counted = Signal(str, int)

    def __init__(self) -> None:
        self.received: list[tuple[Any, ...]] = []

    def fire(self, text: str) -> None:
        self.finished.emit(text)

    def fire_counted(self, text: str, n: int) -> None:
        self.counted.emit(text, n)


def _wait_until(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump_events()
        if predicate():
            return True
        time.sleep(0.005)
    return False


# ── Signal: same-thread (direct) delivery ─────────────────────────────────


def test_same_thread_emit_delivers_immediately() -> None:
    probe = _Probe()
    out: list[str] = []
    probe.finished.connect(out.append)
    probe.fire("hello")
    assert out == ["hello"]


def test_multiple_handlers_run_in_connect_order() -> None:
    probe = _Probe()
    order: list[str] = []
    probe.finished.connect(lambda _t: order.append("first"))
    probe.finished.connect(lambda _t: order.append("second"))
    probe.finished.connect(lambda _t: order.append("third"))
    probe.fire("x")
    assert order == ["first", "second", "third"]


def test_duplicate_connect_emits_once() -> None:
    probe = _Probe()
    hits: list[str] = []
    handler = lambda _t: hits.append("hit")  # noqa: E731
    probe.finished.connect(handler)
    probe.finished.connect(handler)
    probe.fire("x")
    assert hits == ["hit"]


def test_disconnect_stops_delivery_and_unknown_is_harmless() -> None:
    probe = _Probe()
    hits: list[str] = []
    handler = lambda _t: hits.append("hit")  # noqa: E731
    probe.finished.connect(handler)
    probe.finished.disconnect(handler)
    probe.finished.disconnect(lambda _t: None)  # never connected: no-op
    probe.fire("x")
    assert hits == []


def test_per_instance_signals_are_isolated() -> None:
    a, b = _Probe(), _Probe()
    out_a: list[str] = []
    out_b: list[str] = []
    a.finished.connect(out_a.append)
    b.finished.connect(out_b.append)
    a.fire("a")
    b.fire("b")
    assert out_a == ["a"]
    assert out_b == ["b"]


def test_multityped_signal_payload_preserved() -> None:
    probe = _Probe()
    seen: list[tuple[Any, ...]] = []
    probe.counted.connect(lambda text, n: seen.append((text, n)))
    probe.fire_counted("ABC", 7)
    assert seen == [("ABC", 7)]


def test_failing_handler_does_not_block_later_handlers() -> None:
    probe = _Probe()
    out: list[str] = []

    def _boom(_t: str) -> None:
        raise RuntimeError("boom")

    probe.finished.connect(_boom)
    probe.finished.connect(lambda _t: out.append("after"))
    probe.fire("x")  # must not raise
    assert out == ["after"]


def test_emit_with_no_handlers_is_free() -> None:
    probe = _Probe()
    probe.fire("nobody-listening")
    assert pending_events() == 0


# ── Signal: cross-thread (queued) delivery ────────────────────────────────


def test_worker_thread_emit_queues_until_pumped() -> None:
    probe = _Probe()
    out: list[str] = []
    probe.finished.connect(out.append)
    released = threading.Event()

    def _emit_from_worker() -> None:
        probe.fire("from-worker")
        released.set()

    t = threading.Thread(target=_emit_from_worker, daemon=True)
    t.start()
    assert released.wait(timeout=5.0)
    # Not delivered yet: the emission is queued, not unwound on the worker.
    assert out == []
    assert _wait_until(lambda: out == ["from-worker"])
    t.join(timeout=5.0)


def test_cross_thread_emissions_keep_order() -> None:
    probe = _Probe()
    out: list[str] = []
    probe.finished.connect(out.append)
    done = threading.Event()

    def _emit_many() -> None:
        for i in range(50):
            probe.fire(f"e{i}")
        done.set()

    t = threading.Thread(target=_emit_many, daemon=True)
    t.start()
    assert done.wait(timeout=5.0)
    assert _wait_until(lambda: len(out) == 50)
    assert out == [f"e{i}" for i in range(50)]
    t.join(timeout=5.0)


def test_pump_events_off_main_thread_is_noop() -> None:
    result: list[int] = []

    def _pump_off_thread() -> None:
        result.append(pump_events())

    t = threading.Thread(target=_pump_off_thread, daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert result == [0]


# ── WorkerThread ──────────────────────────────────────────────────────────


class _CountingWorker(WorkerThread):
    def __init__(self) -> None:
        super().__init__()
        self.ticks = 0
        self.body_thread: str | None = None

    def run(self) -> None:
        self.body_thread = threading.current_thread().name
        for _ in range(5):
            self.ticks += 1
            time.sleep(0.005)


def test_worker_thread_runs_body_off_main_thread() -> None:
    worker = _CountingWorker()
    assert not worker.is_running()
    worker.start()
    assert worker.wait(5000)
    assert worker.ticks == 5
    assert worker.body_thread == "_CountingWorker"
    assert not worker.is_running()


def test_worker_thread_restart_after_finish() -> None:
    worker = _CountingWorker()
    worker.start()
    assert worker.wait(5000)
    assert worker.ticks == 5
    worker.start()  # finished thread may restart (QThread parity)
    assert worker.wait(5000)
    assert worker.ticks == 10


def test_worker_thread_double_start_is_one_thread() -> None:
    worker = _CountingWorker()
    worker.start()
    worker.start()  # no-op while alive
    assert worker.wait(5000)
    assert worker.ticks == 5


def test_worker_thread_crash_is_logged_not_raised() -> None:
    class _Crashing(WorkerThread):
        def run(self) -> None:
            raise RuntimeError("worker body failed")

    worker = _Crashing()
    worker.start()
    assert worker.wait(5000)  # thread ended cleanly, exception swallowed+logged
    assert not worker.is_running()


def test_wait_on_never_started_worker_is_immediate() -> None:
    worker = _CountingWorker()
    assert worker.wait(1000)


# ── IntervalTimer ─────────────────────────────────────────────────────────


def test_interval_timer_delivers_ticks_on_main_thread() -> None:
    ticks: list[str] = []

    timer = IntervalTimer(20, lambda: ticks.append(threading.current_thread().name))
    try:
        timer.start()
        assert _wait_until(lambda: len(ticks) >= 2, timeout=10.0)
    finally:
        timer.stop()
    # Every tick ran on the main thread (marshalled, not on the timer thread).
    main = threading.main_thread().name
    assert ticks and all(name == main for name in ticks)


def test_interval_timer_stop_halts_scheduling() -> None:
    ticks: list[int] = []
    timer = IntervalTimer(20, lambda: ticks.append(1))
    timer.start()
    assert _wait_until(lambda: len(ticks) >= 1, timeout=10.0)
    timer.stop()
    time.sleep(0.15)
    pump_events()
    count = len(ticks)
    time.sleep(0.15)
    pump_events()
    assert len(ticks) == count  # no new ticks after stop


def test_interval_timer_start_is_idempotent() -> None:
    ticks: list[int] = []
    timer = IntervalTimer(20, lambda: ticks.append(1))
    timer.start()
    timer.start()
    try:
        assert _wait_until(lambda: len(ticks) >= 1, timeout=10.0)
    finally:
        timer.stop()
