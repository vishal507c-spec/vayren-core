"""Vayren Core — Shared Library Domain.

lib/ provides foundation types, utilities, configuration, and patterns
that every other domain depends on. It has zero internal dependencies.

Import directly from lib submodules as needed:
    from lib.types.currency import Currency
    from lib.utils.time_utils import now_utc
    from lib.config.loader import load_config
"""

from lib.types.currency import Currency
from lib.types.timestamp import Timestamp
from lib.types.bounded import Bounded
from lib.utils.time_utils import now_utc, to_iso
from lib.logging.setup import configure_logging

__all__ = [
    "Currency",
    "Timestamp",
    "Bounded",
    "now_utc",
    "to_iso",
    "configure_logging",
]
