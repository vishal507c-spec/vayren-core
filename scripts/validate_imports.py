"""Validate import rules across the repository.

Domains live inside numbered startup-flow chapters (NN_CHAPTER/domain/).
Files under 90_brain (docs) are excluded.
"""

import argparse
import ast
import json
from collections.abc import Iterator
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
    "strategy": "05_strategy",
    "backtest": "06_backtest",
}

# Domains that should only import from lib/ and immediate upstream
DOMAIN_DEPS: dict[str, set[str]] = {
    "app": {"core", "market", "chart", "data", "strategy", "backtest"},
    "core": set(),
    "market": {"core"},
    "chart": {"core", "market"},
    "data": {"core"},
    "strategy": {"core", "market"},
    "backtest": {"core", "market", "strategy"},
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


def _is_type_checking_guard(node: ast.If) -> bool:
    """True for ``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:`` blocks.

    Imports guarded this way create no runtime coupling (annotations only),
    so they are not runtime-dependency violations. Static checkers still see
    them; only the runtime layering rule ignores them.
    """
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _iter_runtime_imports(tree: ast.AST) -> Iterator[ast.stmt]:
    """Yield Import/ImportFrom nodes, skipping TYPE_CHECKING-guarded blocks."""
    stack: list[tuple[ast.AST, bool]] = [(tree, False)]
    while stack:
        node, in_tc = stack.pop()
        if isinstance(node, ast.If) and _is_type_checking_guard(node):
            stack.extend((child, True) for child in node.body)
            stack.extend((child, in_tc) for child in node.orelse)
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not in_tc:
            yield node
        stack.extend((child, in_tc) for child in ast.iter_child_nodes(node))


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
            source = pyfile.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except SyntaxError:
            errors.append(f"Syntax error: {pyfile}")
            continue
        lines = source.splitlines()
        allowed_imports = DOMAIN_DEPS.get(domain, set())
        for node in _iter_runtime_imports(tree):
            stmt = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if (
                        parts[0] in DOMAIN_DEPS
                        and parts[0] != domain
                        and parts[0] not in allowed_imports
                    ):
                        errors.append(
                            f"{pyfile}:{node.lineno}: imports {alias.name} "
                            f"(not allowed from {domain}) | {stmt}"
                        )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in DOMAIN_DEPS
                and node.module.split(".")[0] != domain
                and node.module.split(".")[0] not in allowed_imports
            ):
                errors.append(
                    f"{pyfile}:{node.lineno}: imports {node.module} "
                    f"(not allowed from {domain}) | {stmt}"
                )
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
