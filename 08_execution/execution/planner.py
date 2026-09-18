"""OrderPlanner — deterministic intent → executable order specs.

The planner never talks to a broker and never overrides risk: it only
translates an APPROVED intent (plus advisory execution preferences) into
concrete orders. Bracket legs are data for future use, not live behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

from execution.models.intent import ExecutionIntent
from execution.models.order import OrderPlan
from execution.native_execution import native_plan_order


@dataclass(frozen=True)
class ExecutionPreferences:
    """Advisory preferences from the adaptive layer (never risk overrides)."""

    prefer_limit: bool = False
    size_multiplier: float = 1.0  # (0, 1]: shrink-only, never enlarge

    def __post_init__(self) -> None:
        if not 0.0 < self.size_multiplier <= 1.0:
            raise ValueError("size_multiplier must be in (0, 1]")


class OrderPlanner:
    """Pure deterministic planning: same intent → same plan, always."""

    def plan(
        self,
        intent: ExecutionIntent,
        reference_price: float,
        preferences: ExecutionPreferences | None = None,
    ) -> OrderPlan:
        prefs = preferences if preferences is not None else ExecutionPreferences()
        quantity, order_type, limit_price = native_plan_order(
            intent.quantity,
            intent.preferred_order_type,
            reference_price,
            prefs.prefer_limit,
            prefs.size_multiplier,
        )
        bracket: tuple[dict[str, object], ...] = ()
        return OrderPlan(
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            bracket=bracket,
        )
