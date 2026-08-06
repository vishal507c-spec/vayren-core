# infrastructure/docker/ — Docker Configuration

## What Is Inside

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build: dev → test → production |
| `docker-compose.yml` | Orchestrates services (api, data-worker, scheduler, monitoring) |

---

## Service Architecture

```
docker-compose.yml
├── api            → interfaces/api/server.py (FastAPI)
├── data-worker    → market data ingestion (async consumer)
├── scheduler      → cron-like task scheduling
├── prometheus     → metrics collection
└── grafana        → metrics visualization
```
