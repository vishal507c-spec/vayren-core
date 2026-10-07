"""Backend binding for the remote transport (adapter, no logic).

The remote server never talks to strategies, sessions, or brokers itself:
it talks to a :class:`BackendGateway`. The production binding
(:class:`HeadlessGateway`) delegates to the EXISTING headless backend
helpers — the same snapshot the native UI reads and the same action path
with the same safety gates — so remote commands can never bypass execution
mode, capital/risk validation, broker health, or reconciliation.
"""

from __future__ import annotations

import contextlib
from typing import Any


class BackendGateway:
    """Minimal backend surface the remote server needs (injectable)."""

    def get_snapshot(self) -> dict[str, Any]:
        """Current backend snapshot (live-service shape)."""
        raise NotImplementedError

    def apply_command(self, action: dict[str, Any]) -> dict[str, Any]:
        """Apply one control action; returns the fresh backend snapshot."""
        raise NotImplementedError

    def close(self) -> None:
        """Release backend resources (manager workers, if any)."""


def entry_signature(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    """Identity of one activity entry for cursor diffing."""
    if not isinstance(entry, dict):
        return ("", "", "", "")
    return (
        str(entry.get("timestamp", "") or ""),
        str(entry.get("strategy", "") or ""),
        str(entry.get("symbol", "") or ""),
        str(entry.get("event", "") or ""),
    )


def new_entries(
    previous: list[dict[str, Any]], current: list[dict[str, Any]], *, cap: int = 20
) -> tuple[list[dict[str, Any]], bool]:
    """Incremental activity slice since the last poll (bounded).

    Finds the last previously-seen entry in the current list and returns
    everything after it. When the overlap is gone (restart, overflow) the
    tail is returned with ``resync=True`` so the client knows it missed
    history instead of silently seeing a partial stream.
    """
    cap = max(1, int(cap))
    if not current:
        return [], False
    if not previous:
        return list(current[-cap:]), True
    seen = {entry_signature(item) for item in previous}
    fresh: list[dict[str, Any]] = []
    for item in current:
        if entry_signature(item) in seen:
            fresh = []
        else:
            fresh.append(item)
    if not fresh:
        return [], False
    if len(fresh) >= len(current):
        return list(current[-cap:]), True
    return fresh[-cap:], False


class HeadlessGateway(BackendGateway):
    """Production binding over the existing headless backend.

    Importing this module starts nothing: the headless helpers (and the
    broker manager) are resolved lazily on first use, and this gateway
    never calls ``start`` — serving transport must never start trading.
    """

    def __init__(self, data_dir: str, strategy_dir: str) -> None:
        self._data_dir = str(data_dir)
        self._strategy_dir = str(strategy_dir)
        self._broker_manager: Any | None = None
        self._manager_started = False

    def _ensure_manager(self) -> Any | None:
        """The SYSTEM broker manager (same bootstrap as headless)."""
        if self._manager_started:
            return self._broker_manager
        self._manager_started = True
        try:
            import broker.providers  # noqa: F401
            from app.services.broker_manager import BrokerManager

            manager = BrokerManager(data_dir=self._data_dir)
            for broker_id in manager.broker_ids():
                manager.submit_check(broker_id)
            self._broker_manager = manager
        except Exception:
            self._broker_manager = None
        return self._broker_manager

    def get_snapshot(self) -> dict[str, Any]:
        """Fresh live-service snapshot (same source as the native UI)."""
        from app.headless import _trading_service_snapshot

        return _trading_service_snapshot(self._data_dir, self._strategy_dir, self._ensure_manager())

    def apply_command(self, action: dict[str, Any]) -> dict[str, Any]:
        """One control action through the existing gated action path."""
        from app.headless import _live_action

        return _live_action(
            self._data_dir,
            self._strategy_dir,
            dict(action),
            self._ensure_manager(),
        )

    def close(self) -> None:
        """Stop the broker manager worker (never touches trading state)."""
        manager, self._broker_manager = self._broker_manager, None
        if manager is not None:
            with contextlib.suppress(Exception):
                manager.stop_worker()
