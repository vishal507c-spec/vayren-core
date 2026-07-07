# interfaces/ — Interfaces

## What Are Interfaces?

**Interfaces** are the ways humans and external systems interact with the trading system. They include:

- **API** (Application Programming Interface): REST endpoints that web clients and external systems use
- **CLI** (Command-Line Interface): Terminal commands for administration and operations
- **Web Dashboard:** A graphical interface for monitoring and management
- **Webhooks:** Endpoints that external services can call to send data

---

## Purpose

`interfaces/` provides all user-facing and system-facing access points. It is the **boundary** between the trading system and the outside world.

---

## Why This Folder Exists

Without a dedicated interfaces layer, business logic would leak into API handlers and CLI commands. This makes:
- APIs hard to change (changing a route affects business logic)
- Business logic hard to test (you have to call the API to test it)
- Multiple interfaces impossible (you'd duplicate logic for API + CLI)

With a dedicated interfaces layer, API handlers are thin wrappers that call domain services. They contain no business logic.

---

## Visual Diagram

```
    Outside World              interfaces/                      Departments
    
    Web Browser        ──▶  Web Dashboard       ──▶  market/
    REST Client        ──▶  API (FastAPI)       ──▶  signals/
    Terminal           ──▶  CLI (Click)         ──▶  strategies/
    Mobile App         ──▶  Mobile Routes       ──▶  execution/
    External Service   ──▶  Webhook Handlers    ──▶  portfolio/
                                                      risk/
                                                      analytics/
                                                      research/
```

---

## What Is Inside

| Component | Technology | Purpose |
|---|---|---|
| `api/` | FastAPI | REST endpoints for programmatic access |
| `cli/` | Click + Rich | Terminal commands for administration |
| `web/` | — | Web dashboard (frontend + backend) |
| `mobile/` | — | Mobile API proxy (thin wrapper) |
| `webhooks/` | — | Endpoints for external service callbacks |

---

## Quick Example

```python
# interfaces/api/server.py
from fastapi import FastAPI
from interfaces.api.routes import router

app = FastAPI(title="Vayren Core API")
app.include_router(router)


# interfaces/api/routes.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")

@router.get("/health")
async def health():
    return {"status": "ok"}
```

---

## Continue to the Next Lesson

→ `infrastructure/` — How the system is deployed and operated
