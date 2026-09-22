"""Zero-discovery execution surface tests (Phase 5 matrix §18 + goldens §17).

Read-only against the live warm index (no repo writes, no codegen, no patch
application). A structural test proves no search/rediscovery calls exist in
the execution path; golden flows prove 0 rebuild/refresh calls when warm.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import execute_surface  # noqa: E402
import repo_graph  # noqa: E402
import repo_index  # noqa: E402
from execute_surface import (  # noqa: E402
    build_manifest,
    check_edit,
    check_manifest,
    read_entry,
    run_validation,
)

ROOT = SCRIPTS_DIR.parent

GOLDENS = [
    {"task": "add strategy parameter", "route": "strategy-change"},
    {"task": "add validation to broker registry", "route": "broker-ubl"},
    {"task": "add timeframe", "route": "market-read"},
    {"task": "backtest drawdown", "route": "backtest"},
    {"task": "market screen", "route": "ui-screen"},
    {"task": "ai boundary", "route": "core-ai"},
    {"task": "download settings", "route": "download-config"},
]


def _manifest(task: str, **kwargs: object) -> dict:
    manifest, _status = build_manifest(task, **kwargs)  # type: ignore[arg-type]
    return manifest


def test_valid_execution_surface() -> None:
    manifest = _manifest("add validation to broker registry")
    assert manifest["status"] == "READY"
    assert manifest["schema"] == "execution-manifest/v1"
    assert manifest["route"] == "broker-ubl"
    assert manifest["index_fingerprint"]
    assert manifest["manifest_key"]


def test_exact_target_read() -> None:
    manifest = _manifest("add validation to broker registry")
    result = read_entry(manifest, "09_broker/broker/registry.py")
    assert result["status"] == "READY"
    assert result["sha"] == manifest["hashes"]["09_broker/broker/registry.py"]
    quals = [r["qualified"] for r in result["regions"]]
    assert "broker.registry:BrokerRegistry" in quals
    region = next(
        r for r in result["regions"] if r["qualified"] == "broker.registry:BrokerRegistry"
    )
    assert region["line"] == 68
    assert "class BrokerRegistry" in region["text"]


def test_exact_symbol_read() -> None:
    manifest = _manifest("", symbol="BrokerRegistry")
    assert manifest["status"] == "READY"
    assert "09_broker/broker/registry.py" in manifest["read"]
    assert "09_broker/broker/registry.py" in manifest["modify"]["must"]


def test_multiple_target_files() -> None:
    manifest = _manifest("add timeframe")
    assert manifest["status"] == "READY"
    assert "03_market/market/models/bar.py" in manifest["modify"]["must"]
    assert "rust/vayren-core/src/aggregate.rs" in manifest["modify"]["must"]
    assert len(manifest["modify"]["must"]) > 1


def test_must_change_enforcement() -> None:
    manifest = _manifest("add validation to broker registry")
    result = check_edit(manifest, "09_broker/broker/registry.py")
    assert result["verdict"] == "ALLOW"
    assert result["scope"] == "MUST_CHANGE"


def test_may_change_enforcement() -> None:
    manifest = _manifest("add validation to broker registry")
    may = manifest["modify"]["may"]
    assert may
    result = check_edit(manifest, may[0])
    assert result["verdict"] == "ALLOW"
    assert "MAY_CHANGE" in result["scope"]


def test_must_not_change_block() -> None:
    manifest = _manifest("add validation to broker registry")
    result = check_edit(manifest, "05_strategy/strategy/sma.py")
    assert result["verdict"] == "BLOCKED"
    assert "forbidden pattern" in result["reason"]
    assert result["allowed"]


def test_arbitrary_path_rejected() -> None:
    manifest = _manifest("add validation to broker registry")
    for bad in ["../outside.py", "/abs/path.py", "C:\\win.py", "09_broker/broker/evil.py"]:
        result = check_edit(manifest, bad)
        assert result["verdict"] == "BLOCKED", bad
    result = read_entry(manifest, "../outside.py")
    assert result["status"] == "BLOCKED"


def test_forbidden_owner_block() -> None:
    manifest = _manifest("risk engine", modify=["07_risk/risk/new_logic.py"])
    assert manifest["status"] == "BLOCKED"


def test_forbidden_language_block() -> None:
    manifest = _manifest("add strategy parameter", modify=["05_strategy/strategy/new_kernel.rs"])
    assert manifest["status"] == "BLOCKED"
    assert manifest["safety"]["gate"]["blocked"] is True


def test_stale_packet() -> None:
    manifest = _manifest("add validation to broker registry")
    tampered = dict(manifest)
    tampered["hashes"] = dict(manifest["hashes"])
    target = manifest["modify"]["must"][0]
    tampered["hashes"][target] = "0" * 64
    result = check_manifest(tampered)
    assert result["status"] == "STALE"
    assert "concurrent modification" in result["reason"]


def test_changed_target_hash() -> None:
    manifest = _manifest("add validation to broker registry")
    target = manifest["modify"]["must"][0]
    tampered = dict(manifest)
    tampered["hashes"] = dict(manifest["hashes"])
    tampered["hashes"][target] = "f" * 64
    result = check_edit(tampered, target)
    assert result["verdict"] in ("STALE", "BLOCKED")
    assert "STALE" in result.get("reason", "") or result["verdict"] == "STALE"


def test_changed_symbol() -> None:
    manifest = _manifest("add validation to broker registry")
    result = check_edit(manifest, "09_broker/broker/registry.py", symbol="broker.registry:Ghost")
    assert result["verdict"] == "STALE"
    assert "moved/deleted" in result["reason"]


def test_deleted_target() -> None:
    manifest = _manifest("add validation to broker registry")
    tampered = dict(manifest)
    tampered["hashes"] = dict(manifest["hashes"])
    tampered["hashes"]["09_broker/broker/gone.py"] = "0" * 64
    result = check_manifest(tampered)
    assert result["status"] == "STALE"
    assert "deleted" in result["reason"]


def test_missing_test() -> None:
    manifest = _manifest("ai boundary")
    assert manifest["status"] == "READY"
    assert any(t.get("relevance") == "missing" for t in manifest["tests"])


def test_missing_validation_unit() -> None:
    checked = execute_surface._verify_command("frobnicate do --things")
    assert checked["available"] is False
    assert "frobnicate" in checked["reason"]


def test_unknown_task() -> None:
    manifest = _manifest("zzz no such task anywhere")
    assert manifest["status"] == "UNKNOWN"
    assert manifest["read"] == []


def test_ambiguous_task() -> None:
    manifest = _manifest("Add validation to StrategyRegistry registration")
    assert manifest["status"] == "NEEDS_CLARIFICATION"


def test_concurrent_modification() -> None:
    manifest = _manifest("add validation to broker registry")
    target = manifest["modify"]["must"][0]
    tampered = dict(manifest)
    tampered["hashes"] = dict(manifest["hashes"])
    tampered["hashes"][target] = "1" * 64
    result = read_entry(tampered, target)
    assert result["status"] == "STALE"
    assert "concurrent modification" in result["reason"]


def test_deterministic_manifest() -> None:
    first, _ = build_manifest("download settings", use_cache=False)
    second, _ = build_manifest("download settings", use_cache=False)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_cache_hit() -> None:
    _manifest("download settings")
    manifest, status = build_manifest("download settings")
    assert status == "hit"
    assert manifest["cache"]["status"] == "hit"


def test_cache_invalidation() -> None:
    first, _ = build_manifest("download settings", use_cache=False)
    second, status = build_manifest("ai boundary", use_cache=True)
    assert second["route"] == "core-ai"
    assert first["manifest_key"] != second["manifest_key"]
    assert status in ("hit", "miss")


def test_read_only_manifest() -> None:
    manifest = _manifest("add validation to broker registry", read_only=True)
    assert manifest["status"] == "READ_ONLY"
    result = check_edit(manifest, "09_broker/broker/registry.py")
    assert result["verdict"] == "BLOCKED"
    assert "read-only" in result["reason"]


def test_validation_run_fast_command() -> None:
    manifest = _manifest("add validation to broker registry")
    assert manifest["status"] == "READY"
    fast = dict(manifest)
    fast["validate"] = {
        "scope": "internal",
        "commands": [{"command": "python scripts/validate_imports.py", "available": True}],
    }
    result = run_validation(fast)
    assert result["status"] == "EXECUTION_COMPLETE"
    assert result["success"] is True
    assert result["results"][0]["rc"] == 0


def test_no_search_calls_in_execution_path() -> None:
    source = (SCRIPTS_DIR / "execute_surface.py").read_text(encoding="utf-8")
    banned = [
        "match_route",
        "load_routes",
        "rglob",
        ".glob(",
        "build_graph",
        "patch_surface.resolve",
        "os.walk",
    ]
    for token in banned:
        assert token not in source, token
    # Exactly two packet calls (manifest build + benchmark), both cached-packet
    # paths; one subprocess call site (explicit --run validation only).
    assert source.count("context_packet.build_packet") == 2
    assert source.count("subprocess.run") == 1


def test_zero_rediscovery_golden_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"refresh": 0, "build_graph": 0}

    def _no_refresh(*_args: object, **_kwargs: object) -> dict:
        calls["refresh"] += 1
        raise AssertionError("refresh must not run on the warm path")

    def _no_build(*_args: object, **_kwargs: object) -> dict:
        calls["build_graph"] += 1
        raise AssertionError("rebuild must not run on the warm path")

    monkeypatch.setattr(repo_index, "refresh", _no_refresh)
    monkeypatch.setattr(repo_graph, "build_graph", _no_build)
    manifest = _manifest("add validation to broker registry")
    assert manifest["status"] == "READY"
    target = manifest["modify"]["must"][0]
    assert read_entry(manifest, target)["status"] == "READY"
    assert check_edit(manifest, target)["verdict"] == "ALLOW"
    assert calls == {"refresh": 0, "build_graph": 0}


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["route"] for g in GOLDENS])
def test_zero_discovery_golden_benchmark(golden: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"refresh": 0, "build_graph": 0}

    def _no_refresh(*_args: object, **_kwargs: object) -> dict:
        calls["refresh"] += 1
        raise AssertionError("refresh must not run on the warm path")

    def _no_build(*_args: object, **_kwargs: object) -> dict:
        calls["build_graph"] += 1
        raise AssertionError("rebuild must not run on the warm path")

    monkeypatch.setattr(repo_index, "refresh", _no_refresh)
    monkeypatch.setattr(repo_graph, "build_graph", _no_build)
    manifest = _manifest(golden["task"])
    assert manifest["status"] in ("READY", "READ_ONLY"), manifest.get("reason")
    assert manifest["route"] == golden["route"]
    assert manifest["modify"]["must"]
    target = manifest["modify"]["must"][0]
    assert read_entry(manifest, target)["status"] == "READY"
    assert check_edit(manifest, target)["verdict"] == "ALLOW"
    assert manifest["tests"] or manifest.get("reason", "")
    assert manifest["validate"]["commands"]
    assert calls == {"refresh": 0, "build_graph": 0}
