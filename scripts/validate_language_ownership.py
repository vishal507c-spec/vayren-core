"""Validate constitutional language ownership (final migration pass).

Rules (ARCHITECTURE_CONSTITUTION.md §8, enforced permanently):
 1. Every product Python file must be classified in the committed inventory
    (no unexplained files): regenerate with `scripts/language_audit.py`.
 2. No NEW Python in Rust-owned domains (CORE/MARKET_DATA/DATA_PROCESSING/
    RISK/EXECUTION/BACKTEST/PRESENTATION_MODEL) beyond the frozen baseline:
    new numeric/lifecycle/core code belongs in `rust/` (or in the retention
    manifest with proof).
 3. No NEW Qt UI surfaces (chart widgets/windows/renderer, app/ui, data/ui,
    backtest/ui): new native UI belongs in `rust/vayren-shell` (Rust+egui).
 4. Migrated authorities must not be reintroduced in Python (AST checks).
 5. The Rust workspace must exist, stay dependency-clean, and keep
    strategy/AI/research out of Rust.
 6. The retention manifest must reference files that exist.

Usage: `python scripts/validate_language_ownership.py [--freeze-baseline]`
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "90_brain"

RUST_OWNED = {
    "CORE",
    "MARKET_DATA",
    "DATA_PROCESSING",
    "RISK",
    "EXECUTION",
    "BACKTEST",
    "PRESENTATION_MODEL",
}

QT_SURFACE_DIRS = (
    "04_chart/chart/widgets/",
    "04_chart/chart/windows/",
    "04_chart/chart/renderer/",
    "00_app/app/ui/",
    "02_data/data/ui/",
    "06_backtest/backtest/ui/",
)

# Migrated authorities that must never reappear in Python.
# Rule kinds: "no-assign" (name must not be assigned as data),
# "no-loop" (a same-named delegate may exist but hold no loops),
# "no-def" (the helper was deleted; any definition is reintroduction).
NO_REINTRODUCE: dict[str, tuple[list[str], str]] = {
    "TRANSITIONS": (["08_execution/execution/models/order.py"], "no-assign"),
    "TERMINAL_STATES": (["08_execution/execution/models/order.py"], "no-assign"),
    "_max_drawdown": (["06_backtest/backtest/engine/metrics.py"], "no-loop"),
    "_sharpe": (["06_backtest/backtest/engine/metrics.py"], "no-loop"),
    "_bucket_key": (["03_market/market/timeframe/aggregate.py"], "no-def"),
}

RUST_FORBIDDEN_MODULES = ("strategy", "research", "ai", "ml", "model_experiment")


def _current_py_files() -> list[str]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        parts = path.parts
        if ".venv" in parts or "__pycache__" in parts or "99_archive" in parts:
            continue
        out.append(rel)
    return out


def _defines(path: Path, name: str, kind: str) -> bool:
    """True when a migrated authority was reintroduced in `path`.

    - "no-assign": the name must not be assigned as module data (imports
      and re-exports are fine).
    - "no-loop": a same-named thin delegate may exist, but it must hold
      no loops (marshaling only, no math).
    - "no-def": the helper was deleted; any definition fails.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if kind == "no-def":
                return True
            return kind == "no-loop" and any(
                isinstance(child, (ast.For, ast.AsyncFor, ast.While))
                for stmt in node.body
                for child in ast.walk(stmt)
            )
        if (
            kind == "no-assign"
            and isinstance(node, ast.Assign)
            and any(getattr(t, "id", "") == name for t in node.targets)
        ):
            return True
        if (
            kind == "no-assign"
            and isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == name
        ):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate language ownership")
    parser.add_argument("--freeze-baseline", action="store_true")
    args = parser.parse_args()
    errors: list[str] = []
    warnings: list[str] = []

    inventory_path = BRAIN / "language_inventory.json"
    baseline_path = BRAIN / "language_baseline.json"
    retention_path = BRAIN / "language_retention.json"
    for required in (inventory_path, retention_path):
        if not required.is_file():
            errors.append(f"missing required manifest: {required.name}")

    inventory: dict[str, dict] = {}
    if inventory_path.is_file():
        try:
            entries = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory = {e["file"]: e for e in entries}
        except (ValueError, KeyError) as exc:
            errors.append(f"unreadable inventory: {exc}")

    current = _current_py_files()
    missing = [f for f in current if f not in inventory]
    if missing:
        errors.append(
            f"{len(missing)} unclassified Python files (run scripts/language_audit.py): "
            + ", ".join(missing[:8])
        )
    stale = [f for f in inventory if not (ROOT / f).is_file()]
    if stale:
        errors.append(f"inventory references deleted files: {stale[:8]}")

    baseline: dict[str, str] = {}
    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if args.freeze_baseline:
        frozen = {f: inventory.get(f, {}).get("responsibility", "?") for f in current}
        baseline_path.write_text(json.dumps(frozen, indent=1, sort_keys=True), encoding="utf-8")
        print(f"baseline frozen: {len(frozen)} files -> {baseline_path.name}")
        return 0
    if not baseline:
        errors.append("missing baseline: run with --freeze-baseline once, then commit it")

    for path in current:
        if path in baseline:
            continue
        entry = inventory.get(path, {})
        responsibility = entry.get("responsibility", "?")
        if responsibility in RUST_OWNED:
            errors.append(
                f"new Python in Rust-owned domain ({responsibility}): {path} — "
                "implement in rust/ or add proven retention to language_retention.json"
            )
        elif any(path.startswith(prefix) for prefix in QT_SURFACE_DIRS):
            errors.append(
                f"new Qt UI surface: {path} — new native UI belongs in "
                "rust/vayren-shell (Rust+egui)"
            )
        else:
            warnings.append(f"new file outside frozen baseline: {path} ({responsibility})")

    for name, (files, kind) in NO_REINTRODUCE.items():
        for rel in files:
            if _defines(ROOT / rel, name, kind):
                errors.append(f"migrated authority reintroduced in Python: {name} in {rel}")

    retention: dict = {}
    if retention_path.is_file():
        retention = json.loads(retention_path.read_text(encoding="utf-8"))
        for section in ("classes", "files", "migrated"):
            for key in retention.get(section, {}):
                if section != "classes" and not (ROOT / key).is_file():
                    errors.append(f"retention manifest references missing file: {key}")

    cargo = ROOT / "rust" / "Cargo.toml"
    core_manifest = ROOT / "rust" / "vayren-core" / "Cargo.toml"
    if not cargo.is_file() or not core_manifest.is_file():
        errors.append("missing Rust workspace (rust/Cargo.toml, rust/vayren-core)")
    else:
        text = core_manifest.read_text(encoding="utf-8")
        if "[dependencies]" not in text:
            errors.append("vayren-core manifest lost its dependency section")
        deps = text.split("[dependencies]", 1)[1].split("[", 1)[0].strip()
        if deps:
            errors.append(f"vayren-core must stay dependency-free (std only): {deps[:120]}")
    for rs in (ROOT / "rust").rglob("*.rs"):
        stem = rs.stem.lower()
        if stem in RUST_FORBIDDEN_MODULES:
            errors.append(f"Rust absorbed a Python-owned domain module: {rs}")
    shell_lib = ROOT / "rust" / "vayren-shell" / "src" / "lib.rs"
    if not shell_lib.is_file():
        errors.append("missing native-UI target: rust/vayren-shell")

    for warning in warnings[:10]:
        print(f"warn: {warning}")
    if errors:
        print("Language ownership validation FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Language ownership validation PASSED ({len(current)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
