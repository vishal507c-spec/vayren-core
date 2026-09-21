"""Global/strategy/broker kill switches with safe JSON persistence.

The latch table, level vocabulary, timestamp format, reload rule and the
serialized document shape are Rust-owned
(`rust/vayren-core/src/kill_switch.rs`, bridge in `risk.native_kill_switch`).
This class is the public facade plus the filesystem call itself — it holds no
kill-switch policy (constitution §8, migration §15).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from risk.native_kill_switch import NativeKillSwitch, levels


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

    LEVELS = levels()

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._kernel = NativeKillSwitch()
        if self._path is not None and self._path.is_file():
            self._load()

    def _load(self) -> None:
        assert self._path is not None
        try:
            text = self._path.read_text(encoding="utf-8")
        except Exception:
            return
        self._kernel.load(text)

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._kernel.serialize(), encoding="utf-8")

    def engage(self, reason: str, level: str = "global") -> None:
        """Latch the switch on. Unknown levels raise (fail-closed stays manual)."""
        self._check_level(level)
        self._kernel.engage(reason, level)
        self._save()

    def disengage(self, level: str = "global") -> None:
        """Release the latch. Requires an explicit call — never automatic."""
        self._check_level(level)
        self._kernel.disengage(level)
        self._save()

    def is_halted(self, level: str = "global") -> bool:
        """True when the given level — or the global switch — is engaged."""
        self._check_level(level)
        return self._kernel.is_halted(level)

    def state(self, level: str = "global") -> KillSwitchState:
        self._check_level(level)
        return KillSwitchState(
            engaged=self._kernel.state_engaged(level),
            reason=self._kernel.reason(level),
            engaged_at=self._kernel.engaged_at(level),
            level=level,
        )

    @classmethod
    def _check_level(cls, level: str) -> None:
        if not isinstance(level, str) or level not in cls.LEVELS:
            raise ValueError(f"unknown kill-switch level: {level}")
