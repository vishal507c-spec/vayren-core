"""KillSwitch tests — latch, levels, persistence across restarts."""

from pathlib import Path

import pytest

from risk.kill_switch import KillSwitch


def test_engage_and_disengage_global() -> None:
    kill = KillSwitch()
    assert not kill.is_halted()
    kill.engage("operator halt")
    assert kill.is_halted()
    assert kill.state().reason == "operator halt"
    kill.disengage()
    assert not kill.is_halted()


def test_levels_are_independent_but_global_dominates() -> None:
    kill = KillSwitch()
    kill.engage("strategy bug", level="strategy")
    assert kill.is_halted(level="strategy")
    assert not kill.is_halted(level="broker")
    kill.engage("everything stops")
    assert kill.is_halted(level="broker")
    kill.disengage()
    assert kill.is_halted(level="strategy")
    kill.disengage(level="strategy")
    assert not kill.is_halted()


def test_unknown_level_raises() -> None:
    kill = KillSwitch()
    with pytest.raises(ValueError):
        kill.engage("x", level="nope")
    with pytest.raises(ValueError):
        kill.disengage(level="nope")
    with pytest.raises(ValueError):
        kill.is_halted(level="nope")


def test_engaged_state_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    kill = KillSwitch(path)
    kill.engage("persist me", level="broker")
    reopened = KillSwitch(path)
    assert reopened.is_halted(level="broker")
    assert reopened.state(level="broker").reason == "persist me"
    reopened.disengage(level="broker")
    assert not KillSwitch(path).is_halted(level="broker")


def test_disengage_persists_release(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    kill = KillSwitch(path)
    kill.engage("halt")
    kill.disengage()
    assert path.is_file()
    assert not KillSwitch(path).is_halted()
