# scripts/ — Utility Scripts

## What Is Inside

| Script | Purpose |
|---|---|
| `validate_structure.py` | Checks that every department follows the standard structure (models, services, events, adapters, tests, conftest.py) |
| `validate_imports.py` | Checks that no department imports from another department's internal modules (public API only) |
| `bootstrap.py` | Initial setup script for new development environments |

---

## Running

```bash
python scripts/validate_structure.py
python scripts/validate_imports.py
```
