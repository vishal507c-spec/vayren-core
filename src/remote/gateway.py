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
        """The SYSTEM broker manager — shared singleton, not a second instance.

        Headless IPC and the EC2 gateway use the same authoritative manager
        (same worker, same venue registration, same snapshot source), so local
        and remote views can never drift.
        """
        if self._manager_started:
            return self._broker_manager
        self._manager_started = True
        try:
            import broker.providers  # noqa: F401
            from app.services.broker_manager import shared_manager

            self._broker_manager = shared_manager(self._data_dir)
        except Exception:
            self._broker_manager = None
        return self._broker_manager

    def get_snapshot(self) -> dict[str, Any]:
        """Fresh live-service snapshot (same source as the native UI).

        The authoritative unified broker session rides on EVERY snapshot —
        not only after broker commands — so the remote SYSTEM panel and
        LIVE always read the same EC2-owned session state and can never
        render opposite verdicts from different sections.
        """
        from app.headless import _trading_service_snapshot

        live = _trading_service_snapshot(self._data_dir, self._strategy_dir, self._ensure_manager())
        with contextlib.suppress(Exception):
            manager = self._ensure_manager()
            if manager is not None and isinstance(live, dict):
                active = str((manager.snapshot() or {}).get("active_broker", "") or "")
                if active:
                    live["broker_session"] = manager.get_session_snapshot(active)
        return live

    def apply_command(self, action: dict[str, Any]) -> dict[str, Any]:
        """One control action through the existing gated action path."""
        from app.headless import _live_action

        act = dict(action)
        name = str(act.get("action", "") or "").strip().lower()
        # Broker session commands go straight to the authoritative manager
        # (same path the local SYSTEM screen uses) — never a parallel flow.
        if name in ("connect_broker", "disconnect_broker", "revoke_session", "broker_status"):
            return self._apply_broker_command(act)
        return _live_action(
            self._data_dir,
            self._strategy_dir,
            act,
            self._ensure_manager(),
        )

    def _apply_broker_command(self, action: dict[str, Any]) -> dict[str, Any]:
        """Broker connect/disconnect/status via the shared manager."""
        from app.headless import _trading_service_snapshot

        name = str(action.get("action", "") or "").strip().lower()
        broker_id = str(action.get("broker_id") or action.get("broker") or "fyers").strip()
        request_id = str(action.get("request_id", "") or "")
        manager = self._ensure_manager()
        if manager is None:
            return {"error": "broker backend unavailable", "session_status": "STOPPED"}
        if name == "connect_broker":
            credentials = action.get("credentials")
            creds = dict(credentials) if isinstance(credentials, dict) else {}
            ok, message, session = manager.connect_broker(broker_id, creds, request_id=request_id)
            snapshot = _trading_service_snapshot(self._data_dir, self._strategy_dir, manager)
            snapshot["broker_session"] = session
            snapshot["action_note"] = message
            snapshot["action_ok"] = ok
            return snapshot
        if name == "disconnect_broker":
            ok, message = manager.disconnect_broker(broker_id, request_id=request_id)
            with contextlib.suppress(Exception):
                from app import headless as _headless

                _headless._ARMED[0] = False
                svc = _headless._live_service(self._data_dir, self._strategy_dir, manager)
                if svc is not None:
                    svc.note_armed(False)
            snapshot = _trading_service_snapshot(self._data_dir, self._strategy_dir, manager)
            snapshot["broker_session"] = manager.get_session_snapshot(broker_id)
            snapshot["action_note"] = message
            snapshot["action_ok"] = ok
            return snapshot
        if name == "revoke_session":
            ok, message = manager.revoke_session(broker_id, request_id=request_id)
            with contextlib.suppress(Exception):
                from app import headless as _headless

                _headless._ARMED[0] = False
                svc = _headless._live_service(self._data_dir, self._strategy_dir, manager)
                if svc is not None:
                    svc.note_armed(False)
            snapshot = _trading_service_snapshot(self._data_dir, self._strategy_dir, manager)
            snapshot["broker_session"] = manager.get_session_snapshot(broker_id)
            snapshot["action_note"] = message
            snapshot["action_ok"] = ok
            return snapshot
        # broker_status: read-only unified snapshot, no mutation.
        snapshot = _trading_service_snapshot(self._data_dir, self._strategy_dir, manager)
        snapshot["broker_session"] = manager.get_session_snapshot(broker_id)
        return snapshot

    def close(self) -> None:
        """Release the gateway handle (shared manager stays alive).

        The manager is shared with headless IPC; stopping its worker here
        would kill the desktop session's broker checks. Ownership stays with
        the process, so close only drops our reference.
        """
        self._broker_manager = None
