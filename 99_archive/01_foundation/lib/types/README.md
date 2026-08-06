# lib/types/ — Types

## What Are Types?

In software engineering, a **type** defines what kind of thing a value is and what operations can be performed on it.

`"hello"` is a **string** type. You can uppercase it, split it, find characters in it.
`42` is an **integer** type. You can add it, multiply it, compare it.
`Currency("150.50", "USD")` is a **Currency** type. You can add it (to other USD amounts), compare it, display it.

Types prevent mistakes. If a function expects `Currency` and receives a string, the compiler (or type checker) catches the error before it reaches production.

---

## What Is Inside

| Type | What It Represents | Why It Exists |
|---|---|---|
| `Currency` | Exact monetary amounts | Computers are bad at decimal math. `0.1 + 0.2` in Python gives `0.30000000000000004`. Currency uses proper decimal arithmetic. |
| `Timestamp` | A precise moment in time | Trading requires knowing exactly when events happened. Timestamps are always in UTC to avoid timezone confusion. |
| `Bounded` | A value with minimum and maximum limits | Many trading concepts have valid ranges (position size 0 to 1000, leverage 0 to 2, etc.). Bounded enforces these limits. |
| `Nullable` | A value that might be missing | Market data is often incomplete. Nullable makes missing data explicit rather than using `None` ambiguously. |

---

## Quick Example

```python
from lib.types.currency import Currency

# Create money — always use strings, never floats
price = Currency("150.50", "USD")
fee = Currency("1.50", "USD")

# Add, subtract, multiply, divide — all precise
total = price + fee       # USD 152.00
```

---

## Continue to the Next Lesson

→ `lib/utils/` — Utility functions
