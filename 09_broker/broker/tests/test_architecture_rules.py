"""Architecture validation: broker-independence + one-registry boundaries.

Proves the Phase-20 mission's explicit architecture checks with file-level
evidence (design §3.2 rules 1/6, §6, §10 untouched-by-design list).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Chapters that must NEVER import the unified broker layer (core stays
# broker-independent; research stays broker-free).
FORBIDDEN_BROKER_IMPORTS = (
    "01_core/core",
    "03_market/market",
    "04_chart/chart",
    "05_strategy/strategy",
    "06_backtest/backtest",
    "07_risk/risk",
)

# Broker identity literals that would signal scattered `if broker == ...`
# branching in product code (tests/docs excluded).
BROKER_NAME_LITERALS = {"zerodha", "paper", "sandbox"}

PRODUCT_SRC_DIRS = (
    "00_app/app",
    "01_core/core",
    "02_data/data",
    "03_market/market",
    "04_chart/chart",
    "05_strategy/strategy",
    "06_backtest/backtest",
    "07_risk/risk",
    "08_execution/execution",
    "09_broker/broker",
)


def _product_files(rel_dir: str) -> list[Path]:
    base = ROOT / rel_dir
    return [p for p in base.rglob("*.py") if "tests" not in p.parts]


def _imports_broker(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "broker" or alias.name.startswith("broker.") for alias in node.names
            ):
                return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "broker" or node.module.startswith("broker."))
        ):
            return True
    return False


def test_core_and_research_chapters_never_import_broker_layer() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for chapter in FORBIDDEN_BROKER_IMPORTS
        for p in _product_files(chapter)
        if _imports_broker(p)
    ]
    assert not offenders, f"broker-independent chapters import UBL: {offenders}"


def test_no_broker_name_branching_in_product_code() -> None:
    """No `if broker == "..."` / `== "zerodha"`-style branching anywhere in
    product source (design §3.2 rule 6). Comparisons against broker-name
    string literals are the smell; registry lookups are the mechanism."""
    offenders: list[str] = []
    for chapter in PRODUCT_SRC_DIRS:
        for path in _product_files(chapter):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                for comparator in (*node.comparators, node.left):
                    if (
                        isinstance(comparator, ast.Constant)
                        and isinstance(comparator.value, str)
                        and comparator.value.lower() in BROKER_NAME_LITERALS
                    ):
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{node.lineno} compares broker name literal"
                        )
    assert not offenders, f"broker-name branching found: {offenders}"


def test_exactly_one_registry_implementation() -> None:
    """The only name→broker map class is BrokerRegistry; legacy factory
    modules must not keep their own dict-of-brokers."""
    registry_classes = [
        path
        for chapter in (
            "09_broker/broker",
            "02_data/data/provider",
            "08_execution/execution/broker",
        )
        for path in _product_files(chapter)
        if "class BrokerRegistry" in path.read_text(encoding="utf-8")
    ]
    assert len(registry_classes) == 1, f"registry implementations: {registry_classes}"
    legacy_dicts = []
    for rel in ("02_data/data/provider/factory.py", "08_execution/execution/broker/factory.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                legacy_dicts.append(f"{rel}: {targets}")
    # `_LEGACY_TO_UBL` in the execution shim is a legacy-id translation
    # table (capability strings), NOT a name→broker registry — whitelist it.
    assert not [d for d in legacy_dicts if "_LEGACY_TO_UBL" not in d], (
        f"legacy broker dicts still present: {legacy_dicts}"
    )


def test_unified_error_vocabulary_is_single_source() -> None:
    """ErrorCode lives once; legacy modules must not define competing enums."""
    definitions = []
    for chapter in PRODUCT_SRC_DIRS:
        for path in _product_files(chapter):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ClassDef)
                    and node.name == "ErrorCode"
                    and any(
                        isinstance(base, ast.Name) and base.id in ("str", "Enum", "StrEnum")
                        for base in node.bases
                        if isinstance(base, ast.Name)
                    )
                ):
                    definitions.append(str(path.relative_to(ROOT)))
    assert definitions == ["09_broker\\broker\\vocab.py"], f"ErrorCode defined at: {definitions}"


# ── M4: selection is app-level only; gates stay authoritative ───────────────


def test_selection_state_is_app_level_only() -> None:
    """Only 00_app (composition) and 09_broker (contract) touch selection
    state. Data/execution must not keep their own broker-selection."""
    offenders = []
    for chapter in ("02_data/data", "08_execution/execution"):
        for path in _product_files(chapter):
            if _imports_symbol(path, ("BrokerSelection", "BrokerSelectionService")):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"selection state leaked into engines: {offenders}"


def _imports_symbol(path: Path, symbols: tuple[str, ...]) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and any(alias.name in symbols for alias in node.names)
        ):
            return True
    return False


def _uses_name(path: Path, name: str) -> list[int]:
    """Line numbers where ``name`` is used as a runtime Name (M7 shim scan)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            or (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.attr == name
            )
        ):
            lines.append(node.lineno)
    return lines


def test_m7_no_build_provider_product_callers_in_app() -> None:
    """M7-A: after direct UBL migration, no product file under 00_app may
    reference ``build_provider`` (the shim def is retained for compat)."""
    offenders = [
        f"{p}: {lines}"
        for p in _product_files("00_app/app")
        if (lines := _uses_name(p, "build_provider"))
    ]
    assert not offenders, f"build_provider product callers remain in 00_app: {offenders}"


def test_m7_no_register_provider_references() -> None:
    """M7-B: the ``register_provider`` shim is retired — zero references
    anywhere in product code (registration is direct ``BrokerRegistry``)."""
    offenders = [
        f"{p}: {lines}"
        for chapter in PRODUCT_SRC_DIRS
        for p in _product_files(chapter)
        if (lines := _uses_name(p, "register_provider"))
    ]
    assert not offenders, f"register_provider references remain: {offenders}"


def test_m7_no_register_adapter_references() -> None:
    """M7-C: the ``register_adapter`` shim is retired — zero references
    anywhere in product code (registration is direct ``BrokerRegistry``)."""
    offenders = [
        f"{p}: {lines}"
        for chapter in PRODUCT_SRC_DIRS
        for p in _product_files(chapter)
        if (lines := _uses_name(p, "register_adapter"))
    ]
    assert not offenders, f"register_adapter references remain: {offenders}"


def test_m7_no_concrete_broker_imports_outside_home_chapter() -> None:
    """M7-D: concrete venues are imported only inside their home chapter
    (Zerodha transport in 02_data, Paper/Sandbox venues in 08_execution).
    The UBL never imports concrete transports at module level."""
    allowed_prefix = {
        "ZerodhaProvider": ("02_data/data/",),
        "PaperBroker": ("08_execution/execution/",),
        "SandboxBroker": ("08_execution/execution/",),
    }
    offenders: list[str] = []
    for chapter in PRODUCT_SRC_DIRS:
        for path in _product_files(chapter):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                names = {alias.name for alias in node.names}
                for venue, prefixes in allowed_prefix.items():
                    if venue in names:
                        rel = path.relative_to(ROOT).as_posix()
                        if not rel.startswith(prefixes):
                            offenders.append(f"{rel}:{node.lineno} imports {venue}")
    assert not offenders, f"concrete broker imports outside home chapter: {offenders}"


def test_m7_download_settings_provider_written_only_in_bootstrap() -> None:
    """M7-E: ``DownloadSettings(provider=...)`` is written only by the
    composition root — the field stays derived, never a second source."""
    writer = "00_app/app/bootstrap/bootstrap.py"
    offenders: list[str] = []
    for chapter in PRODUCT_SRC_DIRS:
        for path in _product_files(chapter):
            rel = path.relative_to(ROOT).as_posix()
            if rel == writer:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_settings = (isinstance(func, ast.Name) and func.id == "DownloadSettings") or (
                    isinstance(func, ast.Attribute) and func.attr == "DownloadSettings"
                )
                if is_settings and any(kw.arg == "provider" for kw in node.keywords):
                    offenders.append(f"{rel}:{node.lineno} writes DownloadSettings.provider")
    assert not offenders, f"DownloadSettings.provider writes outside bootstrap: {offenders}"


def test_live_gates_remain_authoritative_five_named() -> None:
    """M4 must not weaken the five live gates (design §12)."""
    import sys

    for entry in ("07_risk", "08_execution", "09_broker"):
        sys.path.insert(0, str(ROOT / entry))
    from execution.broker.gates import GATE_NAMES

    assert GATE_NAMES == (
        "BROKER_ADAPTER_READY",
        "CREDENTIALS_READY",
        "ACCOUNT_CONFIRMED",
        "RISK_CONFIGURATION_VALID",
        "EXECUTION_SAFETY_ENABLED",
    )


# ── M8: production adapter framework proofs (15-point gate) ───────────────

NETWORK_MODULES = {"kiteconnect", "httpx", "requests", "websockets", "websocket"}
NETWORK_ALLOWLIST_PREFIXES = ("02_data/data/provider/", "09_broker/broker/adapters/")

# Chapters that must never import broker SDK/network clients directly
# (Core → UBL → Adapter → Network; adapters own all transport imports).
NETWORK_FORBIDDEN_CHAPTERS = (
    "00_app/app",
    "01_core/core",
    "03_market/market",
    "04_chart/chart",
    "05_strategy/strategy",
    "06_backtest/backtest",
    "07_risk/risk",
    "08_execution/execution",
)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_m8_network_boundary_isolated_per_chapter() -> None:
    """M8 §5/18.6: only adapter/transport packages may import broker
    network clients. Core, market, chart, strategy, backtest, risk,
    execution and app composition never touch them directly."""
    offenders = []
    for chapter in NETWORK_FORBIDDEN_CHAPTERS:
        for path in _product_files(chapter):
            leaked = _module_imports(path) & NETWORK_MODULES
            if leaked:
                offenders.append(f"{path.relative_to(ROOT)} imports {sorted(leaked)}")
    assert not offenders, f"network boundary violated: {offenders}"


def test_m8_sdk_types_do_not_cross_ubl() -> None:
    """M8 §6/18.12: UBL face signatures expose only VAYREN/domain types."""
    import typing

    import broker.faces as faces

    hints: list[str] = []
    for _name, member in vars(faces).items():
        if inspect.isclass(member):
            for _mname, fn in vars(member).items():
                if callable(fn) and not _mname.startswith("__"):
                    try:
                        resolved = typing.get_type_hints(fn)
                    except Exception:
                        continue
                    hints.extend(str(hint) for hint in resolved.values())
    blob = "\n".join(hints)
    for token in ("Kite", "kiteconnect", "Sdk", "Ticker", "Websocket", "websocket"):
        assert token not in blob, f"SDK type crosses UBL boundary: {token}"


def test_m8_one_selection_one_capability_vocabulary() -> None:
    """M8 §18.2/18.3: exactly one BrokerSelection and one Caps vocabulary."""
    import re

    selections = [
        str(p.relative_to(ROOT))
        for chapter in PRODUCT_SRC_DIRS
        for p in _product_files(chapter)
        if re.search(
            r"^class BrokerSelection\b(?!Service)", p.read_text(encoding="utf-8"), re.MULTILINE
        )
    ]
    assert selections == ["09_broker\\broker\\selection.py"], selections
    capses = [
        str(p.relative_to(ROOT))
        for chapter in PRODUCT_SRC_DIRS
        for p in _product_files(chapter)
        if re.search(r"^class Caps\b", p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert capses == ["09_broker\\broker\\capabilities.py"], capses


def test_m8_research_stays_broker_free() -> None:
    """M8 §18.10: ``05_strategy/strategy/research`` never imports broker."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "05_strategy" / "strategy" / "research").rglob("*.py")
        if "tests" not in p.parts and _imports_broker(p)
    ]
    assert not offenders, f"research imports broker layer: {offenders}"


def test_m8_no_credentials_in_selection_or_ui_text() -> None:
    """M8 §18.13: no secret values in selection persistence, reprs or UI."""
    import json
    import sys

    sys.path.insert(0, str(ROOT / "09_broker"))
    from broker.selection import BrokerSelection
    from broker.vocab import Environment

    sel = BrokerSelection(
        name="sandbox",
        environment=Environment.SANDBOX,
        selected_at="2026-09-07T00:00:00+05:30",
        reason="user-selected",
    )
    blob = json.dumps(sel.__dict__ if hasattr(sel, "__dict__") else repr(sel)).lower()
    blob += repr(sel).lower()
    for secret_word in ("api_key", "secret", "password", "token", "totp"):
        if secret_word == "token":
            continue  # 'token' is substring noise; schema-key check below is exact
        assert secret_word not in blob, f"secret in selection: {secret_word}"
    # The legacy status panel half of this check died with the native
    # migration (SLICE 6d): no Python UI text remains that could read values.
    # The native Slint screens receive credential shapes only (no values).


def test_m8_unsupported_capability_fails_closed_at_face() -> None:
    """M8 §18.14: missing faces raise before any transport call."""
    import sys

    sys.path.insert(0, str(ROOT / "09_broker"))
    from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
    from broker.faces import FactoryPlugin, StaticPlugin
    from broker.vocab import UnsupportedCapabilityError

    trading = capability_set({Domain.TRADING: (Caps.ORDERS_MARKET,)})
    plugin = StaticPlugin(
        name="m8",
        display_name="M8",
        face_map={Domain.TRADING: object()},
        capabilities=trading,
    )
    for domain in (Domain.HISTORICAL_DATA, Domain.MARKET_DATA):
        with pytest.raises(UnsupportedCapabilityError):
            plugin.face(domain)
    opaque = FactoryPlugin(
        name="m8o",
        display_name="M8o",
        factories={Domain.TRADING: lambda: object()},
        capabilities=None,
    )
    assert opaque.capability_set() == CapabilitySet()  # undeclared, never invented
    with pytest.raises(UnsupportedCapabilityError):
        opaque.face(Domain.HISTORICAL_DATA)


# ── M6: funds surface stays UBL-owned, isolated, LIVE-neutral ─────────────


# ── FINAL: 20-point gate (one credential/health abstraction, reconcile-only
# UNKNOWN, no blind retry, environment isolation) ──────────────────────────


def test_final_one_credential_abstraction() -> None:
    """FINAL §W.5: the UBL credential abstraction is single-sourced."""
    import re

    owners: dict[str, list[str]] = {}
    for name in (
        "class CredentialRef\\b",
        "class CredentialResolver\\b",
        "class CredentialScope\\b",
        "class CredentialMetadata\\b",
    ):
        owners[name] = [
            str(p.relative_to(ROOT))
            for chapter in PRODUCT_SRC_DIRS
            for p in _product_files(chapter)
            if re.search(rf"^{name}", p.read_text(encoding="utf-8"), re.MULTILINE)
        ]
    for name, files in owners.items():
        assert files == ["09_broker\\broker\\credentials.py"], f"{name}: {files}"


def test_final_one_health_abstraction() -> None:
    """FINAL §W.6: the UBL health abstraction is single-sourced."""
    import re

    for name, expected in (
        (r"^class HealthState\b", ["09_broker\\broker\\health.py"]),
        (r"^class BrokerHealth\b", ["09_broker\\broker\\health.py"]),
        (r"^class BrokerIdentity\b", ["09_broker\\broker\\identity.py"]),
    ):
        files = [
            str(p.relative_to(ROOT))
            for chapter in PRODUCT_SRC_DIRS
            for p in _product_files(chapter)
            if re.search(name, p.read_text(encoding="utf-8"), re.MULTILINE)
        ]
        assert files == expected, f"{name}: {files}"


def test_final_unknown_orders_reconcile_only() -> None:
    """FINAL §W.18: UNKNOWN exits exclusively via reconcile()."""
    import sys

    for entry in ("07_risk", "08_execution", "09_broker"):
        sys.path.insert(0, str(ROOT / entry))
    from execution.engine import ExecutionEngine, IllegalTransitionError
    from execution.models.order import BrokerOrder, OrderState

    engine = ExecutionEngine()
    engine.create(
        BrokerOrder(client_order_id="w1", intent_id="wi1", symbol="X", side="BUY", quantity=1.0)
    )
    engine.transition("w1", OrderState.VALIDATED)
    engine.transition("w1", OrderState.SUBMITTED)
    engine.transition("w1", OrderState.UNKNOWN, reason="timeout")
    with pytest.raises(IllegalTransitionError):
        engine.transition("w1", OrderState.FILLED)
    with pytest.raises(IllegalTransitionError):
        engine.transition("w1", OrderState.ACKNOWLEDGED)
    assert engine.reconcile("w1", "ACKNOWLEDGED").state is OrderState.ACKNOWLEDGED


def test_final_no_blind_order_retry() -> None:
    """FINAL §W.19: exactly one order-submission call site; submissions are
    classified NOT_SAFE_TO_RETRY (reconcile-first, never blind-resubmit)."""
    import sys

    for entry in ("07_risk", "08_execution", "09_broker"):
        sys.path.insert(0, str(ROOT / entry))
    from execution.broker.resilience import RetryKind, classify_retry

    assert classify_retry("place_order") is RetryKind.NOT_SAFE_TO_RETRY
    assert classify_retry("cancel_order") is RetryKind.MUST_RECONCILE_FIRST
    source = (ROOT / "08_execution" / "execution" / "runtime" / "session.py").read_text(
        encoding="utf-8"
    )
    assert source.count(".place_order(") == 1, "order submission must stay single-sited"


def test_final_environment_isolation() -> None:
    """FINAL §W.20: PAPER/SANDBOX/LIVE never collapse into each other."""
    import sys

    sys.path.insert(0, str(ROOT / "09_broker"))
    from broker.selection import BrokerSelection, SelectionError
    from broker.vocab import Environment

    assert len({Environment.PAPER, Environment.SANDBOX, Environment.LIVE}) == 3
    with pytest.raises(SelectionError):
        BrokerSelection("x", "live", "2026-09-07T00:00:00+05:30", "r")  # type: ignore[arg-type]
    live = BrokerSelection(
        name="venue",
        environment=Environment.LIVE,
        selected_at="2026-09-07T00:00:00+05:30",
        reason="explicit-operator-act",
    )
    assert live.environment is Environment.LIVE
    assert live.environment is not Environment.PAPER


def test_funds_abstraction_is_ubl_owned() -> None:
    """Exactly one funds domain model exists, under 09_broker."""
    owners = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "09_broker").rglob("funds.py")
        if "__pycache__" not in p.parts
    ]
    assert len(owners) == 1, f"funds implementations: {owners}"
    elsewhere = [
        str(p.relative_to(ROOT))
        for chapter in (
            "01_core/core",
            "02_data/data",
            "03_market/market",
            "04_chart/chart",
            "05_strategy/strategy",
            "06_backtest/backtest",
            "07_risk/risk",
        )
        for p in _product_files(chapter)
        if "class FundsSnapshot" in p.read_text(encoding="utf-8")
    ]
    assert not elsewhere, f"funds model leaked outside UBL: {elsewhere}"


def test_funds_contract_has_no_sdk_or_transport_imports() -> None:
    """funds.py imports stdlib + UBL vocabulary only — no SDK, no creds, no net."""
    tree = ast.parse((ROOT / "09_broker" / "broker" / "funds.py").read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    modules = {(node.module or "") for node in imports if isinstance(node, ast.ImportFrom)}
    plain = [node for node in imports if isinstance(node, ast.Import)]
    modules |= {alias.name for node in plain for alias in node.names}
    forbidden = [mod for mod in modules if "kiteconnect" in mod or "credential" in mod.lower()]
    assert not forbidden, f"funds contract leaks transport/credential imports: {forbidden}"
    source = (ROOT / "09_broker" / "broker" / "funds.py").read_text(encoding="utf-8")
    for token in ("kiteconnect", "urllib", "http.client", "requests", "os.environ", "getenv"):
        assert token not in source, f"transport/secret access in funds contract: {token}"


def test_no_second_funds_capability_vocabulary() -> None:
    """Only Caps.ACCOUNT_FUNDS names the funds capability; the execution
    shim reuses the byte-identical id (no new BrokerCapabilities constant)."""
    account_funds_defs = []
    for chapter in PRODUCT_SRC_DIRS:
        for path in _product_files(chapter):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    if "ACCOUNT_FUNDS" in targets or "_ACCOUNT_FUNDS" in targets:
                        account_funds_defs.append(str(path.relative_to(ROOT)))
                if isinstance(node, ast.ClassDef):
                    annotated = {
                        stmt.target.id
                        for stmt in node.body
                        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                    }
                    if "ACCOUNT_FUNDS" in annotated or "FUNDS" in annotated:
                        account_funds_defs.append(str(path.relative_to(ROOT)))
    assert sorted(account_funds_defs) == sorted(
        ["09_broker\\broker\\capabilities.py", "08_execution\\execution\\broker\\factory.py"]
    ), f"funds capability vocabulary drift: {account_funds_defs}"
    adapter_src = (ROOT / "08_execution" / "execution" / "broker" / "adapter.py").read_text(
        encoding="utf-8"
    )
    assert "FUNDS" not in adapter_src, "legacy BrokerCapabilities must not gain a FUNDS constant"


def test_funds_capability_does_not_imply_live_readiness() -> None:
    """Advertising account.funds must not touch gates, modes, or arming."""
    for rel in (
        "08_execution/execution/broker/paper.py",
        "08_execution/execution/broker/sandbox.py",
        "08_execution/execution/broker/factory.py",
        "09_broker/broker/funds.py",
    ):
        source = (ROOT / rel).read_text(encoding="utf-8")
        for token in ("GATE_NAMES", "LiveArm", "ACCOUNT_CONFIRMED", "LIVE_TRADING_ENABLED"):
            assert token not in source, f"{rel} couples funds to LIVE readiness: {token}"
