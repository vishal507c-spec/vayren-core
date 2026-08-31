"""Validate import rules across the repository.

Domains live inside numbered startup-flow chapters (NN_CHAPTER/domain/).
Files under 90_brain (docs) are excluded.
"""

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_TOP_DIRS = {"90_brain"}

# Map domain package names to their numbered chapter folders
DOMAIN_CHAPTERS: dict[str, str] = {
    "app": "00_app",
    "core": "01_core",
    "market": "03_market",
    "chart": "04_chart",
    "data": "02_data",
}

# Domains that should only import from lib/ and immediate upstream
DOMAIN_DEPS: dict[str, set[str]] = {
    "app": {"core", "market", "chart", "data"},
    "core": set(),
    "market": {"core"},
    "chart": {"core", "market"},
    "data": {"core"},
}


def check_file_domain(filepath: Path) -> str | None:
    """Determine which domain a file belongs to, or None if it is excluded."""
    try:
        parts = filepath.relative_to(ROOT).parts
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    if parts[0] in EXCLUDED_TOP_DIRS:
        return None
    # parts[0] is the numbered folder (e.g. "03_market")
    # parts[1] is the domain package name (e.g. "market")
    if parts[1] in DOMAIN_DEPS:
        return parts[1]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate import rules across the repository")
    parser.add_argument("--json", action="store_true", help="print a JSON summary instead of text")
    args = parser.parse_args()
    errors: list[str] = []
    for pyfile in ROOT.rglob("*.py"):
        domain = check_file_domain(pyfile)
        if domain is None:
            continue
        if "test" in pyfile.name:
            continue
        if pyfile.parent.name == "tests":
            continue
        try:
            tree = ast.parse(pyfile.read_text(encoding="utf-8"))
        except SyntaxError:
            errors.append(f"Syntax error: {pyfile}")
            continue
        allowed_imports = DOMAIN_DEPS.get(domain, set())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if (
                        parts[0] in DOMAIN_DEPS
                        and parts[0] != domain
                        and parts[0] not in allowed_imports
                    ):
                        errors.append(f"{pyfile}: imports {alias.name} (not allowed from {domain})")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in DOMAIN_DEPS
                and node.module.split(".")[0] != domain
                and node.module.split(".")[0] not in allowed_imports
            ):
                errors.append(f"{pyfile}: imports {node.module} (not allowed from {domain})")
    if args.json:
        print(json.dumps({"ok": not errors, "error_count": len(errors), "errors": errors}))
        return 1 if errors else 0
    if errors:
        print("Import validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Import validation PASSED")
    return 0


if __name__ == "__main__":
    exit(main())
