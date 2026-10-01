"""FYERS provider tests: identity, honest capability matrix, registration,
delegation, SDK isolation.

Strategy: transport and provider implementation live in ``src/broker/providers/fyers/``;
this suite pins the UBL contract surface, absence of duplicate implementations,
and clean boundary isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from broker.capabilities import Domain  # noqa: E402
from broker.interfaces import ERR_PROVIDER_UNAVAILABLE, ProviderError  # noqa: E402
from broker.providers.fyers import (  # noqa: E402
    BROKER_ID,
    DISPLAY_NAME,
    HISTORICAL_CAPABILITIES,
    FyersProvider,
)
from broker.registry import default_registry  # noqa: E402
from broker.selection import BrokerSelection  # noqa: E402
from broker.vocab import Environment, UnsupportedCapabilityError  # noqa: E402
from data.settings import DownloadSettings  # noqa: E402

PROVIDER_DIR = ROOT / "src" / "broker" / "providers" / "fyers"
TS = "2026-09-07T00:00:00+05:30"


def _settings(tmp_path, provider: str = BROKER_ID) -> DownloadSettings:
    return DownloadSettings(data_dir=str(tmp_path), provider=provider)


def test_broker_id_is_stable_and_single() -> None:
    assert BROKER_ID == "fyers"
    assert DISPLAY_NAME == "Fyers"
    assert FyersProvider.name == BROKER_ID


def test_capability_matrix_advertises_history_shape() -> None:
    """Faces-vs-capabilities consistency: the record serves HISTORICAL_DATA,
    so it advertises the history shape (split-brain empty sets are rejected
    by BrokerRecord). Fail-closed history is enforced by the transport
    raising ProviderError, not by advertising zero capabilities."""
    assert HISTORICAL_CAPABILITIES.supports_domain(Domain.HISTORICAL_DATA)
    assert not HISTORICAL_CAPABILITIES.supports_domain(Domain.MARKET_DATA)
    assert not HISTORICAL_CAPABILITIES.supports_domain(Domain.TRADING)


def test_registry_record_serves_fail_closed_history() -> None:
    import broker.providers  # noqa: F401 — seeds the record

    record = default_registry().get(BROKER_ID)
    assert record.name == BROKER_ID
    assert record.display_name == DISPLAY_NAME
    assert record.faces == (Domain.HISTORICAL_DATA,)
    assert record.capabilities == HISTORICAL_CAPABILITIES
    face = record.plugin.face(Domain.HISTORICAL_DATA, _settings(Path.cwd()))
    assert isinstance(face, FyersProvider)
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.TRADING)
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.MARKET_DATA)


def test_history_face_fails_closed_without_network(tmp_path) -> None:
    import broker.providers  # noqa: F401

    record = default_registry().get(BROKER_ID)
    face = record.plugin.face(Domain.HISTORICAL_DATA, _settings(tmp_path))
    assert isinstance(face, FyersProvider)
    ready, reason = face.available()
    assert isinstance(ready, bool) and isinstance(reason, str)
    with pytest.raises(ProviderError) as excinfo:
        face.fetch_candles("RELIANCE", "15m", None, None)  # type: ignore[arg-type]
    assert excinfo.value.code == ERR_PROVIDER_UNAVAILABLE
    with pytest.raises(ProviderError):
        face.symbols()


def test_selection_resolves_and_history_surface_is_served() -> None:
    """FYERS is selectable and the history surface resolves (caps advertised
    for the served face). Fail-closed without network happens at the
    transport (`ProviderError`), never as a silent fallback to another
    broker — see test_history_face_fails_closed_without_network."""
    import broker.providers  # noqa: F401
    from broker.selection import surface_resolution

    selection = BrokerSelection(
        name=BROKER_ID,
        environment=Environment.PAPER,
        selected_at=TS,
        reason="user-selected",
    )
    record = default_registry().get(selection.name)
    allowed, reason = surface_resolution(selection, record.capabilities, Domain.HISTORICAL_DATA)
    assert allowed
    assert "fyers" in reason


def test_plugin_record_builder_uses_injected_factory() -> None:
    """The provider package builds registry records without hardcoding transport."""
    from broker.providers.fyers import fyers_plugin_record as build_record

    made: list[object] = []

    def fake_factory(settings: object) -> object:
        made.append(settings)
        return object()

    record = build_record(fake_factory)
    assert record.name == BROKER_ID
    assert record.display_name == DISPLAY_NAME
    assert record.faces == (Domain.HISTORICAL_DATA,)
    assert record.capabilities == HISTORICAL_CAPABILITIES
    sentinel = object()
    assert record.plugin.face(Domain.HISTORICAL_DATA, sentinel) is not None
    assert made == [sentinel]
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.TRADING)


def test_data_domain_has_no_broker_sdk_implementation() -> None:
    """src/data/ has no broker-specific SDK implementation, auth flows, or session stores."""
    data_dir = ROOT / "src" / "data"
    for py in data_dir.rglob("*.py"):
        if "__pycache__" in py.parts or "tests" in py.parts:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for forbidden in (
            "class FyersProvider",
            "class ZerodhaProvider",
            "class FyersAuthFlow",
            "class KiteAuthFlow",
        ):
            assert forbidden not in text, f"{py} contains leaked broker implementation: {forbidden}"


def test_no_duplicate_fyers_implementation() -> None:
    """One FYERS transport implementation, in the provider package only."""
    providers = [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*.py")
        if ".venv" not in p.parts
        and "99_archive" not in p.parts
        and "__pycache__" not in p.parts
        and "tests" not in p.parts
        and "class FyersProvider" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert providers == ["src/broker/providers/fyers/adapter.py"], providers
    for cls in ("class FyersAuthFlow", "class FyersSessionStore", "class FyersSessionAdapter"):
        found = [
            p.relative_to(ROOT).as_posix()
            for p in ROOT.rglob("*.py")
            if ".venv" not in p.parts
            and "99_archive" not in p.parts
            and "__pycache__" not in p.parts
            and "tests" not in p.parts
            and cls in p.read_text(encoding="utf-8", errors="replace")
        ]
        assert len(found) == 1, f"{cls}: {found}"


def test_credentials_schema_is_provider_owned() -> None:
    """The venue declares its own credential shape (never Zerodha's)."""
    assert [f.key for f in FyersProvider.credential_fields] == [
        "app_id",
        "secret",
        "client_id",
        "totp_secret",
        "pin",
        "redirect_uri",
    ]
    assert FyersProvider.display_name == "Fyers"
