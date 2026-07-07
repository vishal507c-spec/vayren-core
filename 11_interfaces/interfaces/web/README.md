# interfaces/web/ — Web Dashboard

## What Is Inside

| Component | Technology | Purpose |
|---|---|---|
| `frontend/` | React + TypeScript | Browser-based dashboard for monitoring, trading, reporting |
| `backend/` | Python (FastAPI) | Serves dashboard API endpoints |

---

## Architecture

```
Browser → frontend (React) → backend (FastAPI) → domain services
```

The web dashboard is a **read-heavy** interface. All write operations (submitting orders, changing strategies) go through the API.
