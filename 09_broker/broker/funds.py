"""UBL funds/margin abstraction — UBL-owned domain model (M6).

``TradingFace.funds()`` keeps its ``dict[str, float]`` contract::

    {"available": float, "used": float, "equity": float}

This module adds the small typed helper around that contract:

- :class:`FundsSnapshot` — frozen, validated, deterministic view with
  ``to_dict()`` / ``from_dict()`` round-trip guarantees.
- ``FUNDS_UNKNOWN`` / ``FUNDS_UNSUPPORTED`` — module-level sentinels so
  UNKNOWN, UNSUPPORTED and numeric ``0.0`` never collapse into one meaning
  (a venue that cannot report funds must fail closed, never fake zeros).
- :func:`require_funds` — capability-gated entry point: raises the existing
  unified :class:`UnsupportedCapabilityError` before any ``funds()`` call.

Cash-only semantics (Paper/Sandbox): there is currently no margin engine,
so ``used`` is legitimately ``0.0`` and ``available == equity == capital``.
This is a genuine derivation from the venue's live capital state — not a
hardcoded constant and not mark-to-market (positions are never valued).

Future risk mapping (documentation only — M6 does NOT wire RiskEngine):

- ``FundsSnapshot.available`` → ``RiskRequest.available_capital``
- ``FundsSnapshot.equity`` → ``RiskRequest.equity``

No SDK types, no credentials, no network objects, no broker-specific types.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from broker.capabilities import Caps
from broker.vocab import UnsupportedCapabilityError

FUNDS_AVAILABLE = "available"
FUNDS_USED = "used"
FUNDS_EQUITY = "equity"

_REQUIRED_KEYS: tuple[str, str, str] = (FUNDS_AVAILABLE, FUNDS_USED, FUNDS_EQUITY)


def _check_money(name: str, value: object) -> float:
    """Validate one monetary field; returns it as float. Rejects bool/NaN/inf/negative."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"funds field {name!r} must be a number, got {value!r}")
    amount = float(value)
    if math.isnan(amount) or math.isinf(amount):
        raise ValueError(f"funds field {name!r} must be finite, got {value!r}")
    if amount < 0.0:
        raise ValueError(f"funds field {name!r} must be non-negative, got {value!r}")
    return amount


@dataclass(frozen=True)
class FundsSnapshot:
    """Immutable funds view. Numeric only — availability state travels via
    the ``FUNDS_UNKNOWN`` / ``FUNDS_UNSUPPORTED`` sentinels, never via zeros."""

    available: float
    used: float
    equity: float
    currency: str = "INR"
    account_id: str | None = None
    timestamp: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "available", _check_money("available", self.available))
        object.__setattr__(self, "used", _check_money("used", self.used))
        object.__setattr__(self, "equity", _check_money("equity", self.equity))
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError(f"funds currency must be a non-empty string, got {self.currency!r}")
        if self.account_id is not None and (
            not isinstance(self.account_id, str) or not self.account_id.strip()
        ):
            raise ValueError(f"funds account_id must be a string or None, got {self.account_id!r}")
        if self.timestamp is not None and (
            not isinstance(self.timestamp, str) or not self.timestamp.strip()
        ):
            raise ValueError(f"funds timestamp must be a string or None, got {self.timestamp!r}")

    def to_dict(self) -> dict[str, float]:
        """Exact ``TradingFace.funds()`` contract — money fields only."""
        return {
            FUNDS_AVAILABLE: float(self.available),
            FUNDS_USED: float(self.used),
            FUNDS_EQUITY: float(self.equity),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FundsSnapshot:
        """Strict inverse of :meth:`to_dict` (extra/missing keys rejected)."""
        if not isinstance(payload, dict):
            raise ValueError(f"funds payload must be a dict, got {type(payload).__name__}")
        missing = [key for key in _REQUIRED_KEYS if key not in payload]
        if missing:
            raise ValueError(f"funds payload missing required keys: {missing}")
        extra = sorted(key for key in payload if key not in _REQUIRED_KEYS)
        if extra:
            raise ValueError(f"funds payload has unexpected keys: {extra}")
        return cls(
            available=_check_money(FUNDS_AVAILABLE, payload[FUNDS_AVAILABLE]),
            used=_check_money(FUNDS_USED, payload[FUNDS_USED]),
            equity=_check_money(FUNDS_EQUITY, payload[FUNDS_EQUITY]),
        )


class _FundsUnknown:
    """Sentinel: the venue supports funds but the value is not known."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "FUNDS_UNKNOWN"


class _FundsUnsupported:
    """Sentinel: the venue does not support the funds capability."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "FUNDS_UNSUPPORTED"


FUNDS_UNKNOWN = _FundsUnknown()
FUNDS_UNSUPPORTED = _FundsUnsupported()


def is_unknown(value: object) -> bool:
    """True only for the UNKNOWN sentinel — never for ``0.0`` or UNSUPPORTED."""
    return value is FUNDS_UNKNOWN


def is_unsupported(value: object) -> bool:
    """True only for the UNSUPPORTED sentinel — never for ``0.0`` or UNKNOWN."""
    return value is FUNDS_UNSUPPORTED


def require_funds(face: Any) -> Any:
    """Capability-gated funds access. Returns the face when supported.

    Raises the existing unified :class:`UnsupportedCapabilityError` when the
    face does not advertise ``Caps.ACCOUNT_FUNDS`` — the caller must NOT call
    ``face.funds()``, must NOT substitute zeros, and must NOT fall back to
    another broker.
    """
    try:
        capabilities = face.capabilities()
    except Exception as exc:
        raise UnsupportedCapabilityError(
            f"broker {getattr(face, 'name', '?')!r} has no readable capabilities "
            f"(funds unavailable): {exc}"
        ) from exc
    try:
        supported = bool(capabilities.supports(Caps.ACCOUNT_FUNDS))
    except Exception as exc:
        raise UnsupportedCapabilityError(
            f"broker {getattr(face, 'name', '?')!r} capabilities unreadable "
            f"(funds unavailable): {exc}"
        ) from exc
    if not supported:
        raise UnsupportedCapabilityError(
            f"broker {getattr(face, 'name', '?')!r} does not provide "
            f"{Caps.ACCOUNT_FUNDS} capability"
        )
    return face


__all__ = [
    "FUNDS_AVAILABLE",
    "FUNDS_EQUITY",
    "FUNDS_UNKNOWN",
    "FUNDS_UNSUPPORTED",
    "FUNDS_USED",
    "FundsSnapshot",
    "is_unknown",
    "is_unsupported",
    "require_funds",
]
