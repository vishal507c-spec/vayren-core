"""M7 direct-UBL tests: application/data layers resolve through the single
``BrokerRegistry`` instead of the legacy shims.

Covers the scoped-M7 migration surface: direct historical-face resolution
(the bootstrap path), Zerodha history-only advertisement, fail-closed
unsupported capabilities with no silent fallback, selection/derived-provider
consistency (Phase C invariant), registry-path trading + funds, and LIVE
safety. Existing ``test_shims.py``/``test_provider.py`` stay green and are
not duplicated here — these tests pin the NEW direct path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
for entry in ("00_app", "02_data", "07_risk", "08_execution", "09_broker"):
    sys.path.insert(0, str(ROOT / entry))

import data.provider.factory  # noqa: E402,F401 — seeds the zerodha record
import execution.broker.factory  # noqa: E402,F401 — seeds paper/sandbox
from app.services.broker_selection_service import (  # noqa: E402
    BrokerSelectionService,
    app_selection_store,
)
from data.provider.contract import Provider  # noqa: E402
from data.provider.credentials_store import provider_service  # noqa: E402
from data.provider.manager import ProviderCredentialsManager  # noqa: E402
from data.settings import DownloadSettings  # noqa: E402
from execution.broker.adapter import BrokerAdapter  # noqa: E402
from execution.broker.factory import NotConfiguredError, resolve_broker  # noqa: E402
from execution.modes import ExecutionMode, ModeGates  # noqa: E402

from broker.capabilities import Caps, Domain  # noqa: E402
from broker.funds import require_funds  # noqa: E402
from broker.registry import default_registry  # noqa: E402
from broker.selection import BrokerSelection  # noqa: E402
from broker.vocab import (  # noqa: E402
    BrokerNotRegisteredError,
    UnsupportedCapabilityError,
)


def _settings(tmp_path: Path, provider: str) -> DownloadSettings:
    return DownloadSettings(data_dir=str(tmp_path), provider=provider)


def _resolve_history(settings: DownloadSettings) -> Provider:
    """The exact direct-UBL resolution the composition root now uses."""
    record = default_registry().get(settings.provider)
    face = record.plugin.face(Domain.HISTORICAL_DATA, settings)
    assert isinstance(face, Provider)
    return face


# ── A. direct historical resolution (bootstrap path) ───────────────────


def test_direct_registry_face_resolves_zerodha_history(tmp_path: Path) -> None:
    provider = _resolve_history(_settings(tmp_path, "zerodha"))
    assert type(provider).__name__ == "ZerodhaProvider"
    other = _resolve_history(_settings(tmp_path, "zerodha"))
    assert other is not provider  # fresh per call, like the legacy path


def test_direct_face_satisfies_provider_contract(tmp_path: Path) -> None:
    # Structural only — never touches the network (no available()/symbols()
    # calls; those stay behind explicit user action in the download engine).
    provider = _resolve_history(_settings(tmp_path, "zerodha"))
    assert isinstance(provider, Provider)
    for method in ("available", "symbols", "fetch_candles", "new_session", "renew"):
        assert callable(getattr(provider, method, None)), f"missing Provider method {method}"


def test_zerodha_advertises_history_only() -> None:
    record = default_registry().get("zerodha")
    assert record.faces == (Domain.HISTORICAL_DATA,)
    assert record.capabilities.supports(Caps.HIST_CANDLES)
    assert record.capabilities.supports(Caps.HIST_SYMBOLS)
    assert not record.capabilities.supports(Caps.ACCOUNT_FUNDS)


def test_zerodha_trading_face_unsupported() -> None:
    record = default_registry().get("zerodha")
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.TRADING)


def test_paper_history_unsupported_no_silent_fallback(tmp_path: Path) -> None:
    """Paper serves trading only — history must refuse, never borrow Zerodha."""
    record = default_registry().get("paper")
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.HISTORICAL_DATA, _settings(tmp_path, "paper"))


def test_sandbox_history_unsupported_no_silent_fallback(tmp_path: Path) -> None:
    record = default_registry().get("sandbox")
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.HISTORICAL_DATA, _settings(tmp_path, "sandbox"))


def test_unknown_broker_resolve_fails_closed() -> None:
    with pytest.raises(BrokerNotRegisteredError):
        default_registry().get("ghost-broker")


# ── B. selection ⇄ derived-provider consistency (Phase C invariant) ────


def test_derived_provider_matches_selection(tmp_path: Path) -> None:
    service = BrokerSelectionService(app_selection_store(tmp_path))
    selection = service.current()
    settings = DownloadSettings(data_dir=str(tmp_path), provider=selection.name)
    assert settings.provider == selection.name
    assert isinstance(selection, BrokerSelection)


def test_conflicting_independent_provider_cannot_silently_win(tmp_path: Path) -> None:
    """An independently built settings object never feeds back into the
    authoritative selection; the sandbox selection keeps failing history
    closed instead of silently using Zerodha."""
    service = BrokerSelectionService(app_selection_store(tmp_path))
    service.select("sandbox")
    rogue = _settings(tmp_path, "zerodha")  # independent, not derived
    assert service.current().name == "sandbox"  # selection untouched
    allowed, _reason = service.surface_allowed(Domain.HISTORICAL_DATA)
    assert not allowed
    _ = rogue  # rogue settings exist but decide nothing


def test_manager_service_key_follows_derived_provider(tmp_path: Path) -> None:
    service = BrokerSelectionService(app_selection_store(tmp_path))
    selection = service.current()
    settings = DownloadSettings(data_dir=str(tmp_path), provider=selection.name)
    provider = _resolve_history(settings)
    manager = ProviderCredentialsManager(settings, provider)
    assert manager._service == provider_service(selection.name)


# ── C. registry-path trading + funds ───────────────────────────────────


def test_paper_trading_face_via_registry() -> None:
    face = default_registry().get("paper").plugin.face(Domain.TRADING)
    assert isinstance(face, BrokerAdapter)
    assert "orders.market" in default_registry().get("paper").capabilities.items
    assert not default_registry().get("paper").capabilities.supports("orders.modify")


def test_sandbox_trading_face_via_registry() -> None:
    record = default_registry().get("sandbox")
    face = record.plugin.face(Domain.TRADING)
    assert isinstance(face, BrokerAdapter)
    assert record.capabilities.supports("orders.modify")
    assert record.capabilities.supports("stream.fills")


def test_paper_funds_via_registry_face() -> None:
    # Registry-level gating first (no invented probing): the record
    # advertises ACCOUNT_FUNDS, then the constructed face serves it.
    # (require_funds targets future method-style faces; legacy venues
    # expose capabilities as data, so the record gate is the authority.)
    record = default_registry().get("paper")
    assert record.capabilities.supports(Caps.ACCOUNT_FUNDS)
    face = record.plugin.face(Domain.TRADING)
    assert isinstance(face, BrokerAdapter)
    funds_of = getattr(face, "funds", None)
    assert callable(funds_of)
    assert set(cast(Any, funds_of)()) == {"available", "used", "equity"}


def test_sandbox_funds_via_registry_face() -> None:
    record = default_registry().get("sandbox")
    assert record.capabilities.supports(Caps.ACCOUNT_FUNDS)
    face = record.plugin.face(Domain.TRADING)
    assert isinstance(face, BrokerAdapter)
    funds_of = getattr(face, "funds", None)
    assert callable(funds_of)
    assert set(cast(Any, funds_of)()) == {"available", "used", "equity"}


def test_zerodha_funds_require_fails_closed() -> None:
    record = default_registry().get("zerodha")
    with pytest.raises(UnsupportedCapabilityError):
        require_funds(record.plugin)


def test_registry_discovers_funds_capable_venues() -> None:
    names = {record.name for record in default_registry().find_with(Caps.ACCOUNT_FUNDS)}
    assert {"paper", "sandbox"} <= names
    assert "zerodha" not in names


# ── D. LIVE safety ─────────────────────────────────────────────────────


def test_live_via_zerodha_remains_not_configured() -> None:
    # All five gates satisfied so the LIVE path is genuinely attempted —
    # Zerodha has no trading face, so resolution must fail closed.
    open_gates = ModeGates(
        live_trading_enabled=True,
        broker_live_enabled=True,
        account_confirmed=True,
        risk_limits_valid=True,
        kill_switch_off=True,
    )
    with pytest.raises(NotConfiguredError) as excinfo:
        resolve_broker(ExecutionMode.LIVE, open_gates, adapter_name="zerodha")
    assert "LIVE_BROKER_INTEGRATION = NOT_CONFIGURED" in str(excinfo.value)


def test_live_without_gates_downgrades_to_paper() -> None:
    broker, mode, notes = resolve_broker(ExecutionMode.LIVE, ModeGates())
    try:
        assert mode is ExecutionMode.PAPER
        assert len(notes) == 5
    finally:
        broker.disconnect()


def test_ubl_import_pulls_no_broker_sdk() -> None:
    """Fresh interpreter (order-independent): importing the UBL and the
    download engine pulls in NO broker SDK or concrete transport."""
    code = (
        "import sys\n"
        "import broker.registry, broker.faces, broker.capabilities, broker.vocab\n"
        "import data.downloader.engine\n"
        "forbidden = ['kiteconnect', 'data.provider.zerodha']\n"
        "loaded = [m for m in forbidden if m in sys.modules]\n"
        "assert not loaded, loaded\n"
        "print('broker-sdk-free')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "broker-sdk-free" in proc.stdout


def test_funds_paths_need_no_credentials_store() -> None:
    paper = default_registry().get("paper").plugin.face(Domain.TRADING)
    assert isinstance(paper, BrokerAdapter)
    funds_of = getattr(paper, "funds", None)
    assert callable(funds_of)
    funds = cast(Any, funds_of)()
    assert funds["available"] == pytest.approx(funds["equity"])
    assert funds["used"] == 0.0
