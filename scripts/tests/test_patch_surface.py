"""Intent -> patch surface tests (Phase 3 matrix §17 + golden tasks §18).

Read-only against the live warm index (no repo writes). Goldens pin real
repository behavior: task -> route -> target -> symbol -> tests ->
validation -> forbidden boundary. No subjective scoring.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import patch_surface  # noqa: E402
from patch_surface import (  # noqa: E402
    detect_ambiguity,
    resolve,
    validation_scope,
)
from route import load_routes, match_route  # noqa: E402

ROOT = SCRIPTS_DIR.parent

# task -> expected route -> expected target file -> expected symbol ->
# expected tests signal -> expected validation scope -> forbidden sample.
GOLDENS = [
    {
        "task": "add strategy parameter",
        "route": "strategy-change",
        "target": "05_strategy/strategy/registry.py",
        "symbol": "strategy.registry:StrategyRegistry",
        "tests": "validators + pyright",
        "scope": "contract",
        "forbidden": "09_broker/broker/*",
    },
    {
        "task": "add validation to broker registry",
        "route": "broker-ubl",
        "target": "09_broker/broker/registry.py",
        "symbol": "broker.registry:BrokerRegistry",
        "tests": "pytest 09_broker/broker/tests -q",
        "scope": "contract",
        "forbidden": "05_strategy/strategy/*",
    },
    {
        "task": "add timeframe",
        "route": "market-read",
        "target": "03_market/market/models/bar.py",
        "symbol": "market.models.bar:Bar",
        "tests": "cargo test -p vayren-core --lib market",
        "scope": "full",
        "forbidden": "rust/vayren-shell/*",
    },
    {
        "task": "backtest drawdown",
        "route": "backtest",
        "target": "rust/vayren-core/src/metrics.rs",
        "symbol": "rust::vayren-core::src::metrics::max_drawdown",
        "tests": "cargo test -p vayren-core --lib backtest",
        "scope": "full",
        "forbidden": "rust/vayren-shell/*",
    },
    {
        "task": "market screen",
        "route": "ui-screen",
        "target": "rust/vayren-shell/src/market.rs",
        "symbol": "rust::vayren-shell::src::market::MarketBar",
        "tests": "cargo test -p vayren-shell",
        "scope": "contract",
        "forbidden": "rust/vayren-core/src/*",
    },
    {
        "task": "ai boundary",
        "route": "core-ai",
        "target": "01_core/core/ai/boundary.py",
        "symbol": "core.ai.boundary:AiBoundary",
        "tests": "validate_language_ownership",
        "scope": "contract",
        "forbidden": "rust/vayren-core/src/*",
    },
    {
        "task": "download settings",
        "route": "download-config",
        "target": "02_data/data/settings.py",
        "symbol": "data.settings:DownloadSettings",
        "tests": "pytest 02_data/data/tests/test_settings.py -q",
        "scope": "contract",
        "forbidden": "rust/vayren-core/src/*",
    },
]


def _packet(task: str, **kwargs: str | list[str]) -> dict:
    if "route" in kwargs:
        kwargs["route_key"] = kwargs.pop("route")
    return resolve(task, **kwargs)  # type: ignore[arg-type]


def test_exact_file_intent() -> None:
    packet = _packet("", file="09_broker/broker/registry.py")
    assert packet["status"] == "RESOLVED"
    assert packet["resolution"]["kind"] == "file"
    assert [t["file"] for t in packet["targets"]] == ["09_broker/broker/registry.py"]
    quals = [s["qualified"] for t in packet["targets"] for s in t["symbols"]]
    assert "broker.registry:BrokerRegistry" in quals


def test_exact_symbol_intent() -> None:
    packet = _packet("", symbol="BrokerRegistry")
    assert packet["status"] == "RESOLVED"
    assert packet["resolution"]["kind"] == "symbol"
    assert [t["file"] for t in packet["targets"]] == ["09_broker/broker/registry.py"]
    symbol = next(
        s
        for t in packet["targets"]
        for s in t["symbols"]
        if s["qualified"] == "broker.registry:BrokerRegistry"
    )
    assert symbol["line"] == 68
    assert symbol["module"] == "09_broker/broker"
    assert symbol["owner"] == "BROKER_CONTRACT"


def test_qualified_symbol_disambiguates() -> None:
    packet = _packet("", symbol="market.models.bar:Bar")
    assert packet["status"] == "RESOLVED"
    assert [t["file"] for t in packet["targets"]] == ["03_market/market/models/bar.py"]


def test_module_intent() -> None:
    packet = _packet("", module="09_broker/broker")
    assert packet["status"] == "RESOLVED"
    assert packet["resolution"]["kind"] == "module"
    assert packet["route"]["key"] == "broker-ubl"
    assert "09_broker/broker/registry.py" in [t["file"] for t in packet["targets"]]
    assert not [t for t in packet["targets"] if t["is_test"]]


def test_route_intent() -> None:
    packet = _packet("", route="broker-ubl")
    assert packet["status"] == "RESOLVED"
    assert packet["resolution"] == {"kind": "route", "by": "exact-key"}
    assert packet["route"]["key"] == "broker-ubl"


def test_natural_language_deterministic_intent() -> None:
    packet = _packet("add strategy parameter")
    assert packet["status"] == "RESOLVED"
    assert packet["route"]["key"] == "strategy-change"
    assert packet["resolution"]["by"] == "alias-substring"


def test_exact_target_file_in_route_packet() -> None:
    packet = _packet("add validation to broker registry")
    assert packet["status"] == "RESOLVED"
    assert "09_broker/broker/registry.py" in [t["file"] for t in packet["targets"]]


def test_exact_symbol_current_line() -> None:
    packet = _packet("add validation to broker registry")
    registry = next(t for t in packet["targets"] if t["file"] == "09_broker/broker/registry.py")
    symbol = next(
        s for s in registry["symbols"] if s["qualified"] == "broker.registry:BrokerRegistry"
    )
    assert symbol["line"] == 68
    assert symbol["kind"] == "class"
    assert symbol["public"] is True


def test_caller_resolution() -> None:
    packet = _packet("add validation to broker registry")
    registry = next(t for t in packet["targets"] if t["file"] == "09_broker/broker/registry.py")
    symbol = next(
        s for s in registry["symbols"] if s["qualified"] == "broker.registry:BrokerRegistry"
    )
    assert "broker.registry:default_registry" in symbol["callers"]
    assert "09_broker/broker/registry.py" in packet["impact"]["caller_files"]
    assert symbol["callers"] == sorted(symbol["callers"])


def test_dependent_resolution() -> None:
    packet = _packet("add strategy parameter")
    dependents = packet["impact"]["dependents"]
    assert "06_backtest/backtest" in dependents.get("05_strategy/strategy", [])
    assert "00_app/app" in dependents.get("05_strategy/strategy", [])
    actual = packet["impact"]["dependents_actual"]
    assert "06_backtest/backtest" in actual.get("05_strategy/strategy", [])
    assert "05_strategy/strategy" in packet["impact"]["affected_modules"]
    assert len(packet["impact"]["affected_modules"]) <= 8


def test_affected_module_resolution_bounded() -> None:
    packet = _packet("add validation to broker registry")
    affected = packet["impact"]["affected_modules"]
    assert "09_broker/broker" in affected
    assert len(affected) <= 8
    assert "rust/vayren-shell" not in affected


def test_direct_test_resolution() -> None:
    packet = _packet("add validation to broker registry")
    assert packet["tests"]["direct"] or packet["tests"]["module"]
    assert packet["tests"]["route"]["command"] == "pytest 09_broker/broker/tests -q"


def test_module_test_resolution() -> None:
    packet = _packet("download settings")
    assert packet["status"] == "RESOLVED"
    module_tests = packet["tests"]["module"]
    assert "02_data/data/tests/test_settings.py" in module_tests


def test_contract_test_and_reference() -> None:
    packet = _packet("add timeframe")
    assert packet["route"]["contract"] == "90_brain/module_contracts.md section 5.4"
    assert packet["evidence"]["contract"] == "90_brain/module_contracts.md section 5.4"
    assert "cargo test -p vayren-core --lib market" in packet["validation"]["commands"]


def test_must_change() -> None:
    packet = _packet("add validation to broker registry")
    must = packet["boundaries"]["must_change"]
    assert "09_broker/broker/registry.py" in must
    assert all((ROOT / path).is_file() for path in must)


def test_may_change_bounded() -> None:
    packet = _packet("add validation to broker registry")
    may = packet["boundaries"]["may_change"]
    assert isinstance(may, list)
    assert not set(may) & set(packet["boundaries"]["must_change"])
    assert all((ROOT / path).is_file() for path in may)


def test_must_not_change_from_route() -> None:
    packet = _packet("add validation to broker registry")
    must_not = packet["boundaries"]["must_not_change"]
    assert "05_strategy/strategy/*" in must_not["patterns"]
    assert must_not["file_count"] >= 0
    assert not (set(packet["boundaries"]["must_change"]) & set(must_not["files"]))


def test_ownership_violation_blocked() -> None:
    packet = _packet("risk engine", modify=["07_risk/risk/new_logic.py"])
    assert packet["status"] == "BLOCKED"
    violation = packet["safety"]["gate"]["violations"][0]
    assert violation["target"] == "07_risk/risk/new_logic.py"
    assert violation["canonical_owner"] == "RUST_RISK"
    assert violation["canonical_language"] == "RUST"
    assert "Rust-owned" in violation["reason"]
    assert "risk_engine" in violation["allowed"]


def test_language_violation_blocked() -> None:
    packet = _packet("add strategy parameter", modify=["05_strategy/strategy/new_kernel.rs"])
    assert packet["status"] == "BLOCKED"
    violation = packet["safety"]["gate"]["violations"][0]
    assert violation["canonical_owner"] == "PYTHON_STRATEGY"
    assert violation["canonical_language"] == "PYTHON"
    assert "Python-owned" in violation["reason"]


def test_retained_bridge_not_blocked() -> None:
    packet = _packet("risk engine", modify=["07_risk/risk/native_engine.py"])
    assert packet["status"] == "RESOLVED"


def test_forbidden_modify_blocked() -> None:
    packet = _packet("add validation to broker registry", modify=["05_strategy/strategy/sma.py"])
    assert packet["status"] == "BLOCKED"
    assert "forbidden" in packet["safety"]["gate"]["violations"][0]["reason"]


def test_cross_module_boundary() -> None:
    packet = _packet("add timeframe")
    assert packet["status"] == "RESOLVED"
    assert "01_core/core" in packet["impact"]["dependencies"].get("03_market/market", [])
    assert packet["validation"]["scope"] in ("ownership", "full")


def test_contract_boundary_scope() -> None:
    packet = _packet("add strategy parameter")
    assert packet["validation"]["scope"] in ("contract", "ownership", "full")
    assert packet["route"]["contract"].startswith("90_brain/module_contracts.md")


def test_stale_index_unknown() -> None:
    import repo_index  # noqa: E402

    real = repo_index.ensure_fresh

    def _broken() -> dict:
        raise repo_index.IndexError("simulated refresh failure")

    repo_index.ensure_fresh = _broken  # type: ignore[assignment]
    try:
        packet = _packet("add strategy parameter")
    finally:
        repo_index.ensure_fresh = real  # type: ignore[assignment]
    assert packet["status"] == "UNKNOWN"
    assert "stale index" in packet["unknown"]["reason"]


def test_ambiguous_route() -> None:
    packet = _packet("Add validation to StrategyRegistry registration")
    assert packet["status"] == "NEEDS_CLARIFICATION"
    assert "broker-ubl" in packet["unknown"]["needed"]
    assert "strategy-change" in packet["unknown"]["needed"]


def test_unknown_route() -> None:
    packet = _packet("zzz no such task anywhere")
    assert packet["status"] == "UNKNOWN"
    assert packet["unknown"]["reason"] == "no route matches this task"
    assert packet["unknown"]["needed"]


def test_unresolved_symbol() -> None:
    packet = _packet("", symbol="NoSuchSymbolAnywhere")
    assert packet["status"] == "UNKNOWN"
    assert "not found in index" in packet["unknown"]["reason"]


def test_unknown_module() -> None:
    packet = _packet("", module="99_ghost/ghost")
    assert packet["status"] == "UNKNOWN"
    assert "not in index" in packet["unknown"]["reason"]


def test_unknown_route_key() -> None:
    packet = _packet("", route="no-such-route")
    assert packet["status"] == "UNKNOWN"
    assert "unknown route key" in packet["unknown"]["reason"]


def test_canonical_conflict_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    ghost = {
        "key": "ghost-route",
        "aliases": [],
        "domain": "GHOST",
        "owner_module": "99_ghost/ghost",
        "language": "PYTHON",
        "primary_files": [],
        "secondary_files": [],
        "depends_on": [],
        "depended_on_by": [],
        "tests": {"files": []},
        "validation": [],
        "forbidden": [],
        "symbols": {},
        "contract": "",
        "change_surface": {},
        "owner_paths": ["99_ghost/ghost"],
    }
    monkeypatch.setattr(patch_surface, "load_routes", lambda: [ghost])
    packet = _packet("", route="ghost-route")
    assert packet["status"] == "UNKNOWN"


def test_deterministic_json_output() -> None:
    first = json.dumps(_packet("add validation to broker registry"), sort_keys=True)
    second = json.dumps(_packet("add validation to broker registry"), sort_keys=True)
    assert first == second


def test_ambiguity_agrees_with_router() -> None:
    routes = load_routes()
    for task in ["add strategy parameter", "market screen", "ai boundary", "risk engine"]:
        winner, _how = match_route(task, routes)
        assert winner is not None
        rivals = detect_ambiguity(task, routes, winner)
        assert rivals == [] or winner in rivals
    winner, _how = match_route("Add validation to StrategyRegistry registration", routes)
    rivals = detect_ambiguity("Add validation to StrategyRegistry registration", routes, winner)
    assert winner in rivals and len(rivals) == 2


def test_explicit_beats_task() -> None:
    packet = _packet("zzz no such task anywhere", file="09_broker/broker/registry.py")
    assert packet["status"] == "RESOLVED"
    assert packet["resolution"]["kind"] == "file"


def test_domain_filter_mismatch() -> None:
    packet = _packet("add strategy parameter", domain="BROKER")
    assert packet["status"] == "UNKNOWN"
    assert "!=" in packet["unknown"]["reason"] or "domain" in packet["unknown"]["reason"]


def test_validation_commands_exist() -> None:
    packet = _packet("add validation to broker registry")
    commands = packet["validation"]["commands"] + packet["validation"]["tests"]
    assert commands
    runners = ("pyright", "python", "cargo", "pytest", "ruff", "make")
    for command in commands:
        assert command.split()[0] in runners, command
        for token in command.split()[1:]:
            if token.startswith("scripts/") and token.endswith(".py"):
                assert (ROOT / token).is_file(), token
            if token.startswith("pytest "):
                target = token.split()[1]
                if "/" in target and not target.startswith("-"):
                    assert (ROOT / target).exists(), target


def test_scope_ladder_units() -> None:
    internal_targets = [{"file": "a.py", "symbols": [{"public": False}]}]
    internal_impact = {
        "target_files": ["a.py"],
        "target_modules": ["m"],
        "caller_modules": [],
        "affected_modules": ["m"],
    }
    assert validation_scope(internal_targets, internal_impact, None) == "internal"
    contract_targets = [{"file": "a.py", "symbols": [{"public": True}]}]
    assert (
        validation_scope(contract_targets, internal_impact, {"contract": "c", "language": "PYTHON"})
        == "contract"
    )
    assert (
        validation_scope(
            contract_targets, internal_impact, {"contract": "c", "language": "PYTHON+RUST"}
        )
        == "ownership"
    )


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["route"] for g in GOLDENS])
def test_golden_task(golden: dict) -> None:
    packet = _packet(golden["task"])
    assert packet["status"] == "RESOLVED"
    assert packet["route"]["key"] == golden["route"]
    assert golden["target"] in [t["file"] for t in packet["targets"]]
    quals = [s["qualified"] for t in packet["targets"] for s in t["symbols"]]
    assert golden["symbol"] in quals
    validation_text = " ".join(packet["validation"]["commands"] + packet["validation"]["tests"])
    route_note = packet["tests"]["route"].get("note") or ""
    assert golden["tests"] in validation_text or golden["tests"] in route_note
    assert packet["validation"]["scope"] == golden["scope"]
    assert golden["forbidden"] in packet["boundaries"]["must_not_change"]["patterns"]
