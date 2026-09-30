"""Contract tests for execution.native_execution (gates, modes, multipliers, intent IDs)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution import native_execution as ne  # noqa: E402


def test_gates_mask_and_missing() -> None:
    # All 5 gates ON ("true")
    all_true = ["true", "true", "true", "true", "true"]
    mask = ne.native_gates_mask(all_true)
    assert mask == 31

    # When all 5 gates are satisfied, missing_gates is empty
    missing = ne.native_missing_gates(mask)
    assert missing == ()

    # When all gates are OFF ("false")
    all_false = ["false", "false", "false", "false", "false"]
    mask0 = ne.native_gates_mask(all_false)
    assert mask0 == 0
    missing0 = ne.native_missing_gates(mask0)
    assert len(missing0) == 5
    assert "LIVE_TRADING_ENABLED" in missing0
    assert "KILL_SWITCH_OFF" in missing0


def test_resolve_mode_falls_back_to_paper() -> None:
    mode, reasons = ne.native_resolve_mode("LIVE", mask=0)
    assert mode == "PAPER"
    assert len(reasons) > 0


def test_intent_id_format() -> None:
    intent = ne.native_intent_id(
        strategy_id="strat-1",
        strategy_version="1.0.0",
        event_seq=10,
        intent_seq=1,
    )
    assert intent == "strat-1:1.0.0:10:1"
