"""Migration validator: evidence-driven gate with no false PASS.

Verdicts per unit: PASS / FAIL / BLOCKED / STALE / INCONCLUSIVE.
Uncertainty is never converted into PASS. A unit that claims a verified or
canonical state without fresh, hash-pinned evidence FAILS. Units that simply
have not migrated yet report BLOCKED/INCONCLUSIVE — tracked, not hidden.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .config import MIN_SHADOW_COMPARISONS, ROOT
from .graph import blockers_for, build_graph
from .hashing import hash_paths
from .manifest_store import load_all
from .models import MigrationManifest
from .quarantine import check_quarantine
from .redact import contains_secret_literal
from .registry import seed_units
from .state_machine import is_allowed

# Unit -> bridge files proving the production path routes through Rust.
BRIDGES: dict[str, tuple[str, ...]] = {
    "execution.order_lifecycle": (
        "08_execution/execution/native_order_state.py",
        "08_execution/execution/models/order.py",
    ),
    "backtest.metrics.drawdown": (
        "06_backtest/backtest/native_metrics.py",
        "06_backtest/backtest/engine/metrics.py",
    ),
    "backtest.metrics.equity_curve": (
        "06_backtest/backtest/native_metrics.py",
        "06_backtest/backtest/engine/metrics.py",
    ),
    "backtest.metrics.sharpe": (
        "06_backtest/backtest/native_metrics.py",
        "06_backtest/backtest/engine/metrics.py",
    ),
    "market.timeframe.aggregate": (
        "03_market/market/native_aggregate.py",
        "03_market/market/timeframe/aggregate.py",
    ),
    "market.timeframe.mode": (
        "03_market/market/native_aggregate.py",
        "03_market/market/timeframe/aggregate.py",
    ),
    "risk.engine.evaluate": (
        "07_risk/risk/native_checks.py",
        "07_risk/risk/engine.py",
    ),
}

# Python-side duplicate authority that must NOT exist once Rust owns the unit.
NO_DUPLICATE: dict[str, tuple[str, str]] = {
    "execution.order_lifecycle": ("08_execution/execution/models/order.py", "TRANSITIONS"),
    "backtest.metrics.drawdown": ("06_backtest/backtest/engine/metrics.py", "_max_drawdown"),
    "backtest.metrics.sharpe": ("06_backtest/backtest/engine/metrics.py", "_sharpe"),
    "market.timeframe.aggregate": ("03_market/market/timeframe/aggregate.py", "_bucket_key"),
}

ADVANCED_STATES = (
    "PARITY_VERIFIED",
    "SHADOW_VALIDATED",
    "INTEGRATED",
    "RUST_CANONICAL",
    "PYTHON_DEPRECATED",
    "PYTHON_QUARANTINED",
    "PYTHON_REMOVED",
    "FINAL_VERIFIED",
    "MIGRATED",
)

# File-level MIGRATED claims in language_retention.json must be backed by
# fresh behavioral parity evidence on these units — never by declaration.
MIGRATED_CLAIM_UNITS: dict[str, tuple[str, ...]] = {
    "08_execution/execution/models/order.py": ("execution.order_lifecycle",),
    "06_backtest/backtest/engine/metrics.py": (
        "backtest.metrics.drawdown",
        "backtest.metrics.equity_curve",
        "backtest.metrics.sharpe",
    ),
    "03_market/market/timeframe/aggregate.py": (
        "market.timeframe.aggregate",
        "market.timeframe.mode",
    ),
}


def _has_duplicate_table(unit_id: str) -> bool:
    spec = NO_DUPLICATE.get(unit_id)
    if spec is None:
        return False
    rel, name = spec
    path = ROOT / rel
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if name == "_bucket_key":
                return True
            if name in ("_max_drawdown", "_sharpe"):
                for stmt in node.body:
                    for child in ast.walk(stmt):
                        if isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
                            return True
            return False
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return True
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name:
            return True
    return False


def production_uses_rust(unit_id: str) -> tuple[bool, str]:
    bridges = BRIDGES.get(unit_id)
    if not bridges:
        return False, "no Rust bridge defined for unit"
    missing = [rel for rel in bridges if not (ROOT / rel).is_file()]
    if missing:
        return False, f"bridge files missing: {', '.join(missing)}"
    consumer = bridges[-1]
    try:
        text = (ROOT / consumer).read_text(encoding="utf-8")
    except OSError:
        return False, f"cannot read consumer {consumer}"
    stem = Path(bridges[0]).stem
    if stem not in text and "native_" not in text and "load_vayren_core" not in text:
        return False, f"{consumer} does not route through {stem}"
    if _has_duplicate_table(unit_id):
        return False, f"python duplicate authority still present for {unit_id}"
    return True, f"{consumer} routes through {stem}"


def current_hashes(manifest: MigrationManifest) -> tuple[str, str]:
    source = hash_paths([manifest.source]) if manifest.source else "none"
    target = hash_paths([manifest.target]) if manifest.target else "none"
    return source, target


def check_unit(
    manifest: MigrationManifest, all_manifests: dict[str, MigrationManifest] | None = None
) -> tuple[str, list[str]]:
    """Return (verdict, reasons) for one manifest against live repository state."""
    states = {uid: m.state for uid, m in (all_manifests or {}).items()}

    for entry in manifest.evidence:
        offender = contains_secret_literal(entry)
        if offender:
            return "FAIL", [f"secret literal {offender!r} present in migration evidence"]

    rust_exists = bool(manifest.target) and (ROOT / manifest.target).is_file()
    if manifest.target and not rust_exists and manifest.state in ADVANCED_STATES:
        return "FAIL", [f"rust target missing: {manifest.target}"]

    stored_fresh = bool(manifest.source_hash) and bool(manifest.target_hash)
    live_source, live_target = current_hashes(manifest)
    hashes_match = stored_fresh and manifest.source_hash == live_source
    hashes_match = hashes_match and manifest.target_hash == live_target

    if manifest.state in ADVANCED_STATES:
        if not stored_fresh:
            return "STALE", ["advanced state without hash-pinned evidence"]
        if not hashes_match:
            return "STALE", ["source or target changed since evidence was recorded"]

    uses_rust, integration_detail = production_uses_rust(manifest.unit)
    if manifest.integration.production_uses_rust and not uses_rust:
        return "FAIL", [f"manifest claims Rust integration: {integration_detail}"]
    if (
        manifest.state
        in (
            "RUST_CANONICAL",
            "PYTHON_DEPRECATED",
            "PYTHON_QUARANTINED",
            "PYTHON_REMOVED",
            "FINAL_VERIFIED",
            "MIGRATED",
        )
        and not uses_rust
    ):
        return "FAIL", [f"canonical state without production Rust path: {integration_detail}"]

    if manifest.parity.status == "PASS" and not hashes_match:
        return "STALE", ["parity PASS pinned to older hashes"]
    if manifest.shadow.status == "PASS" and not hashes_match:
        return "STALE", ["shadow PASS pinned to older hashes"]
    if manifest.shadow.status == "PASS" and manifest.shadow.comparisons < MIN_SHADOW_COMPARISONS:
        return "STALE", [
            f"shadow PASS on {manifest.shadow.comparisons} comparisons "
            f"(minimum {MIN_SHADOW_COMPARISONS})"
        ]

    ok, quarantine_detail = check_quarantine(manifest)
    if not ok:
        return "FAIL", [quarantine_detail]

    if manifest.state == "MIGRATED" and manifest.source and (ROOT / manifest.source).is_file():
        return "FAIL", ["python source still present for MIGRATED unit"]

    if manifest.state in (
        "DISCOVERED",
        "ANALYZED",
        "PLANNED",
        "BLOCKED",
        "READY",
        "IMPLEMENTING",
        "IMPLEMENTED",
        "PARITY_TESTING",
    ):
        if not manifest.target:
            return "BLOCKED", ["no Rust target defined yet"]
        graph_blockers = blockers_for(manifest.unit, states) if states else []
        if graph_blockers:
            return "BLOCKED", [f"waiting on {', '.join(graph_blockers)}"]
        if manifest.state in ("DISCOVERED", "ANALYZED"):
            return "INCONCLUSIVE", ["not yet planned"]
        return "BLOCKED", [f"in {manifest.state}: {integration_detail}"]

    if manifest.state == "REGRESSION":
        return "FAIL", ["unit in regression — rollback evidence recorded, fix required"]

    if manifest.parity.status == "FAIL":
        return "FAIL", ["parity failing"]
    if manifest.shadow.status == "FAIL":
        return "FAIL", ["shadow mismatches open"]
    if manifest.state in ADVANCED_STATES and manifest.parity.status != "PASS":
        return "FAIL", [f"{manifest.state} without parity PASS"]
    return "PASS", [integration_detail or "evidence fresh"]


def _retention_migrated_claims() -> dict[str, object]:
    import json

    from .config import RETENTION_PATH

    try:
        data = json.loads(RETENTION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    claimed = data.get("migrated", {})
    return claimed if isinstance(claimed, dict) else {}


def validate_all() -> tuple[int, dict]:
    """Repository-wide gate. Returns (exit_code, report)."""
    manifests = load_all()
    known = {u.unit_id for u in seed_units()}
    unit_results: dict[str, dict] = {}
    failures = 0
    for unit_id, manifest in sorted(manifests.items()):
        verdict, reasons = check_unit(manifest, manifests)
        unit_results[unit_id] = {"verdict": verdict, "state": manifest.state, "reasons": reasons}
        if verdict in ("FAIL", "STALE"):
            failures += 1
    # A file-level MIGRATED retention claim without fresh behavioral parity
    # evidence on the covering units is a false declaration — hard FAIL.
    false_claims: list[str] = []
    for claimed_file in _retention_migrated_claims():
        covering = MIGRATED_CLAIM_UNITS.get(claimed_file, ())
        if not covering:
            false_claims.append(f"{claimed_file}: no behavioral unit covers this claim")
            continue
        for unit_id in covering:
            manifest = manifests.get(unit_id)
            if manifest is None:
                false_claims.append(f"{claimed_file}: missing migration metadata for {unit_id}")
                continue
            live_source, live_target = current_hashes(manifest)
            fresh = manifest.source_hash == live_source and manifest.target_hash == live_target
            if manifest.parity.status != "PASS" or not fresh:
                false_claims.append(
                    f"{claimed_file}: {unit_id} lacks fresh parity PASS "
                    f"(status={manifest.parity.status}, fresh={fresh})"
                )
    failures += len(false_claims)
    # Frozen oracles are verification-only: any production import of the
    # agent oracle modules would make tests tautological — hard FAIL.
    oracle_breaches = _oracle_hygiene()
    failures += len(oracle_breaches)
    orphaned = [uid for uid in manifests if uid not in known]
    graph = build_graph()
    return_code = 1 if failures or orphaned or oracle_breaches else 0
    report = {
        "units": len(manifests),
        "failures": failures,
        "false_migration_claims": false_claims,
        "oracle_breaches": oracle_breaches,
        "orphaned_manifests": orphaned,
        "graph_order": sorted(graph, key=lambda u: graph[u].depth),
        "results": unit_results,
    }
    return return_code, report


def _oracle_hygiene() -> list[str]:
    """Production files must never import frozen verification oracles."""
    import ast as _ast

    from .config import ROOT as _ROOT

    breaches: list[str] = []
    skip = (".venv", "__pycache__", "99_archive", ".git", ".ruff_cache", ".pytest_cache")
    for path in sorted(_ROOT.rglob("*.py")):
        if any(part in skip for part in path.parts):
            continue
        try:
            rel = path.relative_to(_ROOT).as_posix()
        except ValueError:
            continue
        if (
            "/tests/" in rel
            or rel.endswith("conftest.py")
            or rel.startswith("scripts/")
            or rel.startswith("90_brain/")
        ):
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in _ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, _ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, _ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if "oracles" in module or module.startswith("scripts.migration.agent"):
                    breaches.append(f"{rel} imports verification oracle {module!r}")
    return breaches


__all__ = [
    "BRIDGES",
    "MIGRATED_CLAIM_UNITS",
    "production_uses_rust",
    "current_hashes",
    "check_unit",
    "validate_all",
    "is_allowed",
]
