"""Working memory (bounded short-lived context) + long-term memory (persisted).

Working memory is strictly bounded: every buffer has an explicit maxlen.
Long-term memory persists checkpoints, incidents and broker behavior to
JSON — and can NEVER override deterministic risk rules (no path exists
from memory to RiskPolicy; memory output is diagnostics + warm-start data).
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class WorkingMemory:
    """Rolling runtime context with hard bounds on every buffer."""

    max_events: int = 500
    max_signals: int = 100

    def __post_init__(self) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._signals: deque[dict[str, Any]] = deque(maxlen=self.max_signals)
        self.regime: str = "UNKNOWN"
        self.position_qty: float = 0.0
        self.pending_orders: int = 0
        self.last_execution_state: str = ""

    def note_event(self, kind: str, symbol: str, seq: int) -> None:
        self._events.append({"kind": kind, "symbol": symbol, "seq": seq})

    def note_signal(self, signal_id: str, side: str) -> None:
        self._signals.append({"signal_id": signal_id, "side": side})

    @property
    def recent_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    @property
    def recent_signals(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._signals)

    def snapshot(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "position_qty": self.position_qty,
            "pending_orders": self.pending_orders,
            "last_execution_state": self.last_execution_state,
            "recent_events": len(self._events),
            "recent_signals": len(self._signals),
        }


@dataclass
class Incident:
    kind: str
    detail: str
    timestamp: str


class LongTermMemory:
    """JSON-persisted operational memory: checkpoints, incidents, behavior."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._store: dict[str, Any] = {"checkpoints": {}, "incidents": [], "broker": {}}
        if self._path is not None and self._path.is_file():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for key in self._store:
                        if key in loaded:
                            self._store[key] = loaded[key]
            except Exception:
                pass

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._store, indent=2, sort_keys=True), encoding="utf-8")

    def save_checkpoint(self, key: str, state: dict[str, Any]) -> None:
        self._store["checkpoints"][key] = state
        self._save()

    def load_checkpoint(self, key: str) -> dict[str, Any] | None:
        checkpoint = self._store["checkpoints"].get(key)
        return dict(checkpoint) if isinstance(checkpoint, dict) else None

    def record_incident(self, incident: Incident) -> None:
        self._store["incidents"].append(
            {"kind": incident.kind, "detail": incident.detail, "timestamp": incident.timestamp}
        )
        self._save()

    def record_broker(self, name: str, observation: dict[str, Any]) -> None:
        self._store["broker"][name] = observation
        self._save()

    @property
    def incidents(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._store["incidents"])

    def advise(self) -> dict[str, Any]:
        """Diagnostics-only summary. Callers must not treat this as authority."""
        return {
            "incident_count": len(self._store["incidents"]),
            "checkpoint_keys": sorted(self._store["checkpoints"]),
            "brokers_observed": sorted(self._store["broker"]),
        }
