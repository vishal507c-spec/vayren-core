"""Global API throttle — minimum interval between broker API calls.

Re-exports Throttle from broker.common.throttle.
"""

from __future__ import annotations

from broker.common.throttle import Throttle

__all__ = ["Throttle"]
