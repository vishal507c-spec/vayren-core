"""Arming machine + resilience primitives tests."""

import pytest

from execution.broker.resilience import (
    RateLimiter,
    ResilienceState,
    RetryKind,
    classify_retry,
    clock_drift_ok,
)
from execution.modes import ArmingError, LiveArm, arm_transition


def test_arming_happy_path() -> None:
    state = LiveArm.DISARMED
    state = arm_transition(state, LiveArm.ARMING)
    state = arm_transition(state, LiveArm.ARMED)
    assert state == LiveArm.ARMED
    state = arm_transition(state, LiveArm.RUNNING)
    assert state == LiveArm.RUNNING
    state = arm_transition(state, LiveArm.HALTED)
    assert state == LiveArm.HALTED
    state = arm_transition(state, LiveArm.DISARMED)
    assert state == LiveArm.DISARMED


def test_arming_never_skips_steps() -> None:
    with pytest.raises(ArmingError):
        arm_transition(LiveArm.DISARMED, LiveArm.ARMED)
    with pytest.raises(ArmingError):
        arm_transition(LiveArm.DISARMED, LiveArm.RUNNING)
    with pytest.raises(ArmingError):
        arm_transition(LiveArm.HALTED, LiveArm.ARMED)
    with pytest.raises(ArmingError):
        arm_transition(LiveArm.ARMED, LiveArm.ARMING)


def test_retry_classification_fail_closed() -> None:
    assert classify_retry("health") is RetryKind.SAFE_TO_RETRY
    assert classify_retry("account") is RetryKind.SAFE_TO_RETRY
    assert classify_retry("positions") is RetryKind.SAFE_TO_RETRY
    assert classify_retry("place_order") is RetryKind.NOT_SAFE_TO_RETRY
    assert classify_retry("cancel_order") is RetryKind.REQUIRES_RECONCILIATION
    assert classify_retry("modify_order") is RetryKind.REQUIRES_RECONCILIATION
    assert classify_retry("settle") is RetryKind.REQUIRES_RECONCILIATION
    assert classify_retry("something-brand-new") is RetryKind.NOT_SAFE_TO_RETRY
    assert classify_retry("") is RetryKind.NOT_SAFE_TO_RETRY


def test_rate_limiter_window_and_429_counter() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=10.0)
    assert limiter.allow(100.0) is True
    assert limiter.allow(101.0) is True
    assert limiter.allow(102.0) is False  # exhausted: caller backs off
    assert limiter.allow(110.5) is True  # window slid past the first hit
    assert limiter.used == 2
    limiter.record_429()
    limiter.record_429()
    assert limiter.rejections_429 == 2
    with pytest.raises(ValueError):
        RateLimiter(max_requests=0)
    with pytest.raises(ValueError):
        RateLimiter(window_seconds=0.0)


def test_clock_drift() -> None:
    assert clock_drift_ok(1000.0, 1002.0, max_drift_seconds=5.0) is True
    assert clock_drift_ok(1000.0, 1010.0, max_drift_seconds=5.0) is False
    assert clock_drift_ok(1010.0, 1000.0, max_drift_seconds=5.0) is False
    assert clock_drift_ok(1000.0, 1000.0) is True
    assert clock_drift_ok("nope", 1000.0) is False  # type: ignore[arg-type]
    assert clock_drift_ok(None, 1000.0) is False  # type: ignore[arg-type]
    assert clock_drift_ok(1000.0, 1000.0, max_drift_seconds=-1.0) is False


def test_resilience_state_counters() -> None:
    state = ResilienceState()
    assert (state.allowed, state.denied) == (0, 0)
    assert (state.reconciliations_required, state.rate_429s) == (0, 0)
