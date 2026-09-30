"""Contract tests for execution.native_order_state (state machine transitions)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution import native_order_state as nos  # noqa: E402
from execution.models.order_state import OrderState  # noqa: E402


def test_valid_transitions() -> None:
    # Standard lifecycle transitions
    assert nos.transition_allowed(OrderState.CREATED, OrderState.VALIDATED) is True
    assert nos.transition_allowed(OrderState.VALIDATED, OrderState.SUBMITTED) is True
    assert nos.transition_allowed(OrderState.SUBMITTED, OrderState.ACKNOWLEDGED) is True
    assert nos.transition_allowed(OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED) is True
    assert nos.transition_allowed(OrderState.PARTIALLY_FILLED, OrderState.FILLED) is True
    assert nos.transition_allowed(OrderState.ACKNOWLEDGED, OrderState.CANCEL_PENDING) is True
    assert nos.transition_allowed(OrderState.CANCEL_PENDING, OrderState.CANCELLED) is True


def test_invalid_transitions_fail_closed() -> None:
    # Backward or illegal transitions
    assert nos.transition_allowed(OrderState.FILLED, OrderState.CREATED) is False
    assert nos.transition_allowed(OrderState.CANCELLED, OrderState.SUBMITTED) is False
    assert nos.transition_allowed(OrderState.REJECTED, OrderState.FILLED) is False
    assert nos.transition_allowed(OrderState.EXPIRED, OrderState.SUBMITTED) is False
