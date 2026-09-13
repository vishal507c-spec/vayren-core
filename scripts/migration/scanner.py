"""Automatic migration discovery: what Python behavior is Rust-owned?

The scanner never trusts manually maintained JSON on its own. It parses the
real repository (Python AST, Rust sources, FFI bridges, production imports)
and produces a machine-readable inventory of behavioral units, duplicates,
orphans, stale records and still-authoritative Python code.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import OWNERSHIP_POLICY_PATH, RETENTION_PATH, ROOT, RUST_CORE_SRC
from .registry import seed_units

SKIP_DIRS = {".venv", "__pycache__", "99_archive", ".git", ".ruff_cache", ".pytest_cache"}

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

BRIDGE_MARKERS = ("native_", "core.native", "core/native", "load_vayren_core")

# Rust module -> FFI symbols it provides (via lib.rs vy_* wrappers). A module
# counts as used when any of its symbols is referenced by production code.
RUST_MODULE_SYMBOLS: dict[str, tuple[str, ...]] = {
    "order_state": ("vy_order_transition_allowed", "vy_order_is_terminal", "vy_order_transitions"),
    "metrics": ("vy_max_drawdown", "vy_equity_curve", "vy_sharpe"),
    "aggregate": ("vy_aggregate",),
    "stats": ("vy_mode",),
}


@dataclass
class ScannedFunction:
    name: str
    file: str
    lineno: int
    is_test: bool = False


@dataclass
class ScanInventory:
    python_files: list[str] = field(default_factory=list)
    rust_owned_python: list[str] = field(default_factory=list)
    rust_files: list[str] = field(default_factory=list)
    rust_symbols: list[str] = field(default_factory=list)
    bridges: list[str] = field(default_factory=list)
    production_import_edges: list[list[str]] = field(default_factory=list)
    retention_missing: list[str] = field(default_factory=list)
    retention_orphaned: list[str] = field(default_factory=list)
    migration_claimed: list[str] = field(default_factory=list)
    rust_unused: list[str] = field(default_factory=list)
    python_authoritative: list[str] = field(default_factory=list)
    unresolved_dependencies: list[str] = field(default_factory=list)


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return out


def _load_policy_rules() -> list[dict]:
    try:
        policy = json.loads(OWNERSHIP_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rules = policy.get("rules", [])
    return rules if isinstance(rules, list) else []


def _classify(rel: str, rules: list[dict]) -> str:
    for rule in rules:
        for prefix in rule.get("directory_prefixes", []):
            if rel.startswith(prefix):
                excluded = rule.get("excluded_subpaths", [])
                if any(rel.startswith(exc) for exc in excluded):
                    return "EXCLUDED"
                return str(rule.get("domain", "UNKNOWN"))
    if rel.startswith("scripts/"):
        return "TOOLING"
    if "/tests/" in rel or rel.endswith("conftest.py"):
        return "TEST"
    return "UNCLASSIFIED"


def _is_test_path(rel: str) -> bool:
    return "/tests/" in rel or rel.endswith("conftest.py") or "test_" in Path(rel).name


def _parse_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _file_uses_bridge(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in text for marker in BRIDGE_MARKERS)


def _production_text(py_files: list[Path]) -> str:
    """Concatenated production file contents for symbol-use detection."""
    chunks: list[str] = []
    for path in py_files:
        rel = path.relative_to(ROOT).as_posix()
        if _is_test_path(rel) or rel.startswith("scripts/migration/"):
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


def _rust_symbols() -> tuple[list[str], list[str]]:
    files: list[str] = []
    symbols: list[str] = []
    if not RUST_CORE_SRC.is_dir():
        return files, symbols
    for path in sorted(RUST_CORE_SRC.glob("*.rs")):
        files.append(path.relative_to(ROOT).as_posix())
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            is_function = stripped.startswith("pub fn ")
            is_ffi_entry = "vy_" in stripped and "extern" in stripped
            is_type = stripped.startswith(("pub struct ", "pub const "))
            if is_function or is_ffi_entry or is_type:
                symbols.append(f"{path.name}:{lineno}:{stripped[:80]}")
    return files, symbols


def _production_files(py_files: list[Path]) -> list[Path]:
    return [p for p in py_files if not _is_test_path(p.relative_to(ROOT).as_posix())]


def scan() -> ScanInventory:
    """Run full discovery and return the inventory (no filesystem writes)."""
    rules = _load_policy_rules()
    py_files = _iter_py_files()
    inv = ScanInventory()
    inv.python_files = [p.relative_to(ROOT).as_posix() for p in py_files]

    for rel in inv.python_files:
        if _classify(rel, rules) in RUST_OWNED_DOMAINS:
            inv.rust_owned_python.append(rel)

    rust_files, rust_symbols = _rust_symbols()
    inv.rust_files = rust_files
    inv.rust_symbols = rust_symbols

    try:
        retention = json.loads(RETENTION_PATH.read_text(encoding="utf-8"))
        retained = retention.get("files", {})
        migrated_claims = retention.get("migrated", {})
    except (OSError, ValueError):
        retained = {}
        migrated_claims = {}
    if not isinstance(retained, dict):
        retained = {}
    if not isinstance(migrated_claims, dict):
        migrated_claims = {}
    inv.migration_claimed = sorted(migrated_claims)

    for rel in inv.rust_owned_python:
        if rel not in retained and rel not in migrated_claims:
            inv.retention_missing.append(rel)

    existing = set(inv.python_files)
    for recorded in retained:
        if recorded not in existing and retained.get(recorded, {}).get("state") != "MIGRATED":
            inv.retention_orphaned.append(recorded)

    for path in _production_files(py_files):
        rel = path.relative_to(ROOT).as_posix()
        for module in _parse_imports(path):
            inv.production_import_edges.append([rel, module])
        if _file_uses_bridge(path):
            inv.bridges.append(rel)

    bridge_text = " ".join(inv.bridges)
    production_text = _production_text(py_files)
    for symbol_file in inv.rust_files:
        stem = Path(symbol_file).stem
        if stem in ("lib", "mod"):
            continue
        symbols = RUST_MODULE_SYMBOLS.get(stem, (stem,))
        if stem not in bridge_text and not any(sym in production_text for sym in symbols):
            inv.rust_unused.append(symbol_file)

    # A seed unit is Python-authoritative while its production path does not
    # route through the Rust bridge (same proof the validator requires).
    from .validator import production_uses_rust

    for unit in seed_units():
        if unit.python_glue:
            continue
        uses_rust, _ = production_uses_rust(unit.unit_id)
        if not uses_rust:
            inv.python_authoritative.append(unit.unit_id)
    inv.python_authoritative = sorted(set(inv.python_authoritative))

    known_units = {u.unit_id for u in seed_units()}
    for unit in seed_units():
        for dep in unit.dependencies:
            if dep not in known_units:
                inv.unresolved_dependencies.append(f"{unit.unit_id} -> {dep}")
    return inv


def inventory_dict(inv: ScanInventory) -> dict:
    return {
        "python_files": len(inv.python_files),
        "rust_owned_python": len(inv.rust_owned_python),
        "rust_files": inv.rust_files,
        "rust_symbols": len(inv.rust_symbols),
        "bridges": inv.bridges,
        "retention_missing": inv.retention_missing,
        "retention_orphaned": inv.retention_orphaned,
        "migration_claimed": inv.migration_claimed,
        "rust_unused": inv.rust_unused,
        "python_authoritative": inv.python_authoritative,
        "unresolved_dependencies": inv.unresolved_dependencies,
        "production_import_edges": len(inv.production_import_edges),
    }


__all__ = ["ScanInventory", "ScannedFunction", "scan", "inventory_dict"]
