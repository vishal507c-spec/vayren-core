"""Validate that the repository structure follows the blueprint.

Domains are now inside numbered navigation folders (NN_CHAPTER/domain/).
This script discovers them by scanning all NN_* directories at the repo root.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOMAIN_STRUCTURE: dict[str, list[str]] = {
    "market": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "events/__init__.py", "tests/__init__.py", "conftest.py"],
    "signals": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "events/__init__.py", "tests/__init__.py", "conftest.py"],
    "strategies": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "events/__init__.py", "tests/__init__.py", "conftest.py"],
    "execution": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "events/__init__.py", "tests/__init__.py", "conftest.py"],
    "portfolio": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "events/__init__.py", "tests/__init__.py", "conftest.py"],
    "risk": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "events/__init__.py", "tests/__init__.py", "conftest.py"],
    "analytics": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "events/__init__.py", "tests/__init__.py", "conftest.py"],
    "research": ["__init__.py", "README.md", "tests/__init__.py", "conftest.py"],
    "platform": ["__init__.py", "README.md", "models/__init__.py", "services/__init__.py", "tests/__init__.py", "conftest.py"],
    "interfaces": ["__init__.py", "README.md", "tests/__init__.py", "conftest.py"],
    "infrastructure": ["README.md"],
    "knowledge": ["README.md"],
    "lib": ["__init__.py", "README.md", "tests/__init__.py", "conftest.py"],
}

# Map domain names to their numbered chapter folders
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


def main() -> int:
    errors: list[str] = []
    for domain, required in DOMAIN_STRUCTURE.items():
        chapter = DOMAIN_CHAPTERS[domain]
        domain_path = ROOT / chapter / domain
        if not domain_path.is_dir():
            errors.append(f"Missing domain directory: {chapter}/{domain}/")
            continue
        for item in required:
            item_path = domain_path / item
            if not item_path.exists():
                errors.append(f"Missing: {chapter}/{domain}/{item}")
    if errors:
        print("Structure validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Structure validation PASSED")
    print(f"  {len(DOMAIN_STRUCTURE)} domains checked")
    return 0


if __name__ == "__main__":
    exit(main())
