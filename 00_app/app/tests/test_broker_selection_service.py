"""BrokerSelectionService — the authoritative selection (M4).

Precedence: explicit CLI/UI → persisted store → compatibility default.
Every failure path is fail-closed with the previous valid selection kept.
"""

from __future__ import annotations

import json
from pathlib import Path

# Seed the unified registry's built-ins (paper/sandbox/zerodha).
import data.provider.factory  # noqa: E402,F401
import execution.broker.factory  # noqa: E402,F401
import pytest
from broker.capabilities import Domain
from broker.selection import SelectionError
from broker.selection_store import SelectionLoadError

from app.services.broker_selection_service import (
    DEFAULT_HISTORY_BROKER,
    BrokerSelectionService,
    app_selection_store,
)


def _service(tmp_path: Path) -> BrokerSelectionService:
    return BrokerSelectionService(app_selection_store(tmp_path))


def test_compatibility_default_when_no_selection_exists(tmp_path: Path) -> None:
    service = _service(tmp_path)
    selection = service.current()
    assert selection.name == DEFAULT_HISTORY_BROKER
    assert "compatibility-default" in selection.reason
    assert not service.loaded_from_store
    # The default is recorded, never silently presented as a user choice.
    assert selection.reason != "user-selected"


def test_explicit_select_persists_and_wins(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.current()  # compat default first
    outcome = service.select("sandbox")
    assert outcome.selection.name == "sandbox"
    assert outcome.persisted
    assert service.current().name == "sandbox"
    # A fresh service (new process) reads the persisted explicit choice.
    reloaded = _service(tmp_path)
    assert reloaded.current().name == "sandbox"
    assert reloaded.loaded_from_store


def test_unknown_broker_fails_closed_previous_preserved(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.select("sandbox")
    with pytest.raises(SelectionError, match="unknown broker"):
        service.select("ghost")
    assert service.current().name == "sandbox"  # never silently replaced


def test_corrupt_store_fails_closed_not_silently_defaulted(tmp_path: Path) -> None:
    store = app_selection_store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{broken", encoding="utf-8")
    service = BrokerSelectionService(store)
    with pytest.raises(SelectionLoadError):
        service.current()
    assert service.current_or_none() is None  # surfaces as unconfigured


def test_surface_resolution_matches_registry_capabilities(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.select(DEFAULT_HISTORY_BROKER)
    allowed, reason = service.surface_allowed(Domain.HISTORICAL_DATA)
    assert allowed and "serves historical_data" in reason
    allowed, reason = service.surface_allowed(Domain.TRADING)
    assert not allowed and "does not provide trading capability" in reason


def test_broker_choices_are_registry_backed_and_capability_honest(tmp_path: Path) -> None:
    service = _service(tmp_path)
    choices = {str(c["name"]): c for c in service.broker_choices()}
    assert set(choices) >= {"paper", "sandbox", DEFAULT_HISTORY_BROKER}

    def domains(name: str) -> dict[str, bool]:
        raw = choices[name]["domains"]
        assert isinstance(raw, dict)
        return {str(k): bool(v) for k, v in raw.items()}

    assert domains(DEFAULT_HISTORY_BROKER)["historical_data"] is True
    assert domains(DEFAULT_HISTORY_BROKER)["trading"] is False
    assert domains("sandbox")["trading"] is True
    assert domains("sandbox")["historical_data"] is False


def test_broker_capabilities_unknown_raises(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(SelectionError, match="unknown broker"):
        service.broker_capabilities("ghost")


def test_reload_picks_up_external_store_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.current()
    # External writer persists a different explicit selection.
    other = _service(tmp_path)
    other.select("paper")
    assert service.reload() is not None
    assert service.current().name == "paper"


def test_selection_file_has_no_secrets(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.select("sandbox")
    payload = json.loads(app_selection_store(tmp_path).path.read_text(encoding="utf-8"))
    assert set(payload) == {"kind", "version", "name", "environment", "selected_at", "reason"}
