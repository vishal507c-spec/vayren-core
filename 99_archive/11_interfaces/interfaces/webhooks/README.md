# interfaces/webhooks/ — Webhooks

## What Are Webhooks?

Webhooks are **HTTP callbacks** — external systems send POST requests to our URLs when events happen. Examples:
- Broker sends a webhook when an order fills
- Data provider sends a webhook when new data is available
- Monitoring system sends a webhook on alert

---

## What Is Inside

| File | Purpose |
|---|---|
| `handlers.py` | Webhook endpoint handlers (receives + validates + routes) |
| `verifiers.py` | Signature verification (HMAC, JWT) to authenticate webhook origin |
