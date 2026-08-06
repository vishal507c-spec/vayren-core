"""Types — Fundamental Value Objects

Types are the basic "things" that the entire system works with.
They represent real-world concepts like money, time, limits, and optional values.

Available types:
    Currency  — Exact monetary amounts (no floating-point errors)
    Timestamp — Precise moments in time (always UTC)
    Bounded   — A value constrained between minimum and maximum
    Nullable  — A value that may be explicitly absent

Usage:
    from lib.types.currency import Currency
    from lib.types.timestamp import Timestamp
    from lib.types.bounded import Bounded
"""

from lib.types.currency import Currency
from lib.types.timestamp import Timestamp
from lib.types.bounded import Bounded
from lib.types.nullable import Nullable

__all__ = ["Currency", "Timestamp", "Bounded", "Nullable"]
