"""M5 Zerodha adapter tests: identity, capability matrix, registration,
delegation, SDK isolation (design §5–§7, §9 items 1–10).

Strategy: transport stays exactly once in ``02_data/data/provider/zerodha/``;
this suite pins the UBL contract surface in
``09_broker/broker/adapters/zerodha/`` and the absence of duplication,
leakage, aliases and phantom capabilities.
"""

from __future__ import annotations

import ast
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
from data.provider.factory import build_provider  # noqa: E402
from data.provider.zerodha import ZerodhaProvider  # noqa: E402
from data.settings import DownloadSettings  # noqa: E402

from broker.adapters.zerodha import (  # noqa: E402
    BROKER_ID,
    DISPLAY_NAME,
    HISTORICAL_CAPABILITIES,
)
from broker.capabilities import Caps, Domain  # noqa: E402
from broker.registry import default_registry  # noqa: E402
from broker.selection import BrokerSelection  # noqa: E402
from broker.vocab import Environment, UnsupportedCapabilityError  # noqa: E402

ADAPTER_DIR = ROOT / "09_broker" / "broker" / "adapters" / "zerodha"
TS = "2026-09-07T00:00:00+05:30"


def _settings(tmp_path, provider: str = BROKER_ID) -> DownloadSettings:
    return DownloadSettings(data_dir=str(tmp_path), provider=provider)


# ── §10 identity: one stable id, no aliases ───────────────────────────────


def test_broker_id_is_stable_and_single() -> None:
    assert BROKER_ID == "zerodha"
    assert DISPLAY_NAME == "Zerodha"
    # The three product identity sites resolve to the same constant.
    assert DownloadSettings.__dataclass_fields__["provider"].default == BROKER_ID
    from app.services.broker_selection_service import DEFAULT_HISTORY_BROKER

    assert DEFAULT_HISTORY_BROKER == BROKER_ID
    assert ZerodhaProvider.name == BROKER_ID


def test_no_alias_ids_in_product_code() -> None:
    """Only two spellings may exist as values: the id ``"zerodha"`` and the
    UI label ``"Zerodha"`` (the latter solely in display-name assignments
    plus pinned pre-existing UI labels — display text, never lookup
    keys, tracked here so no new alias site slips in).
    ``kite``/``kiteconnect``/``ZERODHA`` must never appear as values —
    the SDK is referenced by module import only, never by name string."""
    chapters = (
        "01_core/core",
        "02_data/data",
        "03_market/market",
        "04_chart/chart",
        "05_strategy/strategy",
        "06_backtest/backtest",
        "07_risk/risk",
        "08_execution/execution",
        "09_broker/broker",
        "00_app/app",
    )
    display_label_sites = {
        "00_app/app/bootstrap/bootstrap.py",
        "02_data/data/ui/status_view.py",
        # The native market view mirrors the pinned provider-card text (display only).
        "00_app/app/services/slint_market_host.py",
    }
    offenders = []
    for chapter in chapters:
        chapter_dir = ROOT / chapter
        if not chapter_dir.is_dir():
            continue
        paths = [p for p in chapter_dir.rglob("*.py") if "tests" not in p.parts]
        for path in paths:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            # Map child -> parent for display-name assignment detection.
            parents: dict[int, ast.AST] = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[id(child)] = node
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                lowered = node.value.lower()
                if lowered not in {"zerodha", "kite", "kiteconnect"} or node.value == "zerodha":
                    continue
                parent = parents.get(id(node))
                is_display_assignment = (
                    isinstance(parent, ast.Assign)
                    and node.value == "Zerodha"
                    and any(
                        isinstance(t, ast.Name) and t.id.lower() == "display_name"
                        for t in parent.targets
                    )
                )
                rel = path.relative_to(ROOT).as_posix()
                is_pinned_label = node.value == "Zerodha" and rel in display_label_sites
                if not (is_display_assignment or is_pinned_label):
                    offenders.append(f"{rel}:{node.lineno}: {node.value!r}")
    assert not offenders, f"broker id aliases found: {offenders}"


# ── §6 capability matrix: history YES, everything else NO ─────────────────


def test_capability_matrix_exact() -> None:
    assert HISTORICAL_CAPABILITIES.items == frozenset({Caps.HIST_CANDLES, Caps.HIST_SYMBOLS})
    for domain in (Domain.TRADING, Domain.MARKET_DATA):
        assert not HISTORICAL_CAPABILITIES.supports_domain(domain)


def test_registry_record_serves_history_only() -> None:
    import data.provider.factory  # noqa: F401 — seeds the record

    record = default_registry().get(BROKER_ID)
    assert record.name == BROKER_ID
    assert record.display_name == DISPLAY_NAME
    assert record.faces == (Domain.HISTORICAL_DATA,)
    assert record.capabilities == HISTORICAL_CAPABILITIES
    names = {r.name for r in default_registry().find_with_domain(Domain.HISTORICAL_DATA)}
    assert BROKER_ID in names
    assert BROKER_ID not in {r.name for r in default_registry().find_with_domain(Domain.TRADING)}
    assert BROKER_ID not in {
        r.name for r in default_registry().find_with_domain(Domain.MARKET_DATA)
    }


def test_unsupported_faces_fail_closed(tmp_path) -> None:
    import data.provider.factory  # noqa: F401

    record = default_registry().get(BROKER_ID)
    face = record.plugin.face(Domain.HISTORICAL_DATA, _settings(tmp_path))
    assert isinstance(face, ZerodhaProvider)
    other = record.plugin.face(Domain.HISTORICAL_DATA, _settings(tmp_path))
    assert other is not face  # fresh per call, like the legacy dict path
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.TRADING)
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.MARKET_DATA)


def test_provider_exposes_ubl_surface() -> None:
    provider = ZerodhaProvider.__new__(ZerodhaProvider)
    assert provider.name == BROKER_ID
    assert provider.capabilities() == HISTORICAL_CAPABILITIES


def test_error_semantics_preserved_without_network(tmp_path) -> None:
    """Bad interval fails fast with the normalized code — no network."""
    provider = build_provider(_settings(tmp_path))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch_candles("RELIANCE", "9m", None, None)  # type: ignore[arg-type]
    assert excinfo.value.code == "INVALID_REQUEST"


# ── §4 one implementation, no duplication ─────────────────────────────────


def _repo_py_files() -> list[Path]:
    return [
        p
        for p in ROOT.rglob("*.py")
        if ".venv" not in p.parts and "99_archive" not in p.parts and "__pycache__" not in p.parts
    ]


def _rel_posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_no_duplicate_zerodha_implementation() -> None:
    providers = [
        _rel_posix(p)
        for p in _repo_py_files()
        if "tests" not in p.parts
        and "class ZerodhaProvider" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert providers == ["02_data/data/provider/zerodha/adapter.py"], providers
    kite_maps = [
        _rel_posix(p)
        for p in _repo_py_files()
        if "tests" not in p.parts and "KITE_INTERVAL_IDS" in p.read_text(encoding="utf-8")
    ]
    assert kite_maps == ["02_data/data/provider/zerodha/adapter.py"], kite_maps
    for cls in ("class AuthEngine", "class FetchEngine", "class InstrumentResolver"):
        found = [
            _rel_posix(p)
            for p in _repo_py_files()
            if "tests" not in p.parts and cls in p.read_text(encoding="utf-8")
        ]
        assert len(found) == 1, f"{cls}: {found}"


def test_plugin_record_builder_uses_injected_factory() -> None:
    """The adapter package builds registry records without importing the
    transport layer (constructor injection — no broker→data edge)."""
    from broker.adapters.zerodha import zerodha_plugin_record as build_record

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
    no transport classes, no interval maps, no credential handling."""
    assert ADAPTER_DIR.is_dir()
    sources = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted(ADAPTER_DIR.rglob("*.py"))
        if "__pycache__" not in p.parts
    )
    for token in (
        "class ZerodhaProvider",
        "class AuthEngine",
        "class FetchEngine",
        "KITE_INTERVAL_IDS",
        "minute",
        "api_key",
        "os.environ",
        "getenv",
    ):
        assert token not in sources, f"business logic leaked into adapter package: {token}"


# ── §7 SDK isolation ──────────────────────────────────────────────────────


def test_adapter_import_pulls_no_sdk_and_no_data_layer() -> None:
    """Fresh interpreter: importing the adapter package must not touch the
    broker SDK, the transport layer, or any credential machinery."""
    code = (
        "import sys, broker.adapters.zerodha as a; "
        "mods = set(sys.modules); "
        "print('kiteconnect' in mods); "
        "print('pyotp' in mods); "
        "print('selenium' in mods); "
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
    has_kite, has_pyotp, has_selenium, data_mods, broker_id = proc.stdout.splitlines()
    assert has_kite == "False"
    assert has_pyotp == "False"
    assert has_selenium == "False"
    assert data_mods == "[]"
    assert broker_id == "zerodha"


def test_no_concrete_zerodha_imports_outside_boundary() -> None:
    """Concrete-implementation references are pinned to an explicit list.

    Readers: ``02_data/data/provider/factory.py`` (UBL seed wiring),
    ``02_data/data/settings.py`` (single-sourced default id),
    ``02_data/data/provider/zerodha/**`` (the implementation itself),
    ``02_data/data/provider/fyers/**`` (the second venue's implementation),
    ``00_app/.../broker_selection_service.py`` (compatibility default id).
    Anything else importing the concrete transport or the adapter package
    fails here — discovery must go through the registry.
    """
    allowed_files = {
        "02_data/data/provider/factory.py",
        "02_data/data/settings.py",
        "00_app/app/services/broker_selection_service.py",
    }
    allowed_prefixes = (
        "02_data/data/provider/zerodha/",
        "02_data/data/provider/fyers/",
        "09_broker/broker/adapters/",
    )
    offenders = []
    for path in _repo_py_files():
        if "tests" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed_files or rel.startswith(allowed_prefixes):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("data.provider.zerodha", "broker.adapters")):
                        mod = alias.name
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith(("data.provider.zerodha", "broker.adapters"))
            ):
                mod = node.module
            if mod:
                offenders.append(f"{rel} -> {mod}")
    assert not offenders, f"concrete zerodha imports outside boundary: {offenders}"


# ── §13 selection E2E (no network) ────────────────────────────────────────


def test_selection_drives_historical_face(tmp_path) -> None:
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
    assert allowed, reason
    face = record.plugin.face(Domain.HISTORICAL_DATA, _settings(tmp_path))
    assert isinstance(face, ZerodhaProvider)
    ready, why = face.available()
    assert isinstance(ready, bool) and isinstance(why, str)


def test_selection_refuses_trading_face() -> None:
    import data.provider.factory  # noqa: F401

    from broker.selection import surface_resolution

    selection = BrokerSelection(
        name=BROKER_ID,
        environment=Environment.PAPER,
        selected_at=TS,
        reason="user-selected",
    )
    record = default_registry().get(selection.name)
    allowed, reason = surface_resolution(selection, record.capabilities, Domain.TRADING)
    assert not allowed
    assert "does not provide trading capability" in reason
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.TRADING)


# ── §12 LIVE stays NOT_CONFIGURED ─────────────────────────────────────────


def test_zerodha_advertises_no_trading_adapter_to_execution() -> None:
    """The execution broker boundary must fail closed for zerodha: the
    unified record has no trading face, so resolution raises
    NotConfiguredError — LIVE_BROKER_INTEGRATION stays NOT_CONFIGURED."""
    import data.provider.factory  # noqa: F401 — seeds the zerodha record
    from execution.broker.factory import NotConfiguredError, resolve_broker
    from execution.modes import ExecutionMode, ModeGates

    with pytest.raises(NotConfiguredError) as excinfo:
        resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name=BROKER_ID)
    assert "LIVE_BROKER_INTEGRATION = NOT_CONFIGURED" in str(excinfo.value)


def test_credentials_schema_untouched() -> None:
    """M5 moves nothing credential-related: schema identical, values absent."""
    assert [f.key for f in ZerodhaProvider.credential_fields] == [
        "api_key",
        "api_secret",
        "user_id",
        "password",
        "totp_secret",
    ]
    assert ZerodhaProvider.display_name == "Zerodha"
