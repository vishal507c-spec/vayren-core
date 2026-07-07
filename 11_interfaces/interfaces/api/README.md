# interfaces/api/ — REST API

## What Is a REST API?

A **REST API** (Representational State Transfer Application Programming Interface) is a way for external systems to communicate with the trading system over HTTP.

It exposes endpoints like:
```
GET  /api/v1/bars/SPY   → Get market data
POST /api/v1/orders     → Place an order
GET  /api/v1/portfolio  → Get portfolio status
```

---

## What Is Inside

| File | Purpose |
|---|---|
| `server.py` | FastAPI application creation and configuration |
| `routes.py` | API endpoint definitions |
| `auth.py` | API key authentication |
| `middleware.py` | Request/response processing (logging, rate limiting) |
