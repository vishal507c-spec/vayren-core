"""Validate import rules across the repository.

Domains are now inside numbered navigation folders (NN_CHAPTER/domain/).
This script maps Python package names to their numbered chapter locations.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Map domain package names to their numbered chapter folders
DOMAIN_CHAPTERS: dict[str, str] = {
    "lib": "01_foundation",
    "platform": "02_platform",
    "market": "03_market",
    "signals": "04_signals",
    "strategies": "05_strategies",
    "risk": "06_risk",
    "execution": "07_execution",
    "portfolio": "08_portfolio",
    "analytics": "09_analytics",
    "research": "10_research",
    "interfaces": "11_interfaces",
    "infrastructure": "12_infrastructure",
    "knowledge": "13_knowledge",
}

# Domains that should only import from lib/ and immediate upstream
DOMAIN_DEPS: dict[str, set[str]] = {
    "market": {"lib"},
    "signals": {"market", "lib"},
    "strategies": {"signals", "platform", "lib"},
    "execution": {"platform", "lib"},
    "portfolio": {"strategies", "execution", "lib"},
    "risk": {"portfolio", "lib"},
    "analytics": {"portfolio", "risk", "lib"},
    "research": {"market", "analytics", "lib"},
    "platform": {"lib"},
    "interfaces": {"lib"},  # can import any public API
    "infrastructure": set(),
    "knowledge": set(),
    "lib": set(),
}

FORBIDDEN_DOMAIN_IMPORTS = {
    "platform": {"market", "signals", "strategies", "execution", "portfolio", "risk", "analytics", "research"},
    "lib": set(d for d in DOMAIN_DEPS if d != "lib"),
}


def check_file_domain(filepath: Path) -> str | None:
    """Determine which domain a file belongs to.
    
    Looks at the path relative to ROOT. After the refactor, paths look like:
    03_market/market/services/ingestion.py → domain is "market"
    """
    try:
        parts = filepath.relative_to(ROOT).parts
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    # parts[0] is the numbered folder (e.g. "03_MARKET")
    # parts[1] is the domain package name (e.g. "market")
    if parts[1] in DOMAIN_DEPS:
        return parts[1]
    return None


def main() -> int:
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
                    if parts[0] in DOMAIN_DEPS and parts[0] != domain and parts[0] not in allowed_imports:
                        errors.append(f"{pyfile}: imports {alias.name} (not allowed from {domain})")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    parts = node.module.split(".")
                    if parts[0] in DOMAIN_DEPS and parts[0] != domain and parts[0] not in allowed_imports:
                        errors.append(f"{pyfile}: imports {node.module} (not allowed from {domain})")
    if errors:
        print("Import validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Import validation PASSED")
    return 0


if __name__ == "__main__":
    exit(main())
