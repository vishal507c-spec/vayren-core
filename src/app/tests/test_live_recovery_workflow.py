"""Gateway-restart recovery: desired vs observed state, no automatic LIVE resume.

The 2026-10-22 restart left the backend idle while the operator believed a
LIVE run was active. These tests pin the recovery contract: the interrupted
run is durable, the restored process starts STOPPED with an explicit recovery
phase, nothing is armed or submitted without a fresh operator authorization,
and every failure path is reported rather than hidden behind RUNNING.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.live_trading_service import LiveConfigError, LiveTradingService


def _seed(
    tmp_path: Path,
    *,
    timing: dict[str, Any],
    desired: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "config": {
            "strategy_name": "",
            "symbols": [],
            "timeframe": "30m",
            "mode": "LIVE",
            "quantity": 1.0,
            "capital": 1_000_000.0,
        },
        "session_timing": timing,
        "checkpoints": {"NSE:A": {"sentinel": 42}},
        "activity": [],
    }
    if desired is not None:
        payload["desired_state"] = desired
    if recovery is not None:
        payload["recovery"] = recovery
    path = data_dir / "live" / "live_session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return data_dir


def _load(data_dir: Path) -> dict[str, Any]:
    return json.loads((data_dir / "live" / "live_session.json").read_text(encoding="utf-8"))


def _running_timing(**overrides: Any) -> dict[str, Any]:
    timing = {
        "status": "RUNNING",
        "started_at": "2026-10-22T08:49:27+05:30",
        "stopped_at": "",
        "elapsed_seconds": 10_000,
        "signals_generated": 0,
        "start_epoch": 1_700_000_000.0,
    }
    timing.update(overrides)
    return timing


@pytest.mark.parametrize("saved_status", ["RUNNING", "STARTING"])
def test_restart_while_running_becomes_operator_recovery_not_autostart(
    tmp_path: Path, saved_status: str
) -> None:
    data_dir = _seed(tmp_path, timing=_running_timing(status=saved_status))
    service = LiveTradingService(data_dir)

    assert service._status == "STOPPED"
    assert service.desired_status == "RUNNING"
    assert service.recovery_pending is True
    assert service._recovery_phase == "AWAITING_OPERATOR"
    assert service._recovery_reason_code == "GATEWAY_RESTART_WHILE_RUNNING"
    assert service._timer.is_running is False
    assert service._sessions == {}
    assert service.is_live_confirmed() is False, "a restart must clear exec consent"
    assert service.recovery_status()["operator_authorization_required"] is True

    # Legacy timing is corrected to observed STOPPED, desired remains RUNNING,
    # and the checkpoints that enable broker reconciliation survive untouched.
    saved = _load(data_dir)
    assert saved["session_timing"]["status"] == "STOPPED"
    assert saved["session_timing"]["start_epoch"] is None
    assert saved["desired_state"]["status"] == "RUNNING"
    assert saved["desired_state"]["armed"] is True
    assert saved["recovery"]["phase"] == "AWAITING_OPERATOR"
    assert saved["checkpoints"] == {"NSE:A": {"sentinel": 42}}


def test_resume_exposes_recovering_while_sessions_are_rebuilt(tmp_path: Path) -> None:
    data_dir = _seed(tmp_path, timing=_running_timing())
    service = LiveTradingService(data_dir)
    service.validate = lambda: ()  # type: ignore[method-assign]
    service._verify_live_session_fresh = lambda: ()  # type: ignore[method-assign]
    seen: list[str] = []

    def build() -> None:
        seen.append(service._recovery_phase)
        service._sessions = {}

    service._build_sessions = build  # type: ignore[method-assign]

    ok, _ = service.start(confirmed=True)

    assert ok is True
    assert seen == ["RECOVERING"]
    assert service._recovery_phase == "COMPLETE"
    assert service._recovery_recovered_at
    service.stop()


def test_crash_while_recovering_restores_as_awaiting_operator(tmp_path: Path) -> None:
    data_dir = _seed(
        tmp_path,
        timing=_running_timing(),
        recovery={
            "phase": "RECOVERING",
            "required": True,
            "reason_code": "GATEWAY_RESTART_WHILE_RUNNING",
            "reasons": ["operator-authorized resume in progress"],
            "interrupted_at": "2026-10-22T04:00:00+00:00",
            "attempts": 1,
        },
    )
    service = LiveTradingService(data_dir)
    assert service._recovery_phase == "AWAITING_OPERATOR"
    assert service.recovery_pending is True
    assert service.recovery_status()["operator_authorization_required"] is True


def test_build_failure_during_recovery_fails_closed_without_consent(tmp_path: Path) -> None:
    data_dir = _seed(tmp_path, timing=_running_timing())
    service = LiveTradingService(data_dir)
    service.validate = lambda: ()  # type: ignore[method-assign]
    service._verify_live_session_fresh = lambda: ()  # type: ignore[method-assign]

    def explode() -> None:
        raise RuntimeError("session construction failed")

    service._build_sessions = explode  # type: ignore[method-assign]

    ok, reasons = service.start(confirmed=True)

    assert ok is False
    assert "session construction failed" in reasons[0]
    assert service._status == "ERROR"
    assert service._recovery_phase == "FAILED"
    assert service._recovery_reason_code == "SESSION_BUILD_FAILED"
    assert service.recovery_pending is True
    assert service.is_live_confirmed() is False
    assert service._timer.is_running is False


def test_setup_changes_are_blocked_until_recovery_is_resolved(tmp_path: Path) -> None:
    data_dir = _seed(tmp_path, timing=_running_timing())
    service = LiveTradingService(data_dir)
    with pytest.raises(LiveConfigError, match="recovery is pending"):
        service.configure(quantity=2.0)
    service.stop("operator stop")
    service.configure(quantity=2.0)
    assert service._config.quantity == 2.0


def test_blocked_resume_is_durable_and_survives_another_restart(tmp_path: Path) -> None:
    data_dir = _seed(tmp_path, timing=_running_timing())
    service = LiveTradingService(data_dir)
    service.confirm_live_execution()
    assert service.is_live_confirmed() is True
    service.validate = lambda: ("broker not authenticated",)  # type: ignore[method-assign]

    ok, blockers = service.start(confirmed=False)

    assert ok is False
    assert any("broker not authenticated" in item for item in blockers)
    assert service._sessions == {}
    assert service._timer.is_running is False
    assert service._status == "STOPPED"
    assert service._recovery_phase == "BLOCKED"
    assert service._recovery_attempts == 1
    assert service.desired_status == "RUNNING"
    assert service.is_live_confirmed() is False, "a blocked resume is not authorization"
    saved = _load(data_dir)
    assert saved["recovery"]["phase"] == "BLOCKED"
    assert saved["recovery"]["attempts"] == 1

    # A second gateway restart must not lose the unresolved attempt or reset
    # its count to zero.
    restarted = LiveTradingService(data_dir)
    assert restarted._status == "STOPPED"
    assert restarted._recovery_phase == "BLOCKED"
    assert restarted._recovery_attempts == 1
    assert restarted.recovery_pending is True


def test_explicit_confirmed_start_is_the_authorization_and_completes_recovery(
    tmp_path: Path,
) -> None:
    data_dir = _seed(tmp_path, timing=_running_timing())
    service = LiveTradingService(data_dir)
    service.validate = lambda: ()  # type: ignore[method-assign]
    service._verify_live_session_fresh = lambda: ()  # type: ignore[method-assign]
    service._build_sessions = lambda: None  # type: ignore[method-assign]

    ok, reasons = service.start(confirmed=True)

    assert ok is True, reasons
    assert service._status == "RUNNING"
    assert service._recovery_phase == "COMPLETE"
    assert service.recovery_pending is False
    assert service._recovery_attempts == 1
    assert service._recovery_recovered_at
    assert service.is_live_confirmed() is True
    saved = _load(data_dir)
    assert saved["recovery"]["phase"] == "COMPLETE"
    assert saved["desired_state"]["status"] == "RUNNING"
    service._timer.stop()
    service._worker_started = False


def test_desired_running_without_a_running_marker_is_still_recovery(tmp_path: Path) -> None:
    data_dir = _seed(
        tmp_path,
        timing={"status": "STOPPED", "started_at": "", "stopped_at": "", "start_epoch": None},
        desired={"status": "RUNNING", "armed": True},
    )
    service = LiveTradingService(data_dir)
    assert service._status == "STOPPED"
    assert service.desired_status == "RUNNING"
    assert service._recovery_phase == "AWAITING_OPERATOR"
    assert service.recovery_pending is True


def test_clean_stop_ends_recovery_intent(tmp_path: Path) -> None:
    data_dir = _seed(tmp_path, timing=_running_timing())
    service = LiveTradingService(data_dir)
    assert service.recovery_pending is True

    service.stop("operator stop")

    assert service.desired_status == "STOPPED"
    assert service._recovery_phase == "NOT_REQUIRED"
    assert service.recovery_pending is False
    assert _load(data_dir)["desired_state"]["status"] == "STOPPED"
    assert _load(data_dir)["recovery"]["phase"] == "NOT_REQUIRED"

    restarted = LiveTradingService(data_dir)
    assert restarted._status == "STOPPED"
    assert restarted.recovery_pending is False


def test_clean_shutdown_is_not_recovery(tmp_path: Path) -> None:
    data_dir = _seed(
        tmp_path,
        timing={"status": "STOPPED", "started_at": "", "stopped_at": "", "start_epoch": None},
    )
    service = LiveTradingService(data_dir)
    assert service._status == "STOPPED"
    assert service.desired_status == "STOPPED"
    assert service.recovery_pending is False
    assert service._timer.is_running is False


class _FailingSession:
    def __init__(self) -> None:
        self.disarmed = ""

    def step(self, _now_epoch: float) -> None:
        raise RuntimeError("feed worker died")

    def disarm(self, reason: str) -> None:
        self.disarmed = reason

    def checkpoint(self) -> dict[str, Any]:
        return {}


class _StoppedTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def test_worker_tick_failure_never_stays_running(tmp_path: Path) -> None:
    data_dir = _seed(
        tmp_path,
        timing={"status": "STOPPED", "started_at": "", "stopped_at": "", "start_epoch": None},
    )
    service = LiveTradingService(data_dir)
    failing = _FailingSession()
    timer = _StoppedTimer()
    service._sessions = {"NSE:A": failing}  # type: ignore[assignment]
    service._timer = timer  # type: ignore[assignment]
    service._worker_started = True
    service._status = "RUNNING"
    service._desired_status = "RUNNING"
    service.confirm_live_execution()

    service.tick()

    assert service._status == "ERROR"
    assert service._desired_status == "RUNNING"
    assert service._recovery_phase == "FAILED"
    assert service._recovery_reason_code == "WORKER_TICK_FAILED"
    assert service.recovery_pending is True
    assert service.is_live_confirmed() is False, "degrade requires a fresh authorization"
    assert failing.disarmed.startswith("degraded:")
    assert timer.stopped is True
    assert service._worker_started is False
