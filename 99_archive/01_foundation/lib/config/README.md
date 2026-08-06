# lib/config/ — Configuration

## What Is Configuration?

**Configuration** (often abbreviated "config") is the settings that control how a system behaves without changing its code.

Instead of hard-coding "the API key is X" or "the default symbol is SPY", you put these values in a configuration file. This means:
- You can change behavior without modifying code
- Different environments (development, paper trading, live trading) can have different settings
- Sensitive values (API keys) are kept separate from code

---

## What Is Inside

| File | What It Does |
|---|---|
| `loader.py` | Reads configuration from YAML files and makes them available to the system |
| `schemas.py` | Defines the structure of valid configuration — what fields exist, what types they are, what defaults they have |

---

## Quick Example

```python
from lib.config.loader import load_config

# Load configuration from default location (~/.vayren/config/config.yaml)
config = load_config()

# Access settings
print(config.market.default_bar_size)  # "1d"
print(config.environment)              # "development"
```

---

## Continue to the Next Lesson

→ `lib/logging/` — Logging system
