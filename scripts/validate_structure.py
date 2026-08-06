"""Validate that the repository structure follows the blueprint.

Domains live inside numbered startup-flow chapters (NN_CHAPTER/domain/).
Archived modules in 99_archive and knowledge in 90_brain are excluded.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOMAIN_STRUCTURE: dict[str, list[str]] = {
    "app": [
        "__init__.py",
        "README.md",
        "bootstrap/__init__.py",
        "lifecycle/__init__.py",
        "tests/__init__.py",
    ],
    "core": [
        "__init__.py",
        "README.md",
        "event_bus/__init__.py",
        "events/__init__.py",
        "logger/__init__.py",
        "registry/__init__.py",
        "tests/__init__.py",
    ],
    "market": [
        "__init__.py",
        "README.md",
        "models/__init__.py",
        "database/__init__.py",
        "repository/__init__.py",
        "loader/__init__.py",
        "events/__init__.py",
        "tests/__init__.py",
    ],
    "chart": [
        "__init__.py",
        "README.md",
        "models/__init__.py",
        "engine/__init__.py",
        "renderer/__init__.py",
        "widgets/__init__.py",
        "windows/__init__.py",
        "events/__init__.py",
        "tests/__init__.py",
    ],
}

# Map domain names to their numbered chapter folders
DOMAIN_CHAPTERS: dict[str, str] = {
    "app": "00_app",
    "core": "01_core",
    "market": "02_market",
    "chart": "03_chart",
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
