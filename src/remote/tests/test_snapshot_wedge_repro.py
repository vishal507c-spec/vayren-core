"""Deterministic reproduction & regression suite for EC2 snapshot wedge defects.

Covers every failure mode observed in production logs:
1. Client 2 connects after Client 1 / uptime and stalls after ``welcome``.
2. Backend ``get_snapshot()`` stalls indefinitely or times out.
3. Slow/stalled client socket blocks ``_poll_loop`` fanout to other clients.
4. ``LiveTradingService.snapshot()`` drains queued ticks or blocks on ``broker.health()``.
5. ``IntervalTimer`` / ``LiveTradingService.tick()`` builds an unbounded backlog under slow ticks.
6. ``get_server_code_status(force=False)`` blocks snapshot construction on ``git ls-remote``.
7. Unhandled exceptions / transient errors in ``_poll_loop`` kill future publishing.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import app.observable as observable
from app.services.live_trading_service import LiveTradingService
from remote.server_code_status import (
    STATUS_UPDATED,
    clear_cache_for_testing,
    get_server_code_status,
    set_running_sha_for_testing,
)
from remote.tests.test_server import (
    TRADE_TOKEN,
    FakeGateway,
    WsClient,
    authed_client,
    make_live,
    start_server,
)


class _WedgingGateway(FakeGateway):
    """Gateway whose ``get_snapshot()`` blocks until released after warm-up."""

    def __init__(self) -> None:
        super().__init__(make_live())
        self.wedge = False
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.calls = 0

    def get_snapshot(self) -> dict[str, Any]:
        self.calls += 1
        if self.wedge:
            self.entered.set()
            self.gate.wait(timeout=10.0)
        return dict(self.live)


def test_new_client_after_backend_stall_is_not_wedged_after_welcome() -> None:
    """Repro #1: Client 2 connects while backend get_snapshot() is stalled.

    With a warm cached snapshot, Client 2 must still receive welcome + initial
    snapshot and keep answering ping without blocking its read loop for 90s.
    """
    gateway = _WedgingGateway()
    server, _ = start_server(
        gateway,
        poll_interval_s=0.05,
        snapshot_timeout_s=0.5,
        snapshot_cache_max_age_s=30.0,
    )
    c1, _, _ = authed_client(server)
    c2: WsClient | None = None
    try:
        # Wait until at least one poll cycle populates server snapshot cache
        time.sleep(0.15)
        gateway.wedge = True
        assert gateway.entered.wait(timeout=1.5), "backend poll never entered wedged get_snapshot"

        t0 = time.monotonic()
        c2 = WsClient(server.port)
        c2.send_obj({"type": "hello", "v": 1, "payload": {"token": TRADE_TOKEN}})
        welcome = c2.recv_obj(timeout=1.0)
        assert welcome is not None and welcome["type"] == "welcome"

        snapshot = c2.recv_obj(timeout=1.5)
        elapsed = time.monotonic() - t0
        assert snapshot is not None and snapshot["type"] == "snapshot", (
            f"Client 2 wedged after welcome (got {snapshot!r} in {elapsed:.2f}s)"
        )
        assert elapsed < 1.5, f"Initial snapshot took too long ({elapsed:.2f}s)"
        # Because backend was wedged during Client 2's initial snapshot build,
        # the cached fallback must never silently masquerade as fresh.
        assert snapshot["payload"].get("stale") is True, snapshot
        assert "stale_reason" in snapshot["payload"], snapshot

        # Connection must still answer pings while backend stays stalled
        c2.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong: dict[str, Any] | None = None
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            msg = c2.recv_obj(timeout=0.5)
            if msg and msg["type"] == "pong":
                pong = msg
                break
        assert pong is not None, "Client 2 ping starved while backend wedged"

        # Unblock backend and verify fresh state update reaches both clients
        gateway.live = make_live(session_status="RUNNING", start_blockers=[])
        gateway.wedge = False
        gateway.gate.set()

        for client in (c1, c2):
            seen_running = False
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                msg = client.recv_obj(timeout=0.5)
                if not msg:
                    continue
                if (
                    msg["type"] == "state_update"
                    and msg["payload"]["sections"].get("strategy", {}).get("status") == "RUNNING"
                ):
                    seen_running = True
                    break
                if (
                    msg["type"] == "snapshot"
                    and msg["payload"]["snapshot"].get("strategy", {}).get("status") == "RUNNING"
                ):
                    seen_running = True
                    break
            assert seen_running, "Client missed post-recovery state update"
    finally:
        gateway.gate.set()
        if c2 is not None:
            c2.close()
        c1.close()
        server.stop()


def test_cold_snapshot_timeout_returns_explicit_error_not_silent_hang() -> None:
    """When no fresh snapshot exists and backend stalls beyond deadline, send explicit error."""
    gateway = _WedgingGateway()
    gateway.wedge = True
    server, _ = start_server(
        gateway,
        poll_interval_s=0.05,
        snapshot_timeout_s=0.35,
    )
    client = WsClient(server.port)
    try:
        client.send_obj({"type": "hello", "v": 1, "payload": {"token": TRADE_TOKEN}})
        welcome = client.recv_obj(timeout=1.0)
        assert welcome is not None and welcome["type"] == "welcome"

        t0 = time.monotonic()
        msg = client.recv_obj(timeout=1.5)
        elapsed = time.monotonic() - t0
        assert msg is not None and msg["type"] == "error", (
            f"Expected explicit error on snapshot timeout, got {msg!r}"
        )
        assert msg["payload"]["code"] in ("DEADLINE_EXCEEDED", "SERVER_ERROR")
        assert elapsed < 1.5

        # Client socket remains alive and answers ping while awaiting recovery
        client.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong = client.recv_obj(timeout=1.0)
        assert pong is not None and pong["type"] == "pong"
    finally:
        gateway.gate.set()
        client.close()
        server.stop()


def test_slow_client_write_cannot_block_healthy_client() -> None:
    """A stalled TCP/socket send on Client A must never starve Client B."""
    gateway = FakeGateway(make_live())
    server, _ = start_server(
        gateway,
        poll_interval_s=0.04,
        heartbeat_interval_s=0.2,
    )
    c1, _, _ = authed_client(server)
    c2, _, _ = authed_client(server)
    block_send = threading.Event()
    try:
        import remote.server as server_mod

        orig_send_text = server_mod.send_text

        with server._lock:
            conns = list(server._connections.values())
        c1_sock = next(c.sock for c in conns if c.connection_id == "c1")

        def _blocked_send_text(sock: Any, data: bytes) -> None:
            if sock is c1_sock:
                block_send.wait(timeout=3.0)
            orig_send_text(sock, data)

        with patch("remote.server.send_text", side_effect=_blocked_send_text):
            gateway.live = make_live(session_status="RUNNING", start_blockers=[])
            seen_update = False
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                msg = c2.recv_obj(timeout=0.5)
                if msg and msg["type"] in ("state_update", "snapshot"):
                    sec = (
                        msg["payload"]["sections"]
                        if msg["type"] == "state_update"
                        else msg["payload"]["snapshot"]
                    )
                    if sec.get("strategy", {}).get("status") == "RUNNING":
                        seen_update = True
                        break
            assert seen_update, "Healthy client B blocked behind stalled client A"
    finally:
        block_send.set()
        c1.close()
        c2.close()
        server.stop()


class _SlowHealthBroker:
    """Fake adapter whose synchronous health() blocks like a hanging HTTPS request."""

    def __init__(self, unblock: threading.Event) -> None:
        self._unblock = unblock
        self.health_calls = 0
        self.fills: tuple[Any, ...] = ()

    def health(self) -> tuple[bool, str]:
        self.health_calls += 1
        self._unblock.wait(timeout=5.0)
        return True, "ok"

    def cached_health(self) -> tuple[bool, str]:
        return True, "connected (cached)"


class _DummySession:
    """Minimal session stub matching snapshot() attribute reads."""

    def __init__(self, broker: _SlowHealthBroker) -> None:
        from types import SimpleNamespace

        self._broker = broker
        self._risk = SimpleNamespace(kill_switch=SimpleNamespace(is_halted=lambda: False))
        self._lifecycle = SimpleNamespace(state=SimpleNamespace(value="RUNNING"))
        self.ledger = SimpleNamespace(
            all_positions=lambda: (),
            position=lambda _s: SimpleNamespace(flat=True),
        )
        self.engine = SimpleNamespace(snapshot=lambda: {"orders": ()}, open_orders=lambda: ())
        self.reconciliation = SimpleNamespace(
            verdict=lambda: SimpleNamespace(status=SimpleNamespace(value="SYNCED")),
            blocks_live=False,
        )
        self._provider = None
        self._contexts: dict[str, Any] = {}

    def checkpoint(self) -> dict[str, Any]:
        return {}

    def step(self, _now: float) -> int:
        return 0

    def check_tick_invalidation(self) -> None:
        pass

    def eod_sweep(self, _now_ist: Any) -> None:
        pass

    def disarm(self, _reason: str = "") -> None:
        pass


def test_live_service_snapshot_never_drains_ticks_or_blocks_on_broker_health(
    tmp_path: Path,
) -> None:
    """LiveTradingService.snapshot() must remain non-blocking and never drain tick backlog."""
    svc = LiveTradingService(data_dir=tmp_path / "data", strategy_dir=tmp_path / "strats")
    unblock = threading.Event()
    slow_broker = _SlowHealthBroker(unblock)
    session = _DummySession(slow_broker)
    svc._sessions = {"NSE:KAYNES": session}  # type: ignore[dict-item]
    svc._status = "RUNNING"
    svc._desired_status = "RUNNING"
    svc._worker_started = True

    tick_runs = 0

    def _slow_queued_tick() -> None:
        nonlocal tick_runs
        tick_runs += 1
        time.sleep(0.35)

    # Enqueue backlog onto observable._QUEUE as IntervalTimer does during a stall
    for _ in range(5):
        observable._QUEUE.put(((_slow_queued_tick,), ()))

    result: dict[str, Any] = {}
    err: list[Exception] = []

    def _run_snap() -> None:
        try:
            result["snap"] = svc.snapshot()
        except Exception as exc:  # noqa: BLE001
            err.append(exc)

    t0 = time.monotonic()
    thread = threading.Thread(target=_run_snap, daemon=True)
    thread.start()
    thread.join(timeout=0.5)
    elapsed = time.monotonic() - t0
    try:
        assert not thread.is_alive(), "LiveTradingService.snapshot() hung on queue/health"
        assert not err
        assert elapsed < 0.4, f"snapshot took {elapsed:.2f}s"
        assert tick_runs == 0, f"snapshot() illegally drained {tick_runs} queued ticks inline"
        assert slow_broker.health_calls == 0, "snapshot() called blocking broker.health()"
        assert result["snap"]["broker"]["connected"] is True
    finally:
        unblock.set()
        while not observable._QUEUE.empty():
            try:
                observable._QUEUE.get_nowait()
            except Exception:
                break


def test_poll_loop_survives_transient_backend_exception_and_long_churn() -> None:
    """Unhandled exceptions inside backend snapshot or publish must not kill polling."""
    gateway = FakeGateway(make_live())
    real_get = gateway.get_snapshot
    state = {"fail_after": False, "calls": 0}

    def _flaky_snapshot() -> dict[str, Any]:
        state["calls"] += 1
        if state["fail_after"] and 2 <= state["calls"] <= 5:
            raise RuntimeError("transient backend failure")
        return real_get()

    gateway.get_snapshot = _flaky_snapshot  # type: ignore[method-assign]
    server, _ = start_server(gateway, poll_interval_s=0.03)
    client, _welcome, _snapshot = authed_client(server)
    c2: WsClient | None = None
    try:
        state["fail_after"] = True
        time.sleep(0.25)
        gateway.live = make_live(session_status="RUNNING", start_blockers=[])
        seen = False
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            msg = client.recv_obj(timeout=0.5)
            if (
                msg
                and msg["type"] == "state_update"
                and msg["payload"]["sections"].get("strategy", {}).get("status") == "RUNNING"
            ):
                seen = True
                break
        assert seen, "_poll_loop stopped running after backend exception"

        # A fresh second client after long poll churn & transient errors receives
        # a non-stale authoritative snapshot plus valid observability health telemetry.
        c2, _w2, s2 = authed_client(server)
        assert s2["payload"].get("stale") is not True
        assert s2["payload"]["snapshot"].get("strategy", {}).get("status") == "RUNNING"
        c2.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong = c2.recv_obj(timeout=1.5)
        assert pong is not None and pong["type"] == "pong"
        health = pong["payload"]["health"]
        assert health["snapshot_seq"] >= 2
        assert health["last_snapshot_at"] != ""
        assert isinstance(health["poll_delay_ms"], (int, float))
        assert isinstance(health["clients"], list) and len(health["clients"]) >= 2
        c2_diag = next(c for c in health["clients"] if c["connection_id"] == "c2")
        assert c2_diag["authed_at"] != ""
        assert c2_diag["init_snapshot_build_ms"] is not None
    finally:
        if c2 is not None:
            c2.close()
        client.close()
        server.stop()


def test_live_service_timer_coalesces_slow_ticks_without_queue_buildup() -> None:
    """Overlapping slow timer firings must coalesce instead of queuing unboundedly."""
    import app.observable as obs

    executed = 0

    def _slow_tick() -> None:
        nonlocal executed
        executed += 1
        time.sleep(0.12)

    timer = obs.IntervalTimer(15, _slow_tick)
    timer.start()
    try:
        time.sleep(0.35)
        drained = 0
        # Simulate runner main loop draining slowly
        for _ in range(5):
            drained += obs.pump_events(limit=10, allow_any_thread=True)
            time.sleep(0.05)
        assert executed <= 6, f"Expected coalesced slow ticks, ran {executed}"
    finally:
        timer.stop()
        while not obs._QUEUE.empty():
            try:
                obs._QUEUE.get_nowait()
            except Exception:
                break


def test_server_code_status_nonblocking_when_git_remote_hangs() -> None:
    """When cache expires and git ls-remote is slow, force=False must not stall callers."""
    clear_cache_for_testing()
    sha = "a" * 40
    set_running_sha_for_testing(sha)
    started_slow = threading.Event()
    release_slow = threading.Event()
    calls = 0

    def _fetch(_repo: object) -> str:
        nonlocal calls
        calls += 1
        if calls >= 2:
            started_slow.set()
            release_slow.wait(timeout=5.0)
        return sha

    try:
        with (
            patch("remote.server_code_status.fetch_github_origin_sha", side_effect=_fetch),
            patch("remote.server_code_status._read_head_commit_sha", return_value=sha),
        ):
            warm = get_server_code_status(force=False, cache_ttl_s=0.01)
            assert warm["status"] == STATUS_UPDATED
            time.sleep(0.03)
            t0 = time.monotonic()
            cached = get_server_code_status(force=False, cache_ttl_s=0.01)
            elapsed = time.monotonic() - t0
            assert elapsed < 0.1, f"get_server_code_status(force=False) blocked {elapsed:.2f}s"
            assert cached["status"] == STATUS_UPDATED
            assert started_slow.wait(timeout=1.0), "background refresh should still run"
    finally:
        release_slow.set()
        clear_cache_for_testing()


def test_server_stop_and_client_disconnect_do_not_deadlock_under_hung_snapshot() -> None:
    """Cancelling/disconnecting a client or stopping server never deadlocks on hung snapshot."""
    gateway = _WedgingGateway()
    server, _ = start_server(
        gateway,
        poll_interval_s=0.05,
        snapshot_timeout_s=5.0,
        snapshot_cache_max_age_s=0.01,
    )
    gateway.wedge = True
    assert gateway.entered.wait(timeout=1.5)
    client = WsClient(server.port)
    try:
        client.send_obj({"type": "hello", "v": 1, "payload": {"token": TRADE_TOKEN}})
        welcome = client.recv_obj(timeout=1.5)
        assert welcome is not None and welcome["type"] == "welcome"
        # Drop client abruptly while initial snapshot fetch is still blocked in backend
        client.close()
        time.sleep(0.05)

        t0 = time.monotonic()
        server.stop()
        elapsed = time.monotonic() - t0
        assert elapsed < 2.5, f"server.stop() deadlocked on hung backend fetch ({elapsed:.2f}s)"
    finally:
        gateway.gate.set()
        client.close()
        server.stop()
