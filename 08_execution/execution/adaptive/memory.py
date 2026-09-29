"""Working memory (bounded short-lived context) + long-term memory (persisted).

Working memory is strictly bounded: every buffer has an explicit maxlen.
Long-term memory persists checkpoints, incidents and broker behavior to
JSON — and can NEVER override deterministic risk rules (no path exists
from memory to RiskPolicy; memory output is diagnostics + warm-start data).
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Broker observations persist diagnostics only: anything that could carry a
# secret value is dropped before it reaches the store (case-insensitive
# substring match for secret/token/password, prefix match for session*).
_SECRET_SUBSTRINGS = ("secret", "token", "password")
_SESSION_PREFIX = "session"

_BROKER_HISTORY_MAX = 50


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
        return tuple(copy.deepcopy(event) for event in self._events)

    @property
    def recent_signals(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(signal) for signal in self._signals)

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
                    self._normalize_broker_history()
                else:
                    self._backup_corrupt("top-level JSON is not an object")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._backup_corrupt(str(exc))

    def _backup_corrupt(self, reason: str) -> None:
        """Preserve a corrupt store beside the original and start empty.

        The corrupt file is never silently discarded (`except: pass` lost
        operator history); it is renamed with a timestamp suffix and a
        warning names both paths so the incident is auditable.
        """
        assert self._path is not None
        stamp = time.strftime("%Y%m%dT%H%M%S")
        backup = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        try:
            os.replace(self._path, backup)
            _log.warning(
                "long-term memory store corrupt (%s); moved %s to %s and started empty",
                reason,
                self._path,
                backup,
            )
        except OSError as exc:
            _log.warning(
                "long-term memory store corrupt (%s); backup of %s failed: %s",
                reason,
                self._path,
                exc,
            )

    @staticmethod
    def _sanitize_observation(observation: dict[str, Any]) -> dict[str, Any]:
        """Copy keeping diagnostics only; secret-bearing keys never persist."""
        clean: dict[str, Any] = {}
        for key, value in observation.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SECRET_SUBSTRINGS):
                continue
            if lowered.startswith(_SESSION_PREFIX):
                continue
            clean[key] = copy.deepcopy(value)
        return clean

    def _normalize_broker_history(self) -> None:
        """Migrate legacy `broker[name] = dict` rows to `broker[name] = [dict]`."""
        broker = self._store.get("broker")
        if not isinstance(broker, dict):
            self._store["broker"] = {}
            return
        for name, entry in list(broker.items()):
            if isinstance(entry, list):
                broker[name] = [
                    self._sanitize_observation(o) for o in entry if isinstance(o, dict)
                ][-_BROKER_HISTORY_MAX:]
            elif isinstance(entry, dict):
                broker[name] = [self._sanitize_observation(entry)]
            else:
                broker[name] = []

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            "w",
            encoding="utf-8",
            dir=self._path.parent,
            delete=False,
            suffix=".tmp",
        )
        try:
            json.dump(self._store, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            with contextlib.suppress(OSError):
                handle.close()
        try:
            os.replace(handle.name, self._path)
        except OSError:
            Path(handle.name).unlink(missing_ok=True)
            raise
        else:
            try:
                dir_fd = os.open(self._path.parent, os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)

    def save_checkpoint(self, key: str, state: dict[str, Any]) -> None:
        self._store["checkpoints"][key] = copy.deepcopy(state)
        self._save()

    def load_checkpoint(self, key: str) -> dict[str, Any] | None:
        checkpoint = self._store["checkpoints"].get(key)
        return copy.deepcopy(checkpoint) if isinstance(checkpoint, dict) else None

    def record_incident(self, incident: Incident) -> None:
        self._store["incidents"].append(
            {"kind": incident.kind, "detail": incident.detail, "timestamp": incident.timestamp}
        )
        self._save()

    def record_broker(self, name: str, observation: dict[str, Any]) -> None:
        history = self._store["broker"].setdefault(name, [])
        if not isinstance(history, list):
            history = self._store["broker"][name] = [history]
        history.append(self._sanitize_observation(observation))
        del history[:-_BROKER_HISTORY_MAX]
        self._save()

    @property
    def incidents(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(incident) for incident in self._store["incidents"])

    def advise(self) -> dict[str, Any]:
        """Diagnostics-only summary. Callers must not treat this as authority."""
        return {
            "incident_count": len(self._store["incidents"]),
            "checkpoint_keys": sorted(self._store["checkpoints"]),
            "brokers_observed": sorted(self._store["broker"]),
        }
