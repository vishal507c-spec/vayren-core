"""KillSwitch tests — latch, levels, persistence across restarts.

The parity cases pin the historical public contract (exact rejection messages,
exact persisted bytes, reload and truthiness rules) so the Rust kernel can be
authority without any observable behavior changing.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from risk.kill_switch import KillSwitch, KillSwitchState
from risk.native_kill_switch import levels as native_levels

TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?\+00:00")


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


# ── parity: initial shape, vocabulary, timestamps ─────────────────────────


def test_every_level_starts_disengaged() -> None:
    kill = KillSwitch()
    for level in KillSwitch.LEVELS:
        assert kill.state(level=level) == KillSwitchState(
            engaged=False, reason="", engaged_at="", level=level
        )
        assert kill.is_halted(level=level) is False


def test_level_vocabulary_matches_the_kernel() -> None:
    assert KillSwitch.LEVELS == ("global", "strategy", "broker")
    assert native_levels() == KillSwitch.LEVELS


def test_engage_timestamp_is_a_python_isoformat_utc_stamp() -> None:
    kill = KillSwitch()
    kill.engage("stamp me", level="strategy")
    stamp = kill.state(level="strategy").engaged_at
    assert TIMESTAMP.fullmatch(stamp), stamp
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is UTC
    assert abs((datetime.now(UTC) - parsed).total_seconds()) < 60


# ── parity: invalid input, byte-exact messages ────────────────────────────


@pytest.mark.parametrize("level", ["nope", "Global", "", " global", "global ", None, 5, b"global"])
def test_unknown_level_message_is_byte_exact(level: Any) -> None:
    expected = f"unknown kill-switch level: {level}"
    kill = KillSwitch()
    with pytest.raises(ValueError) as engaged:
        kill.engage("reason", level=level)
    assert str(engaged.value) == expected
    with pytest.raises(ValueError) as released:
        kill.disengage(level=level)
    assert str(released.value) == expected
    with pytest.raises(ValueError) as halted:
        kill.is_halted(level=level)
    assert str(halted.value) == expected
    with pytest.raises(ValueError) as stated:
        kill.state(level=level)
    assert str(stated.value) == expected


def test_rejected_engage_never_writes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    kill = KillSwitch(path)
    with pytest.raises(ValueError):
        kill.engage("reason", level="nope")
    assert not path.exists()


# ── parity: the persisted document ────────────────────────────────────────


NASTY_REASON = 'quote " back\\slash tab\tnewline\nCR\r NUL\x00 bell\x07 bs\b ff\f é 中 😀 /solidus'


def test_saved_bytes_match_stdlib_json(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    kill = KillSwitch(path)
    kill.engage(NASTY_REASON, level="strategy")
    kill.engage("broker down", level="broker")
    document = {
        level: {
            "engaged": kill.state(level=level).engaged,
            "reason": kill.state(level=level).reason,
            "engaged_at": kill.state(level=level).engaged_at,
        }
        for level in KillSwitch.LEVELS
    }
    expected = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
    # Path.write_text translates "\n" to the platform line separator.
    assert path.read_bytes() == expected.replace(b"\n", os.linesep.encode())


def test_saved_document_keys_are_sorted_and_have_no_trailing_newline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "kill.json"
    kill = KillSwitch(path)
    kill.engage("halt")
    text = path.read_text(encoding="utf-8").replace(os.linesep, "\n")
    assert text.startswith('{\n  "broker": {\n    "engaged": false,\n')
    assert text.index('"broker"') < text.index('"global"') < text.index('"strategy"')
    assert not text.endswith("\n")


@pytest.mark.parametrize(
    ("stored", "halted"),
    [
        (True, True),
        (1, True),
        (-1, True),
        ("yes", True),
        ("false", True),
        ([0], True),
        ({"a": 1}, True),
        (False, False),
        (0, False),
        ("", False),
        ([], False),
        ({}, False),
        (None, False),
    ],
)
def test_engaged_flag_is_read_with_python_truthiness(
    tmp_path: Path, stored: Any, halted: bool
) -> None:
    path = tmp_path / "kill.json"
    _write_document(path, {"global": {"engaged": stored, "reason": "r"}})
    assert KillSwitch(path).is_halted() is halted


def test_absent_engaged_key_does_not_latch(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    _write_document(path, {"global": {"reason": "no flag"}})
    assert KillSwitch(path).is_halted() is False


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("text", "text"),
        (5, "5"),
        (5.0, "5.0"),
        (1e16, "1e+16"),
        (1e-7, "1e-07"),
        (0.1, "0.1"),
        (-0.0, "-0.0"),
        (12345678901234567890, "12345678901234567890"),
        (True, "True"),
        (False, "False"),
        (None, "None"),
    ],
)
def test_reason_and_timestamp_coerce_like_python_str(
    tmp_path: Path, stored: Any, expected: str
) -> None:
    path = tmp_path / "kill.json"
    _write_document(path, {"global": {"engaged": True, "reason": stored, "engaged_at": stored}})
    state = KillSwitch(path).state()
    assert state.reason == expected
    assert state.engaged_at == expected


def test_missing_reason_and_timestamp_default_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    _write_document(path, {"global": {"engaged": True}})
    assert KillSwitch(path).state() == KillSwitchState(
        engaged=True, reason="", engaged_at="", level="global"
    )


def test_duplicate_keys_resolve_to_the_last_one(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"global": {"engaged": false, "reason": "first"},'
        ' "global": {"engaged": true, "reason": "last"}}',
        encoding="utf-8",
    )
    reopened = KillSwitch(path)
    assert reopened.is_halted() is True
    assert reopened.state().reason == "last"


def test_round_trip_restores_every_level(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    kill = KillSwitch(path)
    kill.engage("g", level="global")
    kill.engage("s", level="strategy")
    kill.engage("b", level="broker")
    reopened = KillSwitch(path)
    for level, reason in (("global", "g"), ("strategy", "s"), ("broker", "b")):
        assert reopened.state(level=level).reason == reason
        assert reopened.state(level=level).engaged_at == kill.state(level=level).engaged_at
        assert reopened.is_halted(level=level) is True


def test_pre_migration_file_is_readable_with_extras_ignored(tmp_path: Path) -> None:
    path = tmp_path / "kill.json"
    _write_document(
        path,
        {
            "strategy": {"engaged": True, "reason": "old write", "engaged_at": "t", "extra": 1},
            "unknown_level": {"engaged": True, "reason": "ignored"},
        },
    )
    reopened = KillSwitch(path)
    assert reopened.state(level="strategy") == KillSwitchState(
        engaged=True, reason="old write", engaged_at="t", level="strategy"
    )
    assert reopened.is_halted(level="global") is False


# ── parity: unreadable files ──────────────────────────────────────────────


@pytest.mark.parametrize("text", ["", "not json at all", "{broken", '{"global": '])
def test_unparseable_file_leaves_the_switch_disengaged(tmp_path: Path, text: str) -> None:
    path = tmp_path / "kill.json"
    _write_document(path, text)
    assert KillSwitch(path).is_halted() is False


@pytest.mark.parametrize(
    ("document", "type_name"),
    [
        ("[]", "list"),
        ("5", "int"),
        ("5.5", "float"),
        ('"text"', "str"),
        ("null", "NoneType"),
        ("true", "bool"),
    ],
)
def test_unwalkable_root_reports_the_historical_attributeerror(
    tmp_path: Path, document: str, type_name: str
) -> None:
    path = tmp_path / "kill.json"
    _write_document(path, document)
    with pytest.raises(AttributeError) as raised:
        KillSwitch(path)
    assert str(raised.value) == f"'{type_name}' object has no attribute 'get'"


@pytest.mark.parametrize(
    ("entry", "type_name"),
    [
        ('{"global": "text"}', "str"),
        ('{"global": {"engaged": true}, "strategy": 7}', "int"),
        ('{"global": {"engaged": true}, "strategy": [], "broker": {}}', "list"),
        ('{"global": {"engaged": true}, "strategy": null}', "NoneType"),
    ],
)
def test_unwalkable_level_reports_the_historical_attributeerror(
    tmp_path: Path, entry: str, type_name: str
) -> None:
    path = tmp_path / "kill.json"
    _write_document(path, entry)
    with pytest.raises(AttributeError) as raised:
        KillSwitch(path)
    assert str(raised.value) == f"'{type_name}' object has no attribute 'get'"


def test_container_valued_fields_report_their_element_count(tmp_path: Path) -> None:
    """Known divergence: Python rendered `str([1, 2])` as `"[1, 2]"`.

    Only a hand-edited file can put a container in these fields; the latch is
    still restored, so the safety behavior is unchanged.
    """
    path = tmp_path / "kill.json"
    _write_document(path, {"global": {"engaged": True, "reason": [1, 2], "engaged_at": {"a": 1}}})
    state = KillSwitch(path).state()
    assert (state.reason, state.engaged_at) == ("[2]", "{1}")
    assert KillSwitch(path).is_halted() is True


# ── parity: failure modes on write ────────────────────────────────────────


def test_failed_write_keeps_the_latch_engaged(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    kill = KillSwitch(blocker / "kill.json")
    with pytest.raises(OSError):
        kill.engage("persist fails")
    assert kill.is_halted() is True


def _write_document(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = document if isinstance(document, str) else json.dumps(document)
    path.write_text(text, encoding="utf-8")
