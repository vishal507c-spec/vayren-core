"""Validate the task routing index (Phase 5 §13/§14).

Every route is re-verified against the tree on each run: renamed/moved files,
changed ownership, or edited interfaces fail loudly instead of serving a stale
map. Broken route -> CI FAIL.

Checks per route:
  unique key + globally unique aliases (no duplicate route authority)
  language in the allowed set (single or explicit multi-language + boundary)
  owner module dir exists; primary/secondary files exist
  test dirs exist when listed (empty tests.files requires a note)
  validation commands use known runners; scripts/* commands reference files
  forbidden globs match >= 1 existing path (stale patterns fail)
  symbols resolve in their file (def/class for Python, fn/struct/enum for Rust)
  primary-file extensions consistent with the route language (drift detection)

Usage: python scripts/validate_routes.py [--json]
Exit: 0 PASS, 1 FAIL, 2 ERROR.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES_FILE = ROOT / "90_brain" / "task_routes.json"

ALLOWED_LANGUAGES = {"PYTHON", "RUST", "SLINT", "PYTHON+RUST", "RUST+SLINT"}
LANGUAGE_EXTENSIONS = {
    "PYTHON": {".py", ".json"},
    "RUST": {".rs", ".json"},
    "SLINT": {".slint", ".json"},
    "PYTHON+RUST": {".py", ".rs", ".json"},
    "RUST+SLINT": {".rs", ".slint", ".json"},
}
KNOWN_RUNNERS = {"pytest", "cargo", "ruff", "pyright", "python", "make", "cd"}
PY_SYMBOL = re.compile(r"^(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
RS_SYMBOL = re.compile(r"^(?:pub\s+)?(?:fn|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


def _symbols_in(path: Path) -> set[str]:
    """Names defined at line start (def/class for .py, fn/struct/enum for .rs)."""
    pattern = PY_SYMBOL if path.suffix == ".py" else RS_SYMBOL
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {m.group(1) for line in text.splitlines() if (m := pattern.match(line.strip()))}


def _existing_paths() -> set[str]:
    """Tracked + untracked-but-visible repo files (fast: no build caches).

    Git-aware so rust/target, __pycache__ and other ignored artifacts are
    never scanned; falls back to a skip-listed walk outside git checkouts.
    """
    try:
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        others = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        if tracked:
            return {line.strip() for line in tracked + others if line.strip()}
    except OSError:
        pass
    skip = {
        ".git",
        ".venv",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        "target",
        "dist",
        "build",
        "htmlcov",
    }
    return {
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*")
        if p.is_file() and not (set(p.parts) & skip)
    }


def validate(routes: list[dict], existing: set[str]) -> list[str]:
    """Pure check over route records + existing-path set. Returns error strings."""
    errors: list[str] = []
    seen_keys: set[str] = set()
    seen_aliases: dict[str, str] = {}
    for route in routes:
        key = str(route.get("key", ""))
        where = f"route '{key or '?'}'"
        if not key:
            errors.append("route without key")
            continue
        if key in seen_keys:
            errors.append(f"duplicate route key: {key}")
        seen_keys.add(key)
        for alias in route.get("aliases", []):
            alias = str(alias).lower()
            if alias in seen_aliases:
                errors.append(f"duplicate alias '{alias}': {key} vs {seen_aliases[alias]}")
            else:
                seen_aliases[alias] = key
        language = str(route.get("language", ""))
        if language not in ALLOWED_LANGUAGES:
            errors.append(f"{where}: unknown language '{language}'")
            continue
        if "+" in language and not route.get("language_boundary"):
            errors.append(f"{where}: multi-language route needs language_boundary")
        owner = str(route.get("owner_module", ""))
        if not owner:
            errors.append(f"{where}: owner module missing")
        for owner_path in route.get("owner_paths", []):
            if not any(e == owner_path or e.startswith(owner_path + "/") for e in existing):
                errors.append(f"{where}: owner path missing: {owner_path}")
        allowed_exts = LANGUAGE_EXTENSIONS[language]
        for field in ("primary_files", "secondary_files"):
            for rel in route.get(field, []):
                if rel not in existing:
                    errors.append(f"{where}: {field} missing: {rel}")
                    continue
                suffix = Path(rel).suffix
                if suffix in allowed_exts:
                    continue
                # Rust-authority routes legitimately list their Python FFI
                # bridges alongside kernels; anything else drifting is real.
                if language == "RUST" and suffix == ".py" and Path(rel).name.startswith("native_"):
                    continue
                errors.append(f"{where}: {field} {rel} extension drifts from language {language}")
        tests = route.get("tests", {})
        for rel in tests.get("files", []):
            if rel not in existing and not any(
                e.startswith(rel.rstrip("/") + "/") for e in existing
            ):
                errors.append(f"{where}: test path missing: {rel}")
        if not tests.get("files") and not tests.get("note"):
            errors.append(f"{where}: empty tests.files needs a note")
        for command in route.get("validation", []):
            runner = command.split()[0] if command.split() else ""
            if runner not in KNOWN_RUNNERS:
                errors.append(f"{where}: unknown validation runner: {command}")
                continue
            for token in command.split():
                # Only concrete script paths are checkable: skip directories,
                # placeholders (<touched-file>), flags, and filters.
                if "/" not in token or "<" in token or not token.endswith(".py"):
                    continue
                if token not in existing:
                    errors.append(f"{where}: validation script missing: {token}")
        for pattern in route.get("forbidden", []):
            if not fnmatch.filter(existing, pattern):
                errors.append(f"{where}: forbidden pattern matches nothing: {pattern}")
        for mod_path, names in route.get("symbols", {}).items():
            if mod_path not in existing:
                errors.append(f"{where}: symbol file missing: {mod_path}")
                continue
            defined = _symbols_in(ROOT / mod_path)
            for name in names:
                if name not in defined:
                    errors.append(f"{where}: symbol '{name}' not found in {mod_path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the task routing index")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))["routes"]
        existing = _existing_paths()
    except (OSError, ValueError, KeyError) as exc:
        print(f"Routes validation ERROR: {exc}")
        return 2
    errors = validate(routes, existing)
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
    elif errors:
        print(f"Routes validation FAILED ({len(errors)} errors):")
        for error in errors[:30]:
            print(f"  - {error}")
    else:
        print(f"Routes validation PASSED ({len(routes)} routes checked)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
