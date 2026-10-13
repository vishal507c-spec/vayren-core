"""Broker session audit — request-id correlated, secret-free operation log.

Records CONNECT requested, token reused, authorization required, verification
failed, DISCONNECT requested and state changed. Never stores secret values.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from broker.error_taxonomy import redact


class SessionAudit:
    """Bounded in-memory audit ring (persisted optionally by the manager)."""

    def __init__(self, cap: int = 200) -> None:
        self._cap = max(1, int(cap))
        self._entries: deque[dict[str, Any]] = deque(maxlen=self._cap)
        self._lock = threading.Lock()

    def record(
        self,
        *,
        request_id: str = "",
        broker_id: str = "",
        action: str = "",
        result: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "request_id": str(request_id or ""),
            "broker_id": str(broker_id or ""),
            "action": str(action or ""),
            "result": str(result or ""),
            "reason": redact(str(reason or ""))[:500],
        }
        with self._lock:
            self._entries.append(entry)
        return dict(entry)

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._entries)[-max(1, int(limit)) :]
        return [dict(item) for item in items]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


__all__ = ["SessionAudit"]
