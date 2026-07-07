# infrastructure/ci/ — Continuous Integration

## What Is Inside

| File | Purpose |
|---|---|
| `.github/workflows/ci.yml` | GitHub Actions workflow — runs on every push and PR |
| `scripts/` | Helper scripts used by CI pipeline |

---

## CI Pipeline

```
Push/PR → lint (ruff) → typecheck (pyright)
        → test (pytest) → validate-structure
        → validate-imports → build (Docker)
```

All steps must pass before merging.
