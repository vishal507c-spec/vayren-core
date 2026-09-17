"""Deterministic fingerprints for research reproducibility.

Every fingerprint is a SHA-256 hex digest over canonical JSON (sorted keys,
no timestamps, no random ids). A fingerprint changes if and only if a
meaningful research input changes — never because of wall-clock time or
execution order.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical(value: Any) -> str:
    """Canonical JSON encoding for fingerprinting (sorted keys, compact)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint_config(config: dict[str, Any]) -> str:
    """Fingerprint the research configuration.

    Covers strategy/version/universe/symbols/timeframe/range/params/costs/side/capital.
    """
    return hashlib.sha256(_canonical(config).encode("utf-8")).hexdigest()


def fingerprint_result(summary: dict[str, Any]) -> str:
    """Fingerprint the executed result summary (trade economics + counts).

    Timestamps of individual trades are part of the economics (entry/exit
    times identify the trade), so they are included; wall-clock metadata
    (execution time, log lines) must never be passed in here.
    """
    return hashlib.sha256(_canonical(summary).encode("utf-8")).hexdigest()


def short_fingerprint(full: str, length: int = 12) -> str:
    """Human-readable prefix of a fingerprint (display only, never identity)."""
    return str(full)[: max(1, length)]
