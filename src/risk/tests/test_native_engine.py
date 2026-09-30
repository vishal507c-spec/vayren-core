"""Contract tests for risk.native_engine (policy checks, limit evaluation, handle lifecycle)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from risk import native_engine as ne  # noqa: E402


def test_engine_lifecycle_and_evaluation() -> None:
    policy = ne.RiskPolicy(
        max_position_qty=100.0,
        max_order_qty=50.0,
        cooldown_seconds=0.0,
        allowed_symbols=("AAPL", "MSFT"),
        session_start="09:15",
        session_end="15:30",
    )
    engine = ne.NativeRiskEngine(policy)
    try:
        req = ne.RiskRequest(
            intent_id="intent-1",
            strategy_id="strat-1",
            symbol="AAPL",
            side="LONG",
            timestamp="2026-01-05T10:00:00",
            quantity=10.0,
            price=150.0,
            position_qty=0.0,
            day_pnl=0.0,
            strategy_day_pnl=0.0,
            equity=10000.0,
            available_capital=10000.0,
            now_epoch=1767607200.0,
            orders_today=1,
            broker_healthy=True,
        )
        decision = engine.evaluate(req, kill_halted=False)
        assert isinstance(decision, ne.RiskDecision)
        assert decision.approved is True
        assert decision.intent_id == "intent-1"

        # Disallowed symbol check
        req_bad_sym = ne.RiskRequest(
            intent_id="intent-2",
            strategy_id="strat-1",
            symbol="GOOG",  # Not in allowed_symbols
            side="LONG",
            timestamp="2026-01-05T10:00:00",
            quantity=10.0,
            price=150.0,
            position_qty=0.0,
            day_pnl=0.0,
            strategy_day_pnl=0.0,
            equity=10000.0,
            available_capital=10000.0,
            now_epoch=1767607200.0,
            orders_today=2,
            broker_healthy=True,
        )
        decision2 = engine.evaluate(req_bad_sym, kill_halted=False)
        assert decision2.approved is False
        assert any("symbol" in r.lower() for r in decision2.reasons)

        # Kill switch halted check
        decision_halted = engine.evaluate(req, kill_halted=True)
        assert decision_halted.approved is False
        assert any("kill" in r.lower() for r in decision_halted.reasons)
    finally:
        engine.close()
