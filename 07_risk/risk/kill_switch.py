"""Global/strategy/broker kill switches with safe JSON persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class KillSwitchState:
    """One latched emergency stop."""

    engaged: bool = False
    reason: str = ""
    engaged_at: str = ""
    level: str = "global"  # global | strategy | broker


class KillSwitch:
    """Latchable emergency stop. Engaged state blocks all NEW orders.

    Existing positions stay visible and reconcilable — the switch never
    deletes state, it only gates order submission. State persists to a JSON
    file so a restart cannot silently clear an engaged switch.
    """

    LEVELS = ("global", "strategy", "broker")

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._state: dict[str, KillSwitchState] = {
            level: KillSwitchState(level=level) for level in self.LEVELS
        }
        if self._path is not None and self._path.is_file():
            self._load()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        for level in self.LEVELS:
            entry = raw.get(level, {})
            if entry.get("engaged"):
                self._state[level] = KillSwitchState(
                    engaged=True,
                    reason=str(entry.get("reason", "")),
                    engaged_at=str(entry.get("engaged_at", "")),
                    level=level,
                )

    def _save(self) -> None:
        if self._path is None:
            return
        raw = {
            level: {
                "engaged": state.engaged,
                "reason": state.reason,
                "engaged_at": state.engaged_at,
            }
            for level, state in self._state.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")

    def engage(self, reason: str, level: str = "global") -> None:
        """Latch the switch on. Unknown levels raise (fail-closed stays manual)."""
        if level not in self.LEVELS:
            raise ValueError(f"unknown kill-switch level: {level}")
        self._state[level] = KillSwitchState(
            engaged=True, reason=reason, engaged_at=self._now(), level=level
        )
        self._save()

    def disengage(self, level: str = "global") -> None:
        """Release the latch. Requires an explicit call — never automatic."""
        if level not in self.LEVELS:
            raise ValueError(f"unknown kill-switch level: {level}")
        self._state[level] = KillSwitchState(engaged=False, level=level)
        self._save()

    def is_halted(self, level: str = "global") -> bool:
        """True when the given level — or the global switch — is engaged."""
        if level not in self.LEVELS:
            raise ValueError(f"unknown kill-switch level: {level}")
        return self._state["global"].engaged or self._state[level].engaged

    def state(self, level: str = "global") -> KillSwitchState:
        if level not in self.LEVELS:
            raise ValueError(f"unknown kill-switch level: {level}")
        return self._state[level]
