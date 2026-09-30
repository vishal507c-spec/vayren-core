"""Order lifecycle vocabulary — the `OrderState` enum.

Kept as a leaf module (no imports beyond stdlib) so the Rust-backed
lifecycle authority (`execution.native_order_state`) can consume it without
an import cycle. The transition TABLE lives in Rust (`rust/vayren-core`,
`order_state` module); this file holds only the vocabulary.
"""

from __future__ import annotations

from enum import Enum


class OrderState(Enum):
    """Broker-independent order lifecycle (§12 mission spec, M8 extended).

    ``MODIFY_PENDING`` / ``MODIFIED`` (M8 §7) mirror the cancel pair:
    a modify request is in flight, then applied — the order stays live.
    ``UNKNOWN`` exits only via explicit reconciliation (never blind retry).
    """

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    MODIFY_PENDING = "MODIFY_PENDING"
    MODIFIED = "MODIFIED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


__all__ = ["OrderState"]
