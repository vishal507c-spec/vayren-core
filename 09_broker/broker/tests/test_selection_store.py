"""FileSelectionStore — atomic JSON persistence for the authoritative
selection (M4). Fail-closed on corrupt content; never stores secrets."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BROKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER_DIR))

import pytest  # noqa: E402

from broker.selection import BrokerSelection  # noqa: E402
from broker.selection_store import FileSelectionStore, SelectionLoadError  # noqa: E402
from broker.vocab import Environment  # noqa: E402

TS = "2026-09-06T14:00:00+05:30"


def _selection(name: str = "zerodha") -> BrokerSelection:
    return BrokerSelection(
        name=name, environment=Environment.PAPER, selected_at=TS, reason="user-selected"
    )


def test_roundtrip(tmp_path: Path) -> None:
    store = FileSelectionStore(tmp_path / "sel.json")
    store.save(_selection("sandbox"))
    loaded = store.load()
    assert loaded is not None
    assert loaded.name == "sandbox"
    assert loaded.environment is Environment.PAPER
    assert loaded.reason == "user-selected"


def test_missing_file_is_no_selection(tmp_path: Path) -> None:
    store = FileSelectionStore(tmp_path / "absent.json")
    assert store.load() is None


def test_malformed_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "sel.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SelectionLoadError, match="unreadable"):
        FileSelectionStore(path).load()


def test_wrong_schema_kind_or_version_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "sel.json"
    path.write_text(json.dumps({"kind": "other", "version": 1}), encoding="utf-8")
    with pytest.raises(SelectionLoadError, match="unknown selection schema"):
        FileSelectionStore(path).load()
    path.write_text(
        json.dumps({"kind": "vayren.broker_selection", "version": 99}), encoding="utf-8"
    )
    with pytest.raises(SelectionLoadError, match="unknown selection schema"):
        FileSelectionStore(path).load()


def test_missing_field_and_bad_environment_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "sel.json"
    path.write_text(
        json.dumps({"kind": "vayren.broker_selection", "version": 1, "name": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(SelectionLoadError, match="missing field"):
        FileSelectionStore(path).load()
    path.write_text(
        json.dumps(
            {
                "kind": "vayren.broker_selection",
                "version": 1,
                "name": "x",
                "environment": "mars",
                "selected_at": TS,
                "reason": "r",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SelectionLoadError, match="invalid"):
        FileSelectionStore(path).load()


def test_save_is_atomic_no_temp_files_left(tmp_path: Path) -> None:
    store = FileSelectionStore(tmp_path / "nested" / "sel.json")
    store.save(_selection())
    leftovers = [p.name for p in (tmp_path / "nested").iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert (tmp_path / "nested" / "sel.json").is_file()


def test_clear_removes_file_and_absent_is_clean(tmp_path: Path) -> None:
    store = FileSelectionStore(tmp_path / "sel.json")
    store.save(_selection())
    store.clear()
    assert store.load() is None
    store.clear()  # already absent — no error


def test_schema_carries_no_secret_fields(tmp_path: Path) -> None:
    """The persisted selection is identity+reason only — no credential keys."""
    path = tmp_path / "sel.json"
    FileSelectionStore(path).save(_selection())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"kind", "version", "name", "environment", "selected_at", "reason"}
    lowered = json.dumps(payload).lower()
    for secret_word in ("api_key", "secret", "password", "token", "totp"):
        assert secret_word not in lowered
