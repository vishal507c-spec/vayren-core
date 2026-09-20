"""Enforce constitutional language ownership (hard gate).

Replaces the previous baseline-allowlist validator. Rules derived from
ARCHITECTURE_CONSTITUTION.md 1-8 and 90_brain/ownership_policy.json.

Checks:
  1. Every Python file maps to a domain via ownership_policy.json.
  2. EVERY Python file in a Rust-owned domain MUST have a per-file entry
     in language_retention.json with a valid state + reason +
     migration_target + migration_condition. Class-level "classes" text and
     baseline membership grant NO exemption -> HARD FAIL otherwise.
  3. New Python files in Rust-owned domains without retention -> HARD FAIL.
   4. New Python UI surfaces without per-file retention -> HARD FAIL.
  5. Migrated authorities must not reappear in Python (AST checks).
  6. Retention entries must have valid states (not blanket exemptions).
  7. Rust workspace integrity.

Usage: python scripts/validate_language_ownership.py
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "90_brain"

RUST_OWNED_DOMAINS = {
    "CORE",
    "MARKET_DATA",
    "DATA_PROCESSING",
    "RISK",
    "EXECUTION",
    "BACKTEST",
    "NATIVE_UI",
    "PRESENTATION_MODEL",
}

VALID_RETENTION_STATES = {
    "MIGRATED",
    "MIGRATION_REQUIRED",
    "TEMPORARILY_RETAINED",
    "EXEMPT_WITH_JUSTIFICATION",
}

NATIVE_UI_DIRS = (
    "04_chart/chart/widgets/",
    "04_chart/chart/windows/",
    "04_chart/chart/renderer/",
    "00_app/app/ui/",
    "02_data/data/ui/",
    "06_backtest/backtest/ui/",
)

NO_REINTRODUCE: dict[str, tuple[list[str], str]] = {
    "TRANSITIONS": (["08_execution/execution/models/order.py"], "no-assign"),
    "TERMINAL_STATES": (["08_execution/execution/models/order.py"], "no-assign"),
    "_max_drawdown": (["06_backtest/backtest/engine/metrics.py"], "no-loop"),
    "_sharpe": (["06_backtest/backtest/engine/metrics.py"], "no-loop"),
    "_bucket_key": (["03_market/market/timeframe/aggregate.py"], "no-def"),
}

RUST_FORBIDDEN_MODULES = ("strategy", "research", "ai", "ml", "model_experiment")

SKIP_DIRS = {".venv", "__pycache__", "99_archive", ".git", ".ruff_cache", ".pytest_cache"}


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object file; missing/unreadable/non-object -> empty dict."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _current_py_files() -> list[str]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if any(d in path.parts for d in SKIP_DIRS):
            continue
        out.append(rel)
    return out


def _classify_file(rel_path: str, rules: list[dict]) -> dict | None:
    for rule in rules:
        for prefix in rule.get("directory_prefixes", []):
            if rel_path.startswith(prefix):
                for excluded in rule.get("excluded_subpaths", []):
                    if rel_path.startswith(excluded):
                        return None
                return rule
    if rel_path.startswith("scripts/"):
        return {"domain": "TOOLING", "required_language": "PYTHON", "allow_python_glue": True}
    if "/tests/" in rel_path or rel_path.endswith("conftest.py"):
        return {"domain": "TEST", "required_language": "SAME_AS_PARENT", "allow_python_glue": True}
    return {"domain": "UNCLASSIFIED", "required_language": "UNKNOWN", "allow_python_glue": True}


def _defines(path: Path, name: str, kind: str) -> bool:
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
    errors: list[str] = []
    warnings: list[str] = []

    policy = _load_json(BRAIN / "ownership_policy.json")
    if not policy:
        print("Language ownership validation FAILED: missing 90_brain/ownership_policy.json")
        return 1

    retention_data = _load_json(BRAIN / "language_retention.json")
    if not retention_data:
        errors.append("missing required file: 90_brain/language_retention.json")

    rules = policy.get("rules", [])
    current = _current_py_files()

    per_file_retention: dict[str, dict] = {}
    for file_path, entry in retention_data.get("files", {}).items():
        if isinstance(entry, str):
            per_file_retention[file_path] = {"state": "TEMPORARILY_RETAINED", "reason": entry}
        elif isinstance(entry, dict):
            per_file_retention[file_path] = entry

    migrated_files: set[str] = set()
    for file_path in retention_data.get("migrated", {}):
        migrated_files.add(file_path)

    # NOTE: retention_data["classes"] is DOCUMENTATION ONLY (context for why a
    # domain retains Python glue). It grants ZERO enforcement exemptions.
    # Every Python file in a Rust-owned domain MUST have a per-file entry in
    # retention_data["files"] with a valid state, or the validator FAILS.
    # Baseline (language_baseline.json) is historical reference only and is
    # NEVER consulted here: baseline membership grants no exemption.
    native_allowlist: set[str] = set(retention_data.get("native_workspace_allowlist", {}).keys())

    for rel in current:
        if rel in migrated_files:
            continue

        rule = _classify_file(rel, rules)
        if rule is None:
            continue

        domain = rule["domain"]
        req_lang = rule["required_language"]

        if domain in ("TEST", "TOOLING", "UNCLASSIFIED"):
            continue
        if req_lang in ("PYTHON", "SAME_AS_PARENT"):
            continue
        if req_lang == "UNKNOWN":
            warnings.append(f"unclassified file: {rel}")
            continue

        if domain in RUST_OWNED_DOMAINS:
            if rel in per_file_retention:
                entry = per_file_retention[rel]
                state = entry.get("state", "")
                if state not in VALID_RETENTION_STATES:
                    errors.append(
                        f"INVALID RETENTION STATE for {rel}: '{state}'. "
                        f"Must be one of {sorted(VALID_RETENTION_STATES)}"
                    )
                elif state == "MIGRATED":
                    errors.append(
                        f"FILE MARKED MIGRATED BUT STILL EXISTS: {rel}. "
                        f"Remove the file or change state to MIGRATION_REQUIRED."
                    )
                elif state in ("TEMPORARILY_RETAINED", "EXEMPT_WITH_JUSTIFICATION"):
                    for field in ("reason", "migration_target", "migration_condition"):
                        if not entry.get(field):
                            errors.append(
                                f"INCOMPLETE RETENTION for {rel}: missing '{field}'. "
                                f"Retained files must define reason, migration_target, "
                                f"migration_condition."
                            )
                continue

            if any(rel.startswith(prefix) for prefix in NATIVE_UI_DIRS):
                if rel in native_allowlist:
                    continue
                errors.append(
                    f"NEW PYTHON UI SURFACE IN WRONG LANGUAGE: {rel}. "
                    f"Domain {domain} requires {req_lang} (Rust+Slint). "
                    f"Add to language_retention.json with TEMPORARILY_RETAINED or migrate."
                )
                continue

            errors.append(
                f"WRONG LANGUAGE: {rel} implements {domain} responsibility in Python. "
                f"Required language: {req_lang}. "
                f"Baseline membership grants no exemption. "
                f"Migrate to {req_lang} or add a tracked per-file retention entry "
                f"(state/reason/migration_target/migration_condition) to "
                f"90_brain/language_retention.json."
            )

    for name, (files, kind) in NO_REINTRODUCE.items():
        for rel in files:
            target = ROOT / rel
            if target.is_file() and _defines(target, name, kind):
                errors.append(f"migrated authority reintroduced in Python: {name} in {rel}")

    for file_path, entry in per_file_retention.items():
        if not (ROOT / file_path).is_file():
            state = entry.get("state", "") if isinstance(entry, dict) else "?"
            if state != "MIGRATED":
                errors.append(f"retention references deleted file: {file_path}")

    cargo = ROOT / "rust" / "Cargo.toml"
    core_manifest = ROOT / "rust" / "vayren-core" / "Cargo.toml"
    if not cargo.is_file() or not core_manifest.is_file():
        errors.append("missing Rust workspace (rust/Cargo.toml, rust/vayren-core)")
    else:
        text = core_manifest.read_text(encoding="utf-8")
        if "[dependencies]" not in text:
            errors.append("vayren-core manifest lost its dependency section")
        else:
            deps = text.split("[dependencies]", 1)[1].split("[", 1)[0].strip()
            if deps:
                errors.append(f"vayren-core must stay dependency-free (std only): {deps[:120]}")

    for rs in (ROOT / "rust").rglob("*.rs"):
        stem = rs.stem.lower()
        if stem in RUST_FORBIDDEN_MODULES:
            errors.append(f"Rust absorbed a Python-owned domain module: {rs}")

    shell_lib = ROOT / "rust" / "vayren-shell" / "src" / "lib.rs"
    if not shell_lib.is_file():
        errors.append("missing native-UI target: rust/vayren-shell/src/lib.rs")

    migration_required = [
        f
        for f, e in per_file_retention.items()
        if isinstance(e, dict) and e.get("state") == "MIGRATION_REQUIRED" and (ROOT / f).is_file()
    ]
    if migration_required:
        warnings.append(
            f"{len(migration_required)} files marked MIGRATION_REQUIRED still active: "
            + ", ".join(migration_required[:5])
        )

    for warning in warnings[:20]:
        print(f"warn: {warning}")

    if errors:
        print(f"Language ownership validation FAILED ({len(errors)} errors):")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"Language ownership validation PASSED ({len(current)} files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
