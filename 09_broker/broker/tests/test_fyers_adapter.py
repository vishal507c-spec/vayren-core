"""FYERS adapter tests: identity, honest capability matrix, registration,
delegation, SDK isolation.

Strategy: transport stays exactly once in ``02_data/data/provider/fyers/``;
this suite pins the UBL contract surface in
``09_broker/broker/adapters/fyers/`` and the absence of duplication,
leakage and aliases. Authentication phase: the venue claims NO history
capabilities, so history fails closed while management/selection work.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
for entry in (
    "00_app",
    "02_data",
    "07_risk",
    "08_execution",
    "09_broker",
):
    sys.path.insert(0, str(ROOT / entry))

from data.provider.contract import ProviderError  # noqa: E402
from data.provider.fyers import FyersProvider  # noqa: E402
from data.settings import DownloadSettings  # noqa: E402

from broker.adapters.fyers import (  # noqa: E402
    BROKER_ID,
    DISPLAY_NAME,
    HISTORICAL_CAPABILITIES,
)
from broker.capabilities import Domain  # noqa: E402
from broker.registry import default_registry  # noqa: E402
from broker.selection import BrokerSelection  # noqa: E402
from broker.vocab import Environment, UnsupportedCapabilityError  # noqa: E402

ADAPTER_DIR = ROOT / "09_broker" / "broker" / "adapters" / "fyers"
TS = "2026-09-07T00:00:00+05:30"


def _settings(tmp_path, provider: str = BROKER_ID) -> DownloadSettings:
    return DownloadSettings(data_dir=str(tmp_path), provider=provider)


def test_broker_id_is_stable_and_single() -> None:
    assert BROKER_ID == "fyers"
    assert DISPLAY_NAME == "Fyers"
    assert FyersProvider.name == BROKER_ID


def test_capability_matrix_is_honestly_empty() -> None:
    """Authentication phase: no history capabilities claimed, so every
    history surface fails closed with the recorded reason."""
    assert HISTORICAL_CAPABILITIES.items == frozenset()
    for domain in (Domain.HISTORICAL_DATA, Domain.MARKET_DATA, Domain.TRADING):
        assert not HISTORICAL_CAPABILITIES.supports_domain(domain)


def test_registry_record_serves_fail_closed_history() -> None:
    import data.provider.factory  # noqa: F401 — seeds the record

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
    import data.provider.factory  # noqa: F401

    record = default_registry().get(BROKER_ID)
    face = record.plugin.face(Domain.HISTORICAL_DATA, _settings(tmp_path))
    assert isinstance(face, FyersProvider)
    ready, reason = face.available()
    assert isinstance(ready, bool) and isinstance(reason, str)
    with pytest.raises(ProviderError) as excinfo:
        face.fetch_candles("RELIANCE", "15m", None, None)  # type: ignore[arg-type]
    assert excinfo.value.code == "PROVIDER_UNAVAILABLE"
    with pytest.raises(ProviderError):
        face.symbols()


def test_selection_resolves_but_history_surface_refuses() -> None:
    """FYERS is selectable (management works); the history surface fails
    closed with an explicit reason instead of a silent fallback."""
    import data.provider.factory  # noqa: F401

    from broker.selection import surface_resolution

    selection = BrokerSelection(
        name=BROKER_ID,
        environment=Environment.PAPER,
        selected_at=TS,
        reason="user-selected",
    )
    record = default_registry().get(selection.name)
    allowed, reason = surface_resolution(selection, record.capabilities, Domain.HISTORICAL_DATA)
    assert not allowed
    assert "fyers" in reason


def test_plugin_record_builder_uses_injected_factory() -> None:
    """The adapter package builds registry records without importing the
    transport layer (constructor injection — no broker→data edge)."""
    from broker.adapters.fyers import fyers_plugin_record as build_record

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


def test_adapter_package_has_no_business_logic() -> None:
    """The adapter package owns identity/capabilities/registration only —
    no transport classes, no endpoints, no credential handling."""
    assert ADAPTER_DIR.is_dir()
    sources = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted(ADAPTER_DIR.rglob("*.py"))
        if "__pycache__" not in p.parts
    )
    for token in (
        "class FyersProvider",
        "class FyersAuthFlow",
        "generate-authcode",
        "validate-authcode",
        "app_id_hash",
        "os.environ",
        "getenv",
    ):
        assert token not in sources, f"business logic leaked into adapter package: {token}"


def test_adapter_import_pulls_no_transport_or_secrets() -> None:
    """Fresh interpreter: importing the adapter package must not touch the
    transport layer, HTTP clients, or any credential machinery."""
    code = (
        "import sys, broker.adapters.fyers as a; "
        "mods = set(sys.modules); "
        "print('urllib.request' in mods); "
        "print([m for m in mods if m == 'data' or m.startswith('data.')]); "
        "print(a.BROKER_ID)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "09_broker"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    has_http, data_mods, broker_id = proc.stdout.splitlines()
    assert has_http == "False"
    assert data_mods == "[]"
    assert broker_id == "fyers"


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
    assert providers == ["02_data/data/provider/fyers/adapter.py"], providers
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


def test_credentials_schema_is_adapter_owned() -> None:
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
