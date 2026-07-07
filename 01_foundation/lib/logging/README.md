# lib/logging/ — Logging

## What Is Logging?

**Logging** is the practice of recording what a system does while it runs. Log entries are timestamped messages that say things like:

```
2025-07-05 20:00:00 [INFO] market.ingestion: Bar received for SPY
2025-07-05 20:00:01 [WARNING] execution.order: Order rejected: insufficient capital
2025-07-05 20:00:02 [ERROR] broker.alpaca: Connection timeout
```

Logs are essential for:
- **Debugging** — what happened before an error?
- **Monitoring** — is the system behaving normally?
- **Auditing** — what trades were placed and why?

---

## What Is Inside

| File | What It Does |
|---|---|
| `setup.py` | Configures the logging system — output format, destination (console/file), verbosity level |

---

## Quick Example

```python
from lib.logging.setup import configure_logging, get_logger

# Set up logging once at system startup
configure_logging(level="INFO")

# Get a logger for your module
logger = get_logger(__name__)
logger.info("System started")
logger.warning("Something unusual happened")
logger.error("Something failed")
```

---

## Continue to the Next Lesson

→ `lib/patterns/` — Reusable design patterns
