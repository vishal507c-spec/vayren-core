"""VAYREN Phase 6 — Production Hardening and Disaster Recovery Tests.

Comprehensive failure-injection and disaster recovery verification:
1.  Primary crash and standby failure detection (§1, §3)
2.  Deterministic 10-step safe failover workflow (§3)
3.  Split-brain protection: monotonic epoch, lease exclusivity, fencing (§4)
4.  Fenced primary order blocking: old primary losing lease is fenced (§4)
5.  Broker truth authority and self-healing adoption during failover (§5)
6.  Missing protective stop-loss halts failover before live readiness (§5)
7.  State checkpointing: SHA-256 cryptographic integrity hash (§6)
8.  Corrupted checkpoint rejection (tamper-detection) (§6, §9)
9.  Incompatible checkpoint version rejection (§6, §9)
10. Checkpoint zero-secrets sanitization (§6, §13)
11. Deployment locking: live running executor rejects deployment lock (§8)
12. Deployment version compatibility validation (§8)
13. Resource protection: CPU, RAM, disk, and broker latency exhaustion triggers (§12)
14. Observability: HighAvailabilitySnapshot and ControlPlane RuntimeSnapshot (§10)
15. Audit trail: ExecutionJournal records HA lifecycle events with zero secrets (§11)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from execution.control_plane import (
    ControlPlaneAuth,
    ControlPlaneController,
    EventStreamManager,
)
from execution.disaster_recovery import (
    CheckpointCorruptedError,
    CheckpointManager,
    CheckpointVersionMismatchError,
    DeploymentSafetyError,
    DeploymentSafetyManager,
    DistributedLeaseCoordinator,
    ExecutionLease,
    ExecutorRole,
    FailoverCoordinator,
    HighAvailabilitySnapshot,
    ResourceMetrics,
    ResourceMonitor,
    SplitBrainGuard,
    StateCheckpoint,
)
from execution.engine import ExecutionEngine
from execution.journal import ExecutionJournal
from execution.models.position import Position
from execution.portfolio.ledger import PositionLedger
from execution.reconciliation import (
    BrokerOrderTruth,
    BrokerPositionTruth,
    BrokerTruthSnapshot,
    ReconciliationPipeline,
)
from execution.recovery import (
    ExecutionState,
    ExecutionStateMachine,
)
from execution.safety import (
    SlProtectionTracker,
    build_safety_snapshot,
)

# ── 1. Distributed Lease & Split-Brain Protection (§4) ───────────────────────


def test_lease_acquisition_and_monotonic_epoch() -> None:
    """Lease coordinator grants lease with monotonic epoch and tracks highest epoch."""
    journal = ExecutionJournal()
    coord = DistributedLeaseCoordinator(default_ttl_seconds=10.0, journal=journal)

    # Initial acquisition by primary
    lease_1 = coord.acquire_lease("primary-ec2-a", proposed_epoch=1, current_time=100.0)
    assert lease_1 is not None
    assert lease_1.holder_id == "primary-ec2-a"
    assert lease_1.epoch == 1
    assert lease_1.is_valid(current_time=105.0) is True
    assert lease_1.is_valid(current_time=111.0) is False

    # Attempt with stale or duplicate epoch must be rejected (split-brain guard)
    lease_stale = coord.acquire_lease("standby-ec2-b", proposed_epoch=1, current_time=115.0)
    assert lease_stale is None
    alerts = coord.journal.of_kind("SPLIT_BRAIN_ATTEMPT")
    assert len(alerts) == 1
    assert alerts[0].payload["proposed_epoch"] == 1


def test_lease_contention_blocks_unexpired_usurpation() -> None:
    """Standby cannot usurp an active unexpired lease held by primary."""
    coord = DistributedLeaseCoordinator(default_ttl_seconds=10.0)
    coord.acquire_lease("primary-ec2-a", proposed_epoch=1, current_time=100.0)

    # Standby proposes epoch 2 while primary lease is still valid (100.0 -> 110.0)
    contention = coord.acquire_lease("standby-ec2-b", proposed_epoch=2, current_time=105.0)
    assert contention is None
    entries = coord.journal.of_kind("LEASE_CONTENTION")
    assert len(entries) == 1
    assert entries[0].payload["candidate_id"] == "standby-ec2-b"


def test_lease_renewal_and_graceful_release() -> None:
    """Current holder can renew lease before expiry or release it gracefully."""
    coord = DistributedLeaseCoordinator(default_ttl_seconds=10.0)
    coord.acquire_lease("primary-ec2-a", proposed_epoch=1, current_time=100.0)

    # Renewal extends lease expiry
    renewed = coord.renew_lease("primary-ec2-a", epoch=1, ttl_seconds=15.0, current_time=108.0)
    assert renewed is True
    assert coord.current_lease is not None
    assert coord.current_lease.expires_at == 108.0 + 15.0

    # Wrong holder or epoch cannot renew
    assert coord.renew_lease("rogue-node", epoch=1, current_time=109.0) is False
    assert coord.renew_lease("primary-ec2-a", epoch=99, current_time=109.0) is False

    # Graceful release
    assert coord.release_lease("primary-ec2-a", epoch=1) is True
    assert coord.current_lease is None


def test_split_brain_guard_fences_old_primary() -> None:
    """SplitBrainGuard fences executor immediately if lease expires or epoch mismatches."""
    guard = SplitBrainGuard(executor_id="primary-ec2-a")
    guard.set_role(ExecutorRole.ACTIVE, epoch=1)

    valid_lease = ExecutionLease(
        holder_id="primary-ec2-a",
        epoch=1,
        fencing_token=1,
        acquired_at=100.0,
        expires_at=110.0,
    )

    # Action permitted while lease valid and epoch matches
    assert guard.validate_action(action_epoch=1, lease=valid_lease, current_time=105.0) is True

    # Lease expires -> guard transitions to FENCED
    assert guard.validate_action(action_epoch=1, lease=valid_lease, current_time=115.0) is False
    assert guard.is_fenced is True
    assert guard.role == ExecutorRole.FENCED

    # Subsequent actions permanently blocked
    assert guard.validate_action(action_epoch=1, lease=valid_lease, current_time=105.0) is False


# ── 2. Deterministic 10-Step Safe Failover (§3) ──────────────────────────────


def test_ten_step_failover_clean_flow_armed() -> None:
    """Standby executes all 10 failover steps cleanly and resumes live trading."""
    journal = ExecutionJournal()
    leases = DistributedLeaseCoordinator(journal=journal)
    # Primary had lease epoch 1 which expired at 100.0
    leases._highest_epoch = 1

    rec_pipeline = ReconciliationPipeline(journal=journal)
    failover = FailoverCoordinator(
        lease_coordinator=leases,
        reconciliation_pipeline=rec_pipeline,
        journal=journal,
    )

    guard = SplitBrainGuard("standby-ec2-b")
    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=100_000.0)
    ledger.adopt_position(Position(symbol="NSE:INFY-EQ", quantity=50.0, avg_price=1450.0))

    sl_tracker = SlProtectionTracker()
    sl_tracker.on_fill("intent-1", "NSE:INFY-EQ", "BUY", 1450.0, 1400.0)
    sl_tracker.confirm_sl("intent-1", "venue-sl-1")

    sm = ExecutionStateMachine(initial=ExecutionState.READY)

    truth = BrokerTruthSnapshot(
        positions=(BrokerPositionTruth(symbol="NSE:INFY-EQ", quantity=50.0),),
        orders=(
            BrokerOrderTruth(
                order_id="venue-sl-1",
                client_order_id="cid-sl-1",
                symbol="NSE:INFY-EQ",
                side="SELL",
                quantity=50.0,
                filled_quantity=0.0,
                status="OPEN",
                is_protective_sl=True,
            ),
        ),
    )

    result = failover.execute_failover(
        standby_id="standby-ec2-b",
        guard=guard,
        engine=engine,
        ledger=ledger,
        sl_tracker=sl_tracker,
        state_machine=sm,
        broker_fetcher=lambda: truth,
        primary_liveness_check=lambda: False,  # Primary confirmed dead
        operator_armed=True,
        current_time=120.0,
    )

    assert result.success is True
    assert result.final_role == ExecutorRole.ACTIVE
    assert sm.state == ExecutionState.RUNNING
    assert sm.allows_live_trading is True
    assert len(result.steps) == 10
    step_names = [s.step_name for s in result.steps]
    expected_steps = [
        "DETECT",
        "FREEZE_NEW_ORDERS",
        "VERIFY_PRIMARY_IS_REALLY_DEAD",
        "ACQUIRE_EXECUTION_LEASE",
        "FETCH_BROKER_TRUTH",
        "RECONCILE",
        "VERIFY_POSITIONS",
        "VERIFY_SL",
        "VERIFY_RISK",
        "ONLY_THEN_READY",
    ]
    assert step_names == expected_steps
    assert all(s.passed for s in result.steps)

    # Check journal has recorded failover lifecycle
    assert len(journal.of_kind("FAILOVER_STARTED")) == 1
    assert len(journal.of_kind("FAILOVER_COMPLETED")) == 1


def test_failover_aborts_if_primary_still_alive() -> None:
    """Failover aborts at Step 3 if primary is still alive and holding active lease."""
    journal = ExecutionJournal()
    leases = DistributedLeaseCoordinator(journal=journal)
    leases.acquire_lease("primary-ec2-a", proposed_epoch=1, current_time=100.0)

    failover = FailoverCoordinator(leases, ReconciliationPipeline(), journal)
    guard = SplitBrainGuard("standby-ec2-b")
    sm = ExecutionStateMachine(initial=ExecutionState.READY)

    result = failover.execute_failover(
        standby_id="standby-ec2-b",
        guard=guard,
        engine=ExecutionEngine(),
        ledger=PositionLedger(starting_capital=50_000.0),
        sl_tracker=SlProtectionTracker(),
        state_machine=sm,
        broker_fetcher=lambda: BrokerTruthSnapshot(),
        primary_liveness_check=lambda: True,  # Primary is still responding!
        current_time=105.0,  # Lease still valid
    )

    assert result.success is False
    assert guard.role == ExecutorRole.STANDBY
    assert len(result.steps) == 3
    assert result.steps[2].passed is False
    assert len(journal.of_kind("FAILOVER_BLOCKED")) == 1


def test_failover_blocks_if_protective_sl_missing_on_venue() -> None:
    """Failover halts at Step 8 and transitions to BLOCKED if open position lacks SL."""
    leases = DistributedLeaseCoordinator()
    leases._highest_epoch = 1
    failover = FailoverCoordinator(leases, ReconciliationPipeline())

    guard = SplitBrainGuard("standby-ec2-b")
    sm = ExecutionStateMachine(initial=ExecutionState.READY)
    ledger = PositionLedger(starting_capital=50_000.0)

    # Broker truth holds position +50 INFY but NO stop loss orders
    truth_unprotected = BrokerTruthSnapshot(
        positions=(BrokerPositionTruth(symbol="NSE:INFY-EQ", quantity=50.0),),
        orders=(),
    )

    result = failover.execute_failover(
        standby_id="standby-ec2-b",
        guard=guard,
        engine=ExecutionEngine(),
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        state_machine=sm,
        broker_fetcher=lambda: truth_unprotected,
        primary_liveness_check=lambda: False,
        operator_armed=True,
        current_time=120.0,
    )

    assert result.success is False
    assert guard.role == ExecutorRole.BLOCKED
    assert sm.allows_live_trading is False
    assert result.steps[7].step_name == "VERIFY_SL"
    assert result.steps[7].passed is False


# ── 3. Checkpointing & Cryptographic Integrity (§6, §9) ──────────────────────


def test_checkpoint_roundtrip_with_sha256_checksum(tmp_path: Path) -> None:
    """Checkpoint creates and verifies cryptographic SHA-256 integrity hash."""
    mgr = CheckpointManager(storage_dir=tmp_path)

    checkpoint = StateCheckpoint.create(
        checkpoint_id="chk-1001",
        epoch=3,
        executor_id="primary-ec2-a",
        role="ACTIVE",
        execution_state="READY",
        positions=[{"symbol": "NSE:INFY-EQ", "quantity": 50}],
        orders=[{"client_order_id": "cid-1", "symbol": "NSE:INFY-EQ"}],
        sl_records=[{"symbol": "NSE:INFY-EQ", "status": "PROTECTED"}],
        reconciliation_summary={"matched": True},
        last_event_seq=42,
    )

    saved_path = mgr.save(checkpoint)
    assert saved_path.exists()

    loaded = mgr.load(saved_path)
    assert loaded.checkpoint_id == "chk-1001"
    assert loaded.epoch == 3
    assert loaded.executor_id == "primary-ec2-a"
    assert loaded.checksum == checkpoint.checksum
    assert len(loaded.positions) == 1


def test_corrupted_checkpoint_rejected_by_integrity_check(tmp_path: Path) -> None:
    """Tampering with checkpoint file invalidates checksum and raises CheckpointCorruptedError."""
    mgr = CheckpointManager(storage_dir=tmp_path)

    chk = StateCheckpoint.create(
        checkpoint_id="chk-tamper",
        epoch=1,
        executor_id="node-1",
        role="ACTIVE",
        execution_state="READY",
        positions=[{"symbol": "NSE:INFY-EQ", "quantity": 10}],
        orders=[],
        sl_records=[],
        reconciliation_summary={"matched": True},
        last_event_seq=5,
    )
    p = mgr.save(chk)

    # Tamper with file on disk
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    data["positions"][0]["quantity"] = 999999  # Malicious injection
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(CheckpointCorruptedError):
        mgr.load(p)


def test_incompatible_checkpoint_version_rejected(tmp_path: Path) -> None:
    """Incompatible schema version raises CheckpointVersionMismatchError."""
    mgr = CheckpointManager(storage_dir=tmp_path)
    chk = StateCheckpoint.create(
        checkpoint_id="chk-v2",
        epoch=1,
        executor_id="node-1",
        role="ACTIVE",
        execution_state="READY",
        positions=[],
        orders=[],
        sl_records=[],
        reconciliation_summary={},
        last_event_seq=1,
        schema_version="2.0.0",  # Unsupported version
    )
    p = mgr.save(chk)

    with pytest.raises(CheckpointVersionMismatchError):
        mgr.load(p)


def test_checkpoint_zero_secrets_sanitization() -> None:
    """Checkpoint creation defensively filters tokens, passwords, and secrets."""
    chk = StateCheckpoint.create(
        checkpoint_id="chk-sec",
        epoch=1,
        executor_id="node-1",
        role="ACTIVE",
        execution_state="READY",
        positions=[{"symbol": "NSE:INFY-EQ", "quantity": 10, "api_token": "secret_abc"}],
        orders=[{"client_order_id": "cid-1", "user_secret": "pass_123"}],
        sl_records=[],
        reconciliation_summary={},
        last_event_seq=1,
    )

    as_str = json.dumps(chk.to_dict())
    assert "secret_abc" not in as_str
    assert "pass_123" not in as_str


# ── 4. Deployment Safety & Resource Monitoring (§8, §12) ─────────────────────


def test_deployment_lock_blocked_while_live_running() -> None:
    """Active live executor running trades disallows deployment locking."""
    mgr = DeploymentSafetyManager()

    # Reject deployment when ACTIVE and RUNNING
    with pytest.raises(DeploymentSafetyError):
        mgr.acquire_deployment_lock(
            deployer_id="deployer-ci",
            current_state=ExecutionState.RUNNING,
            current_role=ExecutorRole.ACTIVE,
        )
    assert mgr.is_locked is False

    # Allowed when STOPPED or STANDBY
    assert (
        mgr.acquire_deployment_lock(
            deployer_id="deployer-ci",
            current_state=ExecutionState.STOPPED,
            current_role=ExecutorRole.ACTIVE,
        )
        is True
    )
    assert mgr.is_locked is True

    # Duplicate acquisition blocked
    assert (
        mgr.acquire_deployment_lock(
            deployer_id="deployer-ci-2",
            current_state=ExecutionState.STOPPED,
            current_role=ExecutorRole.STANDBY,
        )
        is False
    )

    # Release lock
    assert mgr.release_deployment_lock("deployer-ci") is True
    assert mgr.is_locked is False


def test_deployment_version_compatibility() -> None:
    """Major version mismatch is rejected during deployment validation."""
    mgr = DeploymentSafetyManager()
    assert mgr.validate_version_compatibility("1.18.0", "1.17.0") is True
    assert mgr.validate_version_compatibility("2.0.0", "1.17.0") is False


def test_resource_monitor_threshold_evaluation() -> None:
    """ResourceMonitor flags violations when CPU, RAM, disk or broker latency breach limits."""
    monitor = ResourceMonitor(
        max_cpu_percent=90.0,
        max_ram_percent=90.0,
        max_disk_percent=90.0,
        max_broker_latency_ms=2000.0,
        max_ws_staleness_seconds=10.0,
    )

    healthy_metrics = ResourceMetrics(
        cpu_percent=45.0,
        ram_percent=60.0,
        disk_percent=50.0,
        broker_latency_ms=120.0,
        ws_freshness_seconds=0.5,
    )
    is_ok, breaches = monitor.evaluate(healthy_metrics)
    assert is_ok is True
    assert len(breaches) == 0

    exhausted_metrics = ResourceMetrics(
        cpu_percent=96.5,
        ram_percent=92.0,
        disk_percent=70.0,
        broker_latency_ms=3500.0,
        ws_freshness_seconds=12.0,
    )
    is_bad, breaches = monitor.evaluate(exhausted_metrics)
    assert is_bad is False
    assert len(breaches) == 4
    assert any("CPU exhaustion" in b for b in breaches)
    assert any("RAM exhaustion" in b for b in breaches)
    assert any("Broker latency" in b for b in breaches)
    assert any("WS tick stale" in b for b in breaches)


# ── 5. Observability & Control Plane Integration (§10) ───────────────────────


class MockHADisasterRecoverySession:
    """Mock session with HA disaster recovery attributes."""

    def __init__(self) -> None:
        self.executor_id = "standby-ec2-b"
        self.executor_role = "STANDBY"
        self.executor_epoch = 2
        self.lease_valid = True
        self.is_fenced = False
        self.resources_healthy = True
        self.sl_tracker = SlProtectionTracker()
        self.ledger = PositionLedger(starting_capital=100_000.0)
        self.engine = ExecutionEngine()

    def safety_snapshot(self) -> Any:
        return build_safety_snapshot(
            kill_switch_engaged=False,
            kill_switch_reason="",
            kill_switch_level="INFO",
            gates_live_trading_enabled=True,
            gates_broker_live_enabled=True,
            gates_account_confirmed=True,
            gates_risk_limits_valid=True,
            gates_kill_switch_off=True,
            broker_connected=True,
            broker_name="FYERS",
            broker_environment="PAPER",
            broker_health_reason="ok",
            reconciliation_healthy=True,
            reconciliation_status="MATCHED",
            reconciliation_reasons=(),
            risk_ready=True,
            capital_valid=True,
            last_risk_denial_reason="",
            sl_tracker=self.sl_tracker,
            armed="DISARMED",
            session_lifecycle="READY",
            last_block_reason="",
        )


def test_control_plane_snapshot_exposes_ha_metrics() -> None:
    """RuntimeSnapshot includes disaster_recovery metadata with role, epoch and lease."""
    session = MockHADisasterRecoverySession()
    auth = ControlPlaneAuth(valid_tokens={"auth_ha_secret"})
    events = EventStreamManager()
    ctrl = ControlPlaneController(session=session, auth=auth, event_stream=events)

    snap = ctrl.build_runtime_snapshot()
    assert "disaster_recovery" in snap.to_dict()
    ha = snap.disaster_recovery
    assert ha["executor_id"] == "standby-ec2-b"
    assert ha["role"] == "STANDBY"
    assert ha["epoch"] == 2
    assert ha["lease_valid"] is True
    assert ha["is_fenced"] is False
    assert ha["resources_healthy"] is True


def test_high_availability_snapshot_to_dict() -> None:
    """HighAvailabilitySnapshot serializes cleanly for APK/EXE display."""
    snap = HighAvailabilitySnapshot(
        executor_id="primary-ec2-a",
        role=ExecutorRole.ACTIVE.value,
        epoch=1,
        is_fenced=False,
        lease_holder="primary-ec2-a",
        lease_epoch=1,
        lease_valid=True,
        lease_expires_in_seconds=8.456,
        primary_alive=True,
        failover_history_count=0,
        resources_healthy=True,
        resource_violations=(),
    )

    d = snap.to_dict()
    assert d["role"] == "ACTIVE"
    assert d["lease_expires_in_seconds"] == 8.46
    assert d["resources_healthy"] is True
