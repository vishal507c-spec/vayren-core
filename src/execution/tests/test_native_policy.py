"""Contract tests for execution.broker.native_policy (retries, limiter, backoff, clocks)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution.broker import native_policy as np  # noqa: E402


def test_retry_kind_enum() -> None:
    assert np.RetryKind.RETRY_SAFE == 0
    assert np.RetryKind.DO_NOT_RETRY == 1
    assert np.RetryKind.RECONCILE_FIRST == 2


def test_native_retry_kind() -> None:
    # Kernel classifies operations
    assert isinstance(np.native_retry_kind("GET /orders"), np.RetryKind)


def test_native_limiter_problem() -> None:
    # Usable configuration -> empty string
    assert np.native_limiter_problem(max_requests=10, window_seconds=1.0) == ""

    # Unusable configuration -> reports reason
    problem = np.native_limiter_problem(max_requests=0, window_seconds=1.0)
    assert len(problem) > 0
    assert "positive" in problem.lower()


def test_native_clock_ok() -> None:
    # Within drift
    assert np.native_clock_ok(local=100.0, reference=100.5, max_drift=1.0, readable=True) is True

    # Beyond drift
    assert np.native_clock_ok(local=100.0, reference=105.0, max_drift=1.0, readable=True) is False


def test_native_backoff_delay() -> None:
    delay = np.native_backoff_delay(base_seconds=1.0, factor=2.0, max_seconds=10.0, attempt=1)
    assert delay >= 1.0
    assert delay <= 10.0


def test_native_exhausted() -> None:
    assert np.native_exhausted(attempts_made=3, max_attempts=3) is True
    assert np.native_exhausted(attempts_made=1, max_attempts=3) is False
