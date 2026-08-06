# lib/utils/ — Utilities

## What Are Utilities?

**Utilities** are functions that perform common operations. They are the "tools" in the library toolbox — small, focused, reusable.

If types are nouns (things), utilities are verbs (actions).

---

## What Is Inside

| File | Functions | What They Do |
|---|---|---|
| `time_utils.py` | `now_utc()`, `to_iso()`, `parse_iso()`, `is_market_hours()` | Work with time — get current time, format it, parse it, check if markets are open |
| `math_utils.py` | `round_to_tick()`, `weight()`, `weighted_average()` | Financial math — round to valid price increments, compute weighted averages |
| `serialization.py` | `to_json()`, `from_json()` | Convert data to/from JSON format, handling our custom types |
| `decorators.py` | `@retry`, `@async_retry`, `@timed` | Function wrappers that add retry logic or timing measurements |

---

## Quick Example

```python
from lib.utils.time_utils import now_utc, is_market_hours

current = now_utc()
if is_market_hours(current):
    print("Markets are open")
```

---

## Continue to the Next Lesson

→ `lib/config/` — Configuration system
