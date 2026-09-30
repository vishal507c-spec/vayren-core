"""Contract tests for risk.native_kill_switch (engage, disengage, levels, serialization)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from risk import native_kill_switch as nk  # noqa: E402


def test_kill_switch_levels_and_states() -> None:
    levels = nk.levels()
    assert len(levels) >= 1

    ks = nk.NativeKillSwitch()
    try:
        # Initial state: not halted
        first_level = levels[0]
        assert ks.is_halted(first_level) is False

        # Engage kill switch (reason, level)
        ks.engage("manual test engagement", first_level)
        assert ks.is_halted(first_level) is True

        # Disengage
        ks.disengage(first_level)
        assert ks.is_halted(first_level) is False
    finally:
        ks.close()


def test_kill_switch_serialization() -> None:
    levels = nk.levels()
    first_level = levels[0]

    ks = nk.NativeKillSwitch()
    try:
        ks.engage("persisted reason", first_level)

        doc = ks.serialize()
        assert isinstance(doc, str)
        parsed = json.loads(doc)
        assert isinstance(parsed, dict)

        # Load into another instance
        ks2 = nk.NativeKillSwitch()
        try:
            ks2.load(doc)
            assert ks2.is_halted(first_level) is True
        finally:
            ks2.close()
    finally:
        ks.close()
