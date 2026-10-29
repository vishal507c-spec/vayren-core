"""BrokerManager — the single source of truth for broker lifecycle.

SYSTEM → BROKERS owns everything here; LIVE only *consumes*:

- configuration (api_key/api_secret) via the platform CredentialStore
  (Windows Credential Manager on win32; file fallback elsewhere) — the
  same service key the historical download engine reads, so one config
  serves both;
- interactive authentication: official login URL → browser opens → local
  callback captures request_token → automatic exchange → token persisted
  in the session store → read-only validation. The user never copies
  request_token or access_token; the user ALWAYS performs the venue's
  own login/2FA step (nothing here fakes that);
- startup session check: stored token → one read-only ``profile()`` →
  CONNECTED or LOGIN_REQUIRED (an expired token is LOGIN_REQUIRED, never
  a network error, never retried blindly);
- registry integration: on CONNECTED the authenticated adapter INSTANCE
  is registered as venue ``<broker>-live`` (StaticPlugin), so every
  LiveSession resolves the exact same authenticated adapter;
- health: read-only account/funds/positions/orders + market-data checks;
  never places an order.

Threading: ALL network/SDK work runs on one background worker thread
(job queue — no duplicate workers, no overlapping flows). UI updates
arrive exclusively through the observable signals. No blocking call ever
runs on the UI thread.

This module is STRATEGY-agnostic and BROKER-generic: every concrete
wiring comes from a :class:`~broker.management.BrokerSpec` supplied by
the composition root (``data.provider.factory``). Adding a broker = one
spec; nothing here changes.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.observable import Signal, WorkerThread
from broker import BrokerStatus
from broker.audit import SessionAudit
from broker.error_taxonomy import backoff_with_jitter, classify_exception, redact
from broker.management import BrokerSpec
from broker.session_state import build_snapshot as build_unified_snapshot
from broker.session_state import map_broker_status, next_action_for

CALLBACK_PORT = 9474
_LOGGER = logging.getLogger(__name__)
_SESSION_JOB = "session-check"
_LOGIN_JOB = "interactive-login"
_HEALTH_JOB = "health-check"


def _accepts_keyword(func: Any, name: str) -> bool:
    """True when ``func`` declares ``name`` (or ``**kwargs``).

    Lets the manager offer richer arguments to newer venue callables
    while legacy ones (Zerodha) keep their exact historical call —
    signature inspection only, never behavior inference.
    """
    import inspect

    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == name
        for parameter in parameters
    )


class _Worker(WorkerThread):
    """One serialized job queue for every network/SDK operation."""

    def __init__(self, on_job: Callable[[str, str], None]) -> None:
        super().__init__()
        import queue

        self._jobs: queue.Queue[tuple[str, str]] = queue.Queue()
        self._on_job = on_job
        self._stop = False

    def submit(self, kind: str, broker_id: str) -> None:
        self._jobs.put((kind, broker_id))

    def stop(self) -> None:
        self._stop = True
        self._jobs.put(("", ""))

    def run(self) -> None:
        import queue

        while not self._stop:
            try:
                kind, broker_id = self._jobs.get(timeout=1.0)
            except queue.Empty:
                continue
            if self._stop or not kind:
                return
            try:
                self._on_job(kind, broker_id)
            except Exception:  # noqa: BLE001 — a job must never kill the worker
                with contextlib.suppress(Exception):
                    self._on_job("__error__", broker_id)


class BrokerManager:
    """Owns broker configuration/auth/state. UI reads snapshots only."""

    AUTH_TIMEOUT_S: float = 30.0
    #: Bounded proof window for the START-time order-stream check.
    ORDER_STREAM_VERIFY_TIMEOUT_S: float = 8.0
    #: Low-noise SYSTEM freshness cadence (one queued check per window).
    HEALTH_REFRESH_INTERVAL_S: float = 60.0
    #: Beyond this the verified snapshot is reported stale, never LIVE-ready.
    VERIFICATION_STALE_AFTER_S: float = 180.0

    state_changed = Signal(str)  # broker_id
    message = Signal(str)  # user-safe log line (never secrets)

    def __init__(
        self,
        data_dir: str | Path,
        credential_store: Any | None = None,
        session_store_factory: Callable[[Any, str], Any] | None = None,
        browser_opener: Callable[[str], None] | None = None,
        specs: dict[str, BrokerSpec] | None = None,
    ) -> None:
        from broker.common.credentials_store import default_store

        self._data_dir = Path(data_dir)
        self._store = (
            credential_store if credential_store is not None else default_store(self._data_dir)
        )
        self._session_factory = session_store_factory
        self._browser = browser_opener
        self._specs: dict[str, BrokerSpec] = (
            dict(specs) if specs is not None else self._default_specs()
        )
        self._states: dict[str, dict[str, Any]] = {}
        for broker_id, spec in self._specs.items():
            self._states[broker_id] = self._fresh_state(spec)
        self._lock = threading.RLock()
        self._worker = _Worker(self._run_job)
        self._worker.start()
        self._stopping = False
        self._auth_started_at: dict[str, float] = {}
        #: Serializes venue I/O (worker jobs + START-time verification) so a
        #: health read can never mutate the adapter while START is proving it.
        self._io_locks: dict[str, threading.RLock] = {}
        #: Last time a freshness check was QUEUED (not executed).
        self._health_requested: dict[str, float] = {}
        # ── unified EC2 session lifecycle (one authoritative manager) ──
        # desired: operator intent (CONNECT vs DISCONNECT), persisted.
        # generation: monotonic per-broker epoch; a late auth completion from
        # an older generation must never reconnect after DISCONNECT.
        # ops: idempotency cache for duplicate CONNECT/DISCONNECT requests.
        self._audit = SessionAudit()
        self._ops: dict[str, dict[str, Any]] = {}
        # LIVE runtime facts (arming consent + whether a strategy is actually
        # running) live in the trading service, not here. The manager only
        # READS them through this provider; it never sets arming state, and
        # with no provider registered both report False (unchanged default).
        self._runtime_provider: Callable[[], dict[str, Any]] | None = None
        self._op_lock = threading.Lock()
        self._generations: dict[str, int] = dict.fromkeys(self._specs, 0)
        self._desired: dict[str, str] = dict.fromkeys(self._specs, "DISCONNECTED")
        self._unified: dict[str, dict[str, Any]] = {}
        for broker_id in self._specs:
            self._unified[broker_id] = {
                "legs": {
                    "rest": {
                        "status": "UNKNOWN",
                        "last_success": "",
                        "last_error": "",
                        "consecutive_failures": 0,
                    },
                    "market_ws": {
                        "status": "UNKNOWN",
                        "last_success": "",
                        "last_error": "",
                        "consecutive_failures": 0,
                    },
                    "order_ws": {
                        "status": "UNKNOWN",
                        "last_success": "",
                        "last_error": "",
                        "consecutive_failures": 0,
                    },
                },
                "last_verified": "",
                "last_verified_epoch": 0.0,
                "last_error": "",
                "last_error_kind": "",
                "retry_after_s": 0.0,
                "retry_until": 0.0,
                "version": 0,
                "token_reused": False,
            }
        self._load_intents()
        # Startup crash reset: reset any stale in-progress status from an interrupted process
        for state in self._states.values():
            if state.get("status") in (BrokerStatus.AUTHENTICATING, BrokerStatus.CONFIGURING):
                configured = bool(state.get("configured", False))
                state["status"] = (
                    BrokerStatus.LOGIN_REQUIRED if configured else BrokerStatus.DISCONNECTED
                )
                state["reason"] = "session reset after restart"

    def _check_auth_timeout(self, broker_id: str) -> None:
        """Watchdog: transition stuck in-progress auth attempts to ERROR after AUTH_TIMEOUT_S."""
        import time as _time

        with self._lock:
            state = self._states.get(broker_id, {})
            status = state.get("status")
            if status in (BrokerStatus.AUTHENTICATING, BrokerStatus.CONFIGURING):
                started = self._auth_started_at.get(broker_id)
                if started is not None and (_time.monotonic() - started > self.AUTH_TIMEOUT_S):
                    self._auth_started_at.pop(broker_id, None)
                    state["status"] = BrokerStatus.ERROR
                    state["reason"] = "authentication timed out after 30s — please retry"
                    self._touch_error(broker_id, "TIMEOUT", state["reason"])
                    self._audit.record(
                        broker_id=broker_id,
                        action="auth timeout",
                        result="ERROR",
                        reason="operation exceeded 30s watchdog",
                    )
                    self._bump_version(broker_id)

    @staticmethod
    def _default_specs() -> dict[str, BrokerSpec]:
        from broker.providers.fyers import fyers_management_spec
        from broker.providers.zerodha import zerodha_management_spec

        return {"zerodha": zerodha_management_spec(), "fyers": fyers_management_spec()}

    # ── public surface (UI + LIVE consume only these) ────────────

    def broker_ids(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def display_name(self, broker_id: str) -> str:
        spec = self._specs.get(broker_id)
        return spec.display_name if spec else str(broker_id)

    def state(self, broker_id: str) -> dict[str, Any]:
        self._check_auth_timeout(broker_id)
        with self._lock:
            current = dict(self._states.get(broker_id, {}))
        details = current.get("details")
        if isinstance(details, dict):
            current["details"] = dict(details)
        checks = current.get("checks")
        if isinstance(checks, dict):
            current["checks"] = dict(checks)
        return current

    def snapshot(self) -> dict[str, Any]:
        brokers = []
        for broker_id in self._specs:
            state = self.state(broker_id)
            spec = self._specs[broker_id]
            details = state.get("details")
            if not isinstance(details, dict):
                details = {}
            funds = {
                "available": details.get("funds_available"),
                "used": details.get("funds_used"),
                "total": details.get("funds_total"),
            }
            brokers.append(
                {
                    "id": broker_id,
                    "name": spec.display_name,
                    # Venue-supplied product label (adapter-owned constant).
                    # Surfaces render it instead of pinning broker literals.
                    "venue_subtitle": str(spec.extra.get("venue_subtitle", "") or ""),
                    "status": state["status"].value,
                    "reason": state["reason"],
                    "checks": dict(state["checks"]),
                    "configured": state["configured"],
                    "connected": self.is_connected(broker_id),
                    "api_key_masked": state["api_key_masked"],
                    "can_login": state["configured"]
                    and state["status"]
                    in (
                        BrokerStatus.LOGIN_REQUIRED,
                        BrokerStatus.DISCONNECTED,
                        BrokerStatus.ERROR,
                    ),
                    "can_disconnect": state["configured"]
                    and (
                        self.is_connected(broker_id)
                        or state["status"] in (BrokerStatus.CONNECTED, BrokerStatus.LIVE_READY)
                    ),
                    # Read-only UI details (safe scalars only — never secrets).
                    # Older consumers ignore unknown keys; new cards render
                    # account/funds/counts/last-sync from these.
                    "account_id": str(details.get("account_id", "") or ""),
                    "funds": funds,
                    "positions_open": details.get("positions_open"),
                    "orders_open": details.get("orders_open"),
                    "last_sync": str(state.get("last_sync", "") or ""),
                    "can_refresh": bool(state["configured"]),
                    # Per-venue redirect URL (the FYERS/Zerodha app dashboard
                    # must register exactly this). The top-level
                    # ``callback_url`` stays for backward compatibility.
                    "callback_url": spec.callback_url,
                    # Venue credential shapes (plain render data from the
                    # spec — the form renders these, values never cross).
                    "credential_schema": [dict(row) for row in spec.credential_schema],
                    # Unified authoritative session (new consumers read this;
                    # legacy keys above are unchanged for compatibility).
                    "session": self.get_session_snapshot(broker_id),
                }
            )
        # `active_broker` is the SYSTEM-selected broker, NOT "whichever venue is
        # connected". Reporting the first connected broker here made the
        # manager contradict the selection and pushed other surfaces onto
        # the wrong venue. The selection is the single source of truth.
        active_broker = ""
        try:
            from app.services.broker_selection_service import (
                BrokerSelectionService,
                app_selection_store,
            )

            selection = BrokerSelectionService(
                app_selection_store(self._data_dir)
            ).current_or_none()
            if selection is not None:
                active_broker = selection.name
        except Exception:
            active_broker = ""
        if not active_broker:
            active_broker = next((b["id"] for b in brokers if b.get("connected")), "")
        return {
            "brokers": brokers,
            "active_broker": active_broker,
            "callback_url": self._callback_url(),
        }

    def configure(self, broker_id: str, values: dict[str, str]) -> tuple[bool, str]:
        """Save required config (credentials store). Secret values never echoed.

        UI-submitted keys are translated to the venue's storage keys via
        ``spec.config_key_map`` (identity for Zerodha); required-field
        validation runs on storage keys, so every venue enforces its own
        credential shape with zero manager-side branching.
        """
        spec = self._specs.get(broker_id)
        if spec is None:
            return False, f"unknown broker: {broker_id}"
        clean = {str(k): str(v or "").strip() for k, v in values.items()}
        existing = {}
        try:
            loaded = self._store.load(spec.config_service)
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
        stored = dict(existing)
        for key, value in clean.items():
            storage_key = spec.config_key_map.get(key, key)
            if "\u2022" not in value and value:
                stored[storage_key] = value
        missing = [key for key in spec.required_config if not stored.get(key)]
        if missing:
            return False, f"missing configuration: {', '.join(missing)}"
        try:
            self._store.save(spec.config_service, stored)
        except Exception as exc:
            return False, f"secure storage refused: {type(exc).__name__}"
        state = self._states[broker_id]
        state["configured"] = True
        state["api_key_masked"] = self._mask(stored.get(spec.key_field, ""))
        if state["status"] is BrokerStatus.NOT_CONFIGURED:
            state["status"] = BrokerStatus.LOGIN_REQUIRED
            state["reason"] = "configured — session check pending"
        self._note(broker_id, "configuration saved")
        self.submit_check(broker_id)
        return True, "configuration saved"

    def start_login(self, broker_id: str) -> tuple[bool, str]:
        """Begin one interactive login round-trip (async; worker thread)."""
        spec = self._specs.get(broker_id)
        if spec is None:
            return False, f"unknown broker: {broker_id}"
        if spec.interactive_login is None:
            return False, f"interactive login not available for {spec.display_name}"
        config = self._config_values(spec)
        if not config:
            return False, "broker not configured — save the API key first"
        self._set_status(broker_id, BrokerStatus.AUTHENTICATING, "waiting for broker login…")
        self._worker.submit(_LOGIN_JOB, broker_id)
        return True, "browser opening — complete the login on the broker's site"

    def submit_check(self, broker_id: str) -> None:
        """Queue the read-only startup/session check (async)."""
        if broker_id in self._specs:
            self._worker.submit(_SESSION_JOB, broker_id)

    def refresh_funds(self, broker_id: str) -> None:
        """Run or queue an immediate health & funds check for a broker."""
        spec = self._specs.get(broker_id)
        if spec is None:
            return
        with self._lock:
            state = self._states.get(broker_id, {})
            has_adapter = state.get("adapter") is not None
        if has_adapter:
            try:
                self._job_health(spec)
            except Exception:
                self._worker.submit(_HEALTH_JOB, broker_id)
        else:
            try:
                with self._io_lock(broker_id):
                    self._job_session_check(spec)
            except Exception:
                self._worker.submit(_SESSION_JOB, broker_id)

    def disconnect_broker(
        self, broker_id: str, request_id: str = "", *, revoke: bool = False
    ) -> tuple[bool, str]:
        """Disconnect the VAYREN session safely (idempotent, intent persisted).

        - Persists DISCONNECT intent BEFORE reporting completion.
        - Bumps the generation so a late auth completion cannot reconnect.
        - Retains the encrypted token by default for later reuse; pass
          ``revoke=True`` for the explicit logout/revoke operation.
        - Never closes exchange positions; open counts are kept in details.
        Duplicate ``request_id`` values replay the cached result.
        """
        spec = self._specs.get(broker_id)
        if spec is None:
            return False, f"unknown broker: {broker_id}"
        request_id = str(request_id or "").strip()
        if request_id:
            with self._op_lock:
                cached = self._ops.get(f"disconnect:{broker_id}:{request_id}")
            if cached is not None:
                return bool(cached["ok"]), str(cached["message"])
        with self._lock:
            generation = int(self._generations.get(broker_id, 0)) + 1
            self._generations[broker_id] = generation
            self._desired[broker_id] = "DISCONNECTED"
        self._save_intents()
        self._audit.record(
            request_id=request_id,
            broker_id=broker_id,
            action="DISCONNECT requested",
            result="accepted",
            reason=f"generation {generation}",
        )
        # Stop reconnect-relevant resources; keep token unless revoked.
        with contextlib.suppress(Exception):
            state = self._states.get(broker_id, {})
            adapter = state.get("adapter")
            if adapter is not None and hasattr(adapter, "disconnect"):
                adapter.disconnect()
            md = state.get("market_data")
            if md is not None and hasattr(md, "disconnect"):
                md.disconnect()
        if revoke:
            with contextlib.suppress(Exception):
                self._session_store(spec).clear()
        with contextlib.suppress(Exception):
            if spec.venue_unregister is not None:
                spec.venue_unregister()
        # Keep last-verified open counts for the disconnect explanation.
        prior_details: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            prior = self._states.get(broker_id, {}).get("details")
            if isinstance(prior, dict):
                prior_details = dict(prior)
        self._set_status(broker_id, BrokerStatus.DISCONNECTED, "disconnected by operator")
        with contextlib.suppress(Exception):
            cleared = self._states.get(broker_id)
            if isinstance(cleared, dict):
                details = self._empty_details()
                # Preserve open counts so the UI can explain remaining
                # broker-side positions/orders per risk policy.
                for key in ("positions_open", "orders_open", "account_id"):
                    if prior_details.get(key) is not None:
                        details[key] = prior_details.get(key)
                cleared["details"] = details
                cleared["adapter"] = None
                cleared["market_data"] = None
        self._bump_version(broker_id)
        self._touch_error(broker_id, "OPERATOR", "disconnected by operator")
        self._audit.record(
            request_id=request_id,
            broker_id=broker_id,
            action="state changed",
            result="DISCONNECTED",
            reason="operator disconnect",
        )
        self._note(broker_id, "disconnected (configuration kept)")
        if request_id:
            with self._op_lock:
                self._ops[f"disconnect:{broker_id}:{request_id}"] = {
                    "ok": True,
                    "message": "disconnected",
                }
                while len(self._ops) > 512:
                    self._ops.pop(next(iter(self._ops)))
        return True, "disconnected"

    def revoke_session(self, broker_id: str, request_id: str = "") -> tuple[bool, str]:
        """Explicit logout/revoke: disconnect AND erase the stored token."""
        ok, message = self.disconnect_broker(broker_id, request_id=request_id, revoke=True)
        if ok:
            self._audit.record(
                request_id=str(request_id or ""),
                broker_id=broker_id,
                action="SESSION revoked",
                result="ok",
                reason="explicit logout",
            )
            return True, "session revoked — login required for next connect"
        return ok, message

    def connect_broker(
        self,
        broker_id: str,
        credentials: dict[str, str] | None = None,
        request_id: str = "",
    ) -> tuple[bool, str, dict[str, Any]]:
        """One-click smart connect (idempotent, generation-guarded).

        Step A: if a connect is already in progress, return its status.
        Step B: a valid saved token is reused (verified, not just present).
        Step C: expired tokens move to LOGIN_REQUIRED via the supported flow.
        Step D: CONNECTED only after verification (account identity + funds).
        """
        spec = self._specs.get(broker_id)
        if spec is None:
            return False, f"unknown broker: {broker_id}", self.get_session_snapshot(broker_id)
        request_id = str(request_id or "").strip()
        if request_id:
            with self._op_lock:
                cached = self._ops.get(f"connect:{broker_id}:{request_id}")
            if cached is not None:
                return (
                    bool(cached["ok"]),
                    str(cached["message"]),
                    self.get_session_snapshot(broker_id),
                )
        self._check_auth_timeout(broker_id)
        with self._lock:
            current_status = self._states.get(broker_id, {}).get("status")
        if (
            current_status in (BrokerStatus.AUTHENTICATING, BrokerStatus.CONFIGURING)
            and not credentials
        ):
            self._audit.record(
                request_id=request_id,
                broker_id=broker_id,
                action="CONNECT requested",
                result="in-progress",
                reason="connect already running",
            )
            return True, "connection already in progress", self.get_session_snapshot(broker_id)
        attempt_id = secrets.token_hex(4)
        logging.getLogger(__name__).info(f"[Auth {attempt_id}] Connect initiated for {broker_id}")
        # Rate-limited: refuse a retry storm while backoff is armed.
        if self._retry_armed(broker_id):
            snap = self.get_session_snapshot(broker_id)
            return False, "rate limited — retrying automatically", snap
        credentials = credentials or {}
        clean = {str(k): str(v or "").strip() for k, v in credentials.items()}
        # Merge with stored config (sentinel •••• means "keep saved value").
        try:
            stored_cfg = self._store.load(spec.config_service)
            merged = dict(stored_cfg) if isinstance(stored_cfg, dict) else {}
        except Exception:
            merged = {}
        for key, value in clean.items():
            if value and "•" not in value:
                merged[str(key)] = value
        if merged:
            ok_cfg, msg_cfg = self.configure(broker_id, merged)
            if not ok_cfg:
                self._audit.record(
                    request_id=request_id,
                    broker_id=broker_id,
                    action="CONNECT requested",
                    result="refused",
                    reason=msg_cfg,
                )
                return False, msg_cfg, self.get_session_snapshot(broker_id)
        with self._lock:
            self._generations[broker_id] = int(self._generations.get(broker_id, 0)) + 1
            generation = int(self._generations[broker_id])
            self._desired[broker_id] = "CONNECTED"
        self._save_intents()
        self._audit.record(
            request_id=request_id,
            broker_id=broker_id,
            action="CONNECT requested",
            result="accepted",
            reason=f"generation {generation}",
        )
        self._set_status(broker_id, BrokerStatus.AUTHENTICATING, "checking saved session…")
        self._bump_version(broker_id)
        self._worker.submit(_SESSION_JOB, broker_id)
        if request_id:
            with self._op_lock:
                self._ops[f"connect:{broker_id}:{request_id}"] = {
                    "ok": True,
                    "message": "connection started",
                }
                while len(self._ops) > 512:
                    self._ops.pop(next(iter(self._ops)))
        return True, "connection started", self.get_session_snapshot(broker_id)

    def remove(self, broker_id: str) -> tuple[bool, str]:
        """Erase config + session + venue (explicit user action only)."""
        spec = self._specs.get(broker_id)
        if spec is None:
            return False, f"unknown broker: {broker_id}"
        with contextlib.suppress(Exception):
            self._store.delete(spec.config_service)
        with contextlib.suppress(Exception):
            self._session_store(spec).clear()
        with contextlib.suppress(Exception):
            if spec.venue_unregister is not None:
                spec.venue_unregister()
        self._states[broker_id] = self._fresh_state(spec)
        with self._lock:
            self._desired[broker_id] = "DISCONNECTED"
            self._generations[broker_id] = int(self._generations.get(broker_id, 0)) + 1
        self._save_intents()
        self._note(broker_id, "removed")
        self.state_changed.emit(broker_id)
        return True, "broker removed"

    # ── unified authoritative session snapshot ───────────────────

    def set_runtime_provider(self, provider: Callable[[], dict[str, Any]] | None) -> None:
        """Register a read-only LIVE runtime state provider (consent + session).

        The trading service owns consent and the session lifecycle; the broker
        manager must not infer either. Registration is optional: with no
        provider every session still reports ``live_armed``/``strategy_running``
        as False, exactly as before.
        """
        self._runtime_provider = provider

    def _runtime_state(self) -> dict[str, Any]:
        """Runtime facts from the provider, never from this module's state."""
        if self._runtime_provider is None:
            return {}
        try:
            facts = self._runtime_provider()
        except Exception:  # noqa: BLE001 - reporting must never raise
            return {}
        return dict(facts) if isinstance(facts, dict) else {}

    def _io_lock(self, broker_id: str) -> threading.RLock:
        """Per-broker lock shared by worker jobs and START-time verification."""
        with self._lock:
            lock = self._io_locks.get(broker_id)
            if lock is None:
                lock = threading.RLock()
                self._io_locks[broker_id] = lock
            return lock

    def _queue_health_refresh(self, broker_id: str, *, force: bool = False) -> bool:
        """Queue at most one freshness check per window.

        This is deliberately NOT a poller: no request is made until a
        snapshot is actually read, and repeated snapshot reads cannot enqueue
        duplicates. ``force=True`` is used once after a successful activation.
        """
        if self._stopping or self._desired_state(broker_id) != "CONNECTED":
            return False
        if not force and self._retry_armed(broker_id):
            return False
        with self._lock:
            state = self._states.get(broker_id, {})
            adapter = state.get("adapter")
            status = state.get("status")
            epoch = float(self._unified.get(broker_id, {}).get("last_verified_epoch", 0.0) or 0.0)
        if adapter is None or (not force and epoch <= 0.0):
            return False
        # A terminal auth/disconnect verdict needs an operator CONNECT (or a
        # restart), not a once-per-window venue retry. Only active legs keep
        # refreshing; this is what makes the expired-token path quiet.
        if not force and status not in (
            BrokerStatus.CONNECTED,
            BrokerStatus.LIVE_READY,
            BrokerStatus.MARKET_DATA_NOT_READY,
            BrokerStatus.EXECUTION_NOT_READY,
        ):
            return False
        now = time.monotonic()
        with self._lock:
            last = float(self._health_requested.get(broker_id, 0.0) or 0.0)
            if not force and now - last < self.HEALTH_REFRESH_INTERVAL_S:
                return False
            self._health_requested[broker_id] = now
        self._worker.submit(_HEALTH_JOB, broker_id)
        return True

    def _sync_order_stream_leg(self, broker_id: str) -> str:
        """Cheap local order-WS check (no REST) for the visible snapshot.

        The adapter exposes delivery/transport state directly, so a dropped
        order socket should degrade SYSTEM on the next snapshot instead of
        waiting for the slower REST health cycle. Returns the failure reason
        (empty when healthy/unknown).
        """
        with self._lock:
            state = self._states.get(broker_id, {})
            adapter = state.get("adapter")
        if adapter is None:
            return ""
        probe = getattr(adapter, "order_stream_health", None)
        if not inspect.ismethod(probe):
            return ""
        try:
            result = probe()
            ws_ok = bool(result[0])
            ws_reason = str(result[1] if len(result) > 1 else "")
        except Exception as exc:  # noqa: BLE001 - reporting must never raise
            ws_ok, ws_reason = False, f"unreadable ({type(exc).__name__})"
        new_status = "READY" if ws_ok else f"FAILED: {ws_reason}"
        with self._lock:
            uni = self._unified.setdefault(broker_id, {})
            row = uni.setdefault("legs", {}).setdefault("order_ws", {})
            changed = str(row.get("status", "")) != new_status
        if changed:
            self._set_leg(
                broker_id,
                "order_ws",
                "READY" if ws_ok else "FAILED",
                "" if ws_ok else ws_reason,
                success=ws_ok,
            )
        return "" if ws_ok else ws_reason

    def get_session_snapshot(self, broker_id: str) -> dict[str, Any]:
        """One authoritative backend snapshot for SYSTEM/LIVE/dashboard/health."""
        self._check_auth_timeout(broker_id)
        self._queue_health_refresh(broker_id)
        ws_reason = self._sync_order_stream_leg(broker_id)
        with self._lock:
            state = dict(self._states.get(broker_id, {}))
            uni = self._unified.get(broker_id, {})
            legs = {k: dict(v) for k, v in dict(uni.get("legs", {})).items()}
            desired = str(self._desired.get(broker_id, "DISCONNECTED"))
            version = int(uni.get("version", 0) or 0)
            last_verified = str(uni.get("last_verified", "") or "")
            last_verified_epoch = float(uni.get("last_verified_epoch", 0.0) or 0.0)
            last_error = str(uni.get("last_error", "") or "")
            last_error_kind = str(uni.get("last_error_kind", "") or "")
            retry_after_s = float(uni.get("retry_after_s", 0.0) or 0.0)
            token_reused = bool(uni.get("token_reused", False))
        runtime = self._runtime_state()
        raw_details = state.get("details")
        details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
        raw_status = state.get("status")
        if isinstance(raw_status, BrokerStatus):
            status_raw = raw_status.value
        else:
            status_raw = str(raw_status or "UNKNOWN")
        configured = bool(state.get("configured", False))
        unified_state = map_broker_status(status_raw, configured)
        # Partial-leg failures degrade; rate-limit backoff wins over CONNECTED.
        if unified_state == "CONNECTED" and any(
            legs.get(leg, {}).get("status") not in ("READY", "CONNECTED", "UNKNOWN", "")
            and legs.get(leg, {}).get("status") not in (None,)
            for leg in ("market_ws", "order_ws")
            if str(legs.get(leg, {}).get("status", "")).startswith("FAILED")
        ):
            unified_state = "DEGRADED"
        if unified_state == "CONNECTED" and ws_reason:
            unified_state = "DEGRADED"
        if self._retry_armed(broker_id):
            unified_state = "RATE_LIMITED"
        if unified_state == "CONNECTED" and not self._adapter_verified(broker_id):
            if not self._retry_armed(broker_id):
                self.submit_check(broker_id)
            unified_state = "CHECKING"
        verified_age: float | None = None
        stale = False
        if last_verified_epoch > 0.0 and status_raw in (
            "CONNECTED",
            "LIVE_READY",
            "MARKET_DATA_NOT_READY",
            "EXECUTION_NOT_READY",
        ):
            verified_age = max(0.0, time.monotonic() - last_verified_epoch)
            stale = verified_age > self.VERIFICATION_STALE_AFTER_S
        reason_text = str(state.get("reason", "") or "")
        if stale and unified_state == "CONNECTED":
            # An old "all checks passed" must never be presented as current.
            unified_state = "CHECKING"
            reason_text = f"session verification stale ({int(verified_age or 0)}s) — refreshing"
        funds = {
            "available": details.get("funds_available"),
            "used": details.get("funds_used"),
            "total": details.get("funds_total"),
        }
        leg_list = [
            {
                "name": "rest",
                "status": str(legs.get("rest", {}).get("status", "UNKNOWN")),
                "last_success": str(legs.get("rest", {}).get("last_success", "") or ""),
                "last_error": redact(str(legs.get("rest", {}).get("last_error", "") or "")),
                "consecutive_failures": int(
                    legs.get("rest", {}).get("consecutive_failures", 0) or 0
                ),
            },
            {
                "name": "market_ws",
                "status": str(legs.get("market_ws", {}).get("status", "UNKNOWN")),
                "last_success": str(legs.get("market_ws", {}).get("last_success", "") or ""),
                "last_error": redact(str(legs.get("market_ws", {}).get("last_error", "") or "")),
                "consecutive_failures": int(
                    legs.get("market_ws", {}).get("consecutive_failures", 0) or 0
                ),
            },
            {
                "name": "order_ws",
                "status": str(legs.get("order_ws", {}).get("status", "UNKNOWN")),
                "last_success": str(legs.get("order_ws", {}).get("last_success", "") or ""),
                "last_error": redact(str(legs.get("order_ws", {}).get("last_error", "") or "")),
                "consecutive_failures": int(
                    legs.get("order_ws", {}).get("consecutive_failures", 0) or 0
                ),
            },
        ]
        snap = build_unified_snapshot(
            broker_id=broker_id,
            desired=desired,
            state=unified_state,
            auth_valid=unified_state in ("CONNECTED", "DEGRADED"),
            account_id=str(details.get("account_id", "") or ""),
            funds=funds,
            legs=leg_list,
            last_verified=last_verified,
            last_error=redact(last_error),
            last_error_kind=str(last_error_kind or ""),
            retry_after_s=retry_after_s,
            version=version,
            live_armed=bool(runtime.get("live_armed", False)),
            strategy_running=bool(runtime.get("strategy_running", False)),
            reason=redact(reason_text),
        )
        snap["configured"] = configured
        snap["status_raw"] = status_raw
        snap["token_reused"] = token_reused
        snap["verified_age_s"] = round(verified_age, 1) if verified_age is not None else None
        snap["verification_stale"] = bool(stale)
        snap["health_refresh_interval_s"] = float(self.HEALTH_REFRESH_INTERVAL_S)
        snap["can_connect"] = bool(
            state.get("configured")
            and raw_status
            in (BrokerStatus.LOGIN_REQUIRED, BrokerStatus.DISCONNECTED, BrokerStatus.ERROR)
            or (not configured)
        )
        snap["can_disconnect"] = self.is_connected(broker_id) or raw_status in (
            BrokerStatus.CONNECTED,
            BrokerStatus.LIVE_READY,
        )
        snap["next_action"] = next_action_for(unified_state)
        return snap

    def verify_live_session(
        self, broker_id: str, timeout_s: float | None = None
    ) -> tuple[bool, str]:
        """Fresh, bounded START-time proof of the broker session + order stream.

        This is the authority LIVE START consults instead of a cached
        ``CONNECTED`` snapshot:

        * performs one forced read-only profile check (``adapter.connect``),
        * proves the order WebSocket is connected/usable within ``timeout_s``
          (the adapter owns the bounded wait, so no reconnect storm),
        * refreshes account/funds scalars for the visible snapshot.

        All venue I/O is serialized with the manager worker; no order is
        placed and no token/credential is logged. A failure is written to the
        authoritative state immediately so SYSTEM and LIVE cannot disagree.
        """
        if broker_id not in self._states and broker_id not in self._specs:
            return False, f"unknown broker: {broker_id}"
        if self._desired_state(broker_id) != "CONNECTED":
            return False, "broker is not connected — connect in SYSTEM → BROKERS"
        timeout = (
            float(self.ORDER_STREAM_VERIFY_TIMEOUT_S)
            if timeout_s is None
            else max(0.0, float(timeout_s))
        )
        with self._io_lock(broker_id):
            # Re-check under the I/O lock: an operator disconnect could have
            # completed between the fast-path check above and this point.
            if self._desired_state(broker_id) != "CONNECTED":
                return False, "broker is not connected — connect in SYSTEM → BROKERS"
            with self._lock:
                state = self._states.get(broker_id, {})
                adapter = state.get("adapter")
            if adapter is None:
                return False, "broker adapter is not connected — connect in SYSTEM → BROKERS"
            connect = getattr(adapter, "connect", None)
            if not callable(connect):
                return False, "broker adapter cannot verify a session"
            try:
                connect()
            except Exception as exc:  # noqa: BLE001 - classified below
                return self._record_probe_failure(broker_id, adapter, exc)
            # The operator may have disconnected while the profile check was
            # in flight. Re-check before treating the session as verified and
            # close anything connect() just started.
            if self._desired_state(broker_id) != "CONNECTED":
                with contextlib.suppress(Exception):
                    adapter.disconnect()
                return False, "broker is not connected — connect in SYSTEM → BROKERS"

            ws_proven = False
            verifier = getattr(adapter, "verify_order_stream", None)
            if inspect.ismethod(verifier):
                try:
                    ws_result = verifier(timeout_s=timeout)
                    if not isinstance(ws_result, (tuple, list)) or len(ws_result) < 2:
                        raise RuntimeError("order stream health unreadable")
                    ws_ok, ws_reason = bool(ws_result[0]), str(ws_result[1])
                except Exception as exc:  # noqa: BLE001 - fail closed below
                    return self._record_order_stream_failure(broker_id, str(exc))
                if not ws_ok:
                    return self._record_order_stream_failure(broker_id, ws_reason)
                ws_proven = True
            else:
                # Legacy/other venues may expose a one-shot leg health only.
                leg_probe = getattr(adapter, "order_stream_health", None)
                if inspect.ismethod(leg_probe):
                    try:
                        leg_result = leg_probe()
                        if not isinstance(leg_result, (tuple, list)) or len(leg_result) < 2:
                            raise RuntimeError("order stream health unreadable")
                        leg_ok, leg_reason = bool(leg_result[0]), str(leg_result[1])
                    except Exception as exc:  # noqa: BLE001 - fail closed below
                        return self._record_order_stream_failure(broker_id, str(exc))
                    if not leg_ok:
                        return self._record_order_stream_failure(broker_id, leg_reason)
                    ws_proven = True

            details = self._empty_details()
            checks: dict[str, str] = {"connection": "READY"}
            try:
                account = adapter.account()
            except Exception as exc:  # noqa: BLE001 - classified below
                return self._record_probe_failure(broker_id, adapter, exc)
            if not isinstance(account, dict):
                return self._record_probe_failure(
                    broker_id, adapter, RuntimeError("account response unreadable")
                )
            account_id = self._account_id_of(account)
            if not account_id:
                return self._record_probe_failure(
                    broker_id, adapter, RuntimeError("account identity missing")
                )
            checks["account"] = "READY"
            details["account_id"] = account_id
            try:
                funds = adapter.funds()
                checks["funds"] = "READY"
                if isinstance(funds, dict):
                    details["funds_available"] = self._safe_float(funds.get("available"))
                    details["funds_used"] = self._safe_float(funds.get("used"))
                    total = funds.get("total", funds.get("equity"))
                    details["funds_total"] = self._safe_float(total)
            except Exception as exc:  # noqa: BLE001 - capital gate blocks later
                checks["funds"] = f"FAILED: {type(exc).__name__}"

            with self._lock:
                live = self._states.get(broker_id)
                if live is None:
                    return False, "broker state unavailable"
                merged = dict(live.get("checks") or {})
                merged.update(checks)
                failed = [k for k, v in merged.items() if str(v).startswith("FAILED")]
                if failed:
                    if "orders" in failed or "positions" in failed:
                        live["status"] = BrokerStatus.EXECUTION_NOT_READY
                    else:
                        live["status"] = BrokerStatus.MARKET_DATA_NOT_READY
                    live["reason"] = (
                        "session verified; degraded leg(s): "
                        + ", ".join(failed)
                        + " — "
                        + redact(str(merged.get(failed[0], "")))
                    )
                else:
                    live["status"] = BrokerStatus.CONNECTED
                    live["reason"] = "session verified (fresh START check)"
                live["checks"] = merged
                live["details"] = details
                live["last_sync"] = self._now_stamp()
            self._clear_retry(broker_id)
            self._set_leg(broker_id, "rest", "READY", success=True)
            if ws_proven:
                self._set_leg(broker_id, "order_ws", "READY", success=True)
            self._touch_verified(broker_id)
            self._bump_version(broker_id)
            self.state_changed.emit(broker_id)
            return True, "session verified"

    def _record_order_stream_failure(self, broker_id: str, reason: str) -> tuple[bool, str]:
        """Fail LIVE START closed while keeping the verified REST session."""
        safe = redact(str(reason or "order stream not connected"))
        with self._lock:
            live = self._states.get(broker_id)
            if live is not None:
                live["status"] = BrokerStatus.MARKET_DATA_NOT_READY
                live["reason"] = f"order stream not verified: {safe}"
        self._set_leg(broker_id, "order_ws", "FAILED", safe)
        self._touch_error(broker_id, "ORDER_STREAM", safe)
        self._bump_version(broker_id)
        self.state_changed.emit(broker_id)
        return False, f"order stream not verified: {safe}"

    def _record_probe_failure(
        self, broker_id: str, adapter: Any, exc: BaseException
    ) -> tuple[bool, str]:
        """Write an authoritative failed verification (classify, never guess)."""
        kind, safe = classify_exception(exc)
        safe = redact(safe or str(exc) or kind)
        with self._lock:
            live = self._states.get(broker_id)
            if live is not None:
                if kind in ("AUTH_EXPIRED", "AUTH_INVALID"):
                    live["status"] = BrokerStatus.LOGIN_REQUIRED
                    live["reason"] = f"session expired — {safe}; click CONNECT in SYSTEM → BROKERS"
                elif kind == "RATE_LIMITED":
                    live["status"] = BrokerStatus.ERROR
                    live["reason"] = "rate limited — retry in SYSTEM → BROKERS"
                elif kind in ("NETWORK", "TIMEOUT"):
                    live["status"] = BrokerStatus.DISCONNECTED
                    live["reason"] = f"venue unreachable: {safe}"
                else:
                    live["status"] = BrokerStatus.ERROR
                    live["reason"] = f"session verification failed: {safe}"
        if kind == "RATE_LIMITED":
            delay = backoff_with_jitter(1, base_s=5.0)
            self._arm_retry(broker_id, delay)
        if kind in ("AUTH_EXPIRED", "AUTH_INVALID"):
            with contextlib.suppress(Exception):
                adapter._connected = False
        if kind in ("AUTH_EXPIRED", "AUTH_INVALID", "NETWORK", "TIMEOUT"):
            self._set_leg(broker_id, "rest", "FAILED", safe)
        self._touch_error(broker_id, kind, safe)
        self._bump_version(broker_id)
        self.state_changed.emit(broker_id)
        return False, f"session could not be verified: {safe}"

    def audit_tail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent secret-free session operations (diagnostics only)."""
        return self._audit.tail(limit)

    def is_connected(self, broker_id: str) -> bool:
        with self._lock:
            state = self._states.get(broker_id, {})
            status = state.get("status")
            adapter = state.get("adapter")
            checks = state.get("checks", {})
        if status not in (
            BrokerStatus.CONNECTED,
            BrokerStatus.LIVE_READY,
            BrokerStatus.MARKET_DATA_NOT_READY,
            BrokerStatus.EXECUTION_NOT_READY,
        ):
            return False
        # Authoritative session verification:
        # Never report connected based solely on saved credentials or cached status.
        if adapter is None:
            return False
        is_conn = getattr(adapter, "connected", None)
        if is_conn is None:
            is_conn = getattr(adapter, "_connected", False)
        if not is_conn:
            return False
        conn_check = checks.get("connection")
        return conn_check is None or conn_check == "READY"

    def stop_worker(self) -> None:
        if not self._stopping:
            self._stopping = True
            self._worker.stop()
            stopped = True
            with contextlib.suppress(Exception):
                stopped = self._worker.wait(3000)
            if not stopped:
                logging.getLogger(__name__).warning(
                    "broker worker did not stop in 3s; continuing shutdown"
                )

    # ── worker jobs (background thread ONLY) ─────────────────────

    def _run_job(self, kind: str, broker_id: str) -> None:
        spec = self._specs.get(broker_id)
        if spec is None:
            return
        if kind in (_SESSION_JOB, _LOGIN_JOB, _HEALTH_JOB):
            # Serialize with a START-time verification on the calling thread:
            # both mutate/read the same adapter and must never overlap.
            with self._io_lock(broker_id):
                if kind == _SESSION_JOB:
                    self._job_session_check(spec)
                elif kind == _LOGIN_JOB:
                    self._job_login(spec)
                else:
                    self._job_health(spec)
            return
        if kind == "__error__":
            with self._lock:
                current = self._states.get(broker_id, {}).get("status")
            # Never downgrade a specific verdict (LOGIN_REQUIRED/DISCONNECTED/
            # NOT_CONFIGURED) into a generic ERROR — the specific reason is
            # what the UI must show.
            if current in (
                BrokerStatus.LOGIN_REQUIRED,
                BrokerStatus.DISCONNECTED,
                BrokerStatus.NOT_CONFIGURED,
            ):
                return
            self._set_status(broker_id, BrokerStatus.ERROR, "background operation failed")

    def _job_session_check(self, spec: BrokerSpec) -> None:
        generation = self._generation(spec.broker_id)
        if self._desired_state(spec.broker_id) != "CONNECTED":
            return
        if self._retry_armed(spec.broker_id):
            return
        config = self._config_values(spec)
        if not config:
            self._set_status(spec.broker_id, BrokerStatus.NOT_CONFIGURED, "no configuration saved")
            self._bump_version(spec.broker_id)
            return
        stored = self._session_store(spec).load_token()
        if stored is None:
            if self._try_auto_auth(spec, config, generation=generation):
                return
            self._set_status(spec.broker_id, BrokerStatus.LOGIN_REQUIRED, "no active session")
            self._touch_error(spec.broker_id, "AUTH", "no active session")
            self._audit.record(
                broker_id=spec.broker_id,
                action="authorization required",
                result="LOGIN_REQUIRED",
                reason="no saved token",
            )
            self._bump_version(spec.broker_id)
            return
        flow = spec.build_flow() if spec.build_flow is not None else None
        if flow is None:
            self._set_status(spec.broker_id, BrokerStatus.ERROR, "auth flow not wired")
            self._bump_version(spec.broker_id)
            return
        token = stored.get("access_token", "") if isinstance(stored, dict) else ""
        if not token:
            self._set_status(
                spec.broker_id, BrokerStatus.LOGIN_REQUIRED, "saved session has no token"
            )
            self._bump_version(spec.broker_id)
            return
        self._set_status(spec.broker_id, BrokerStatus.CONFIGURING, "checking saved session…")
        ok, reason = flow.validate(config[spec.key_field], token)
        if not ok:
            kind, safe = self._classify_validate_reason(reason)
            if kind in ("NETWORK", "TIMEOUT"):
                text = (
                    safe
                    if safe.lower().startswith("venue unreachable")
                    else (f"venue unreachable: {safe}")
                )
                self._set_status(spec.broker_id, BrokerStatus.DISCONNECTED, text)
                self._touch_error(spec.broker_id, kind, text)
                self._set_leg(spec.broker_id, "rest", "FAILED", text)
            elif kind == "RATE_LIMITED":
                delay = backoff_with_jitter(1, base_s=5.0)
                self._arm_retry(spec.broker_id, delay)
                self._set_status(
                    spec.broker_id, BrokerStatus.ERROR, f"rate limited — retry in {delay:.0f}s"
                )
                self._touch_error(spec.broker_id, kind, safe)
            elif self._try_auto_auth(spec, config, generation=generation):
                return
            else:
                self._set_status(
                    spec.broker_id, BrokerStatus.LOGIN_REQUIRED, f"session expired — {safe}"
                )
                self._touch_error(spec.broker_id, "AUTH_EXPIRED", safe)
                self._audit.record(
                    broker_id=spec.broker_id,
                    action="verification failed",
                    result="LOGIN_REQUIRED",
                    reason=safe,
                )
            self._bump_version(spec.broker_id)
            return
        # Valid saved token: reuse, no new login, verify expected account.
        self._activate(spec, config, token, reason, generation=generation, token_reused=True)

    @staticmethod
    def _classify_validate_reason(reason: str) -> tuple[str, str]:
        """Classify a validate() reason without treating every 400 as expiry."""
        from broker.error_taxonomy import classify_http_error

        text = redact(str(reason or ""))
        lowered = text.lower()
        if "429" in lowered or "rate limit" in lowered:
            return "RATE_LIMITED", text
        if any(
            k in lowered
            for k in (
                "token is invalid",
                "invalid token",
                "token expired",
                "expired",
                "session expired",
            )
        ):
            return "AUTH_EXPIRED", text
        if "http 400" in lowered:
            return classify_http_error(400, text)
        if "http 401" in lowered or "http 403" in lowered:
            code = 401 if "401" in lowered else 403
            return classify_http_error(code, text)
        if "mismatch" in lowered:
            return "AUTH_INVALID", text
        if any(k in lowered for k in ("invalid", "unauthorized", "session")):
            return "AUTH_EXPIRED", text
        if any(
            k in lowered
            for k in ("unreachable", "timeout", "timed out", "oserror", "connection refused")
        ):
            kind = "TIMEOUT" if "timeout" in lowered or "timed out" in lowered else "NETWORK"
            return kind, text
        return "UNKNOWN", text

    def _try_auto_auth(
        self, spec: BrokerSpec, config: dict[str, str], generation: int | None = None
    ) -> bool:
        """Run the venue's automatic authentication when wired.

        Returns True when the hook handled the outcome (the status is
        already set: CONNECTED via ``_activate`` or LOGIN_REQUIRED with
        the exact reason). ``None`` hook (Zerodha default) returns False
        so the caller keeps the legacy LOGIN_REQUIRED path — Zerodha
        behavior is unchanged.
        """
        auto_auth = getattr(spec, "auto_authenticate", None)
        if auto_auth is None:
            return False
        if generation is None:
            generation = self._generation(spec.broker_id)
        self._set_status(
            spec.broker_id, BrokerStatus.AUTHENTICATING, "authenticating automatically…"
        )
        self._bump_version(spec.broker_id)
        try:
            ok, message = auto_auth(config, self._session_store(spec))
        except Exception as exc:
            kind, safe = classify_exception(exc)
            if kind == "RATE_LIMITED":
                delay = backoff_with_jitter(1, base_s=5.0)
                self._arm_retry(spec.broker_id, delay)
                self._set_status(
                    spec.broker_id, BrokerStatus.ERROR, f"rate limited — retry in {delay:.0f}s"
                )
            else:
                self._set_status(
                    spec.broker_id,
                    BrokerStatus.LOGIN_REQUIRED,
                    f"automatic authentication failed: {redact(safe)}",
                )
            self._touch_error(spec.broker_id, kind, safe)
            self._bump_version(spec.broker_id)
            return True
        self._note(spec.broker_id, message)
        if not ok:
            kind, safe = self._classify_validate_reason(message)
            safe_text = redact(safe)
            if "mismatch" in safe_text.lower() and "redirect" in safe_text.lower():
                safe_text = (
                    "Redirect URI mismatch: Ensure the Redirect URI in VAYREN matches "
                    "the Redirect URL in your FYERS Developer App (myapi.fyers.in). "
                    f"Detail: {safe_text}"
                )
            self._set_status(spec.broker_id, BrokerStatus.LOGIN_REQUIRED, safe_text)
            self._touch_error(spec.broker_id, kind, safe_text)
            self._audit.record(
                broker_id=spec.broker_id,
                action="authorization required",
                result="LOGIN_REQUIRED",
                reason=safe_text,
            )
            self._bump_version(spec.broker_id)
            return True
        stored = self._session_store(spec).load_token() or {}
        token = str(stored.get("access_token", "") or "")
        if not token:
            self._set_status(
                spec.broker_id,
                BrokerStatus.LOGIN_REQUIRED,
                "automatic authentication did not store a session",
            )
            self._bump_version(spec.broker_id)
            return True
        self._activate(spec, config, token, message, generation=generation)
        return True

    def _job_login(self, spec: BrokerSpec) -> None:
        config = self._config_values(spec)
        if not config:
            self._set_status(spec.broker_id, BrokerStatus.NOT_CONFIGURED, "no configuration saved")
            return
        assert spec.interactive_login is not None and spec.build_flow is not None
        if not spec.secret_field:
            self._set_status(
                spec.broker_id, BrokerStatus.ERROR, "venue has no secret field to log in with"
            )
            return
        session_store = self._session_store(spec)
        login_kwargs: dict[str, Any] = {
            "port": int(spec.callback_port or CALLBACK_PORT),
            "open_browser": self._browser,
        }
        if spec.redirect_uri_field:
            login_kwargs["redirect_uri"] = (
                config.get(spec.redirect_uri_field, "") or spec.callback_url
            )
        if _accepts_keyword(spec.interactive_login, "config"):
            # Selenium-capable venues (FYERS) automate the full credential
            # set; legacy callables (Zerodha) keep their exact old call.
            login_kwargs["config"] = dict(config)
        ok, message = spec.interactive_login(
            spec.build_flow(),
            config[spec.key_field],
            config[spec.secret_field],
            session_store,
            **login_kwargs,
        )
        self._note(spec.broker_id, message)
        if not ok:
            self._set_status(spec.broker_id, BrokerStatus.LOGIN_REQUIRED, message)
            return
        stored = session_store.load_token() or {}
        self._activate(spec, config, stored.get("access_token", ""), message)

    def _job_health(self, spec: BrokerSpec) -> None:
        """Serialize the read-only health probe against START verification."""
        with self._io_lock(spec.broker_id):
            self._run_health_checks(spec)

    def _run_health_checks(self, spec: BrokerSpec) -> None:
        """Read-only checks on the active adapter; never places an order.

        Return values are reduced to safe UI scalars (account id, fund
        floats, open counts) the moment they are read — the full adapter
        payloads never enter manager state, snapshots, logs or signals.
        Each leg (REST / market WS / order WS) is tracked independently so a
        partial failure reports DEGRADED instead of CONNECTED.
        """
        if self._desired_state(spec.broker_id) != "CONNECTED":
            return
        if self._retry_armed(spec.broker_id):
            return
        state = self._states.get(spec.broker_id, {})
        adapter = state.get("adapter")
        if adapter is None:
            return
        details = self._empty_details()
        is_auth_error = False
        auth_error_msg = ""
        is_rate_limited = False
        is_session_rate_limited = False
        rate_msg = ""
        rest_ok = True

        def _note_failure(text: str, *, session_level: bool = False) -> tuple[bool, bool]:
            """Record a failed read.

            ``session_level`` marks the checks that PROVE the session
            (``health``'s profile call, ``account``'s profile call). Only
            those may invalidate the session. A sub-read that fails while
            the session has just proved itself — positions, order book,
            funds — marks its check and leg degraded but must NOT report
            LOGIN_REQUIRED: the token is demonstrably valid (the venue just
            served the profile), so downgrading the whole session on a
            single flaky read is exactly the "reads work, state says
            expired" bug this guard removes.
            """
            nonlocal is_auth_error, auth_error_msg
            nonlocal is_rate_limited, is_session_rate_limited, rate_msg
            kind, safe = classify_exception(Exception(text))
            if kind in ("AUTH_EXPIRED", "AUTH_INVALID") and session_level:
                is_auth_error = True
                auth_error_msg = safe
            if kind == "RATE_LIMITED":
                is_rate_limited = True
                rate_msg = safe
                if session_level:
                    is_session_rate_limited = True
            return is_auth_error, is_rate_limited

        try:
            healthy, reason = adapter.health()
            checks: dict[str, str] = {"connection": "READY" if healthy else f"FAILED: {reason}"}
            if not healthy:
                rest_ok = False
                _note_failure(str(reason), session_level=True)
        except Exception as exc:
            kind, safe = classify_exception(exc)
            checks = {"connection": f"FAILED: {type(exc).__name__}"}
            rest_ok = False
            _note_failure(str(exc), session_level=True)
        if rest_ok:
            self._set_leg(spec.broker_id, "rest", "READY", success=True)
        else:
            self._set_leg(spec.broker_id, "rest", "FAILED", checks.get("connection", "failed"))

        time.sleep(0.05)
        try:
            account = adapter.account()
            checks["account"] = "READY"
            details["account_id"] = self._account_id_of(account)
        except Exception as exc:
            checks["account"] = f"FAILED: {type(exc).__name__}"
            # Identity comes from the same profile call as health(), so a
            # failure here is a session-level proof failure.
            _note_failure(str(exc), session_level=True)

        time.sleep(0.05)
        try:
            funds = adapter.funds()
            checks["funds"] = "READY"
            if isinstance(funds, dict):
                details["funds_available"] = self._safe_float(funds.get("available"))
                details["funds_used"] = self._safe_float(funds.get("used"))
                total = funds.get("total", funds.get("equity"))
                details["funds_total"] = self._safe_float(total)
        except Exception as exc:
            kind, safe = classify_exception(exc)
            if kind == "RATE_LIMITED":
                checks["funds"] = "FAILED: rate limited (HTTP 429)"
            else:
                checks["funds"] = f"FAILED: {type(exc).__name__}"
            _note_failure(str(exc), session_level=False)

        for key, fn in (
            ("positions", "positions"),
            ("orders", "open_orders"),
        ):
            time.sleep(0.05)
            try:
                rows = getattr(adapter, fn)()
                checks[key] = "READY"
                details["positions_open" if key == "positions" else "orders_open"] = (
                    len(rows) if isinstance(rows, (list, tuple)) else None
                )
            except Exception as exc:
                kind, safe = classify_exception(exc)
                if kind == "RATE_LIMITED":
                    checks[key] = "FAILED: rate limited (HTTP 429)"
                else:
                    checks[key] = f"FAILED: {type(exc).__name__}"
                _note_failure(str(exc), session_level=False)

        # Order-update WS leg (independent of REST).
        order_ws_status = "UNKNOWN"
        try:
            get_order_health: Any = getattr(adapter, "order_stream_health", None)
            if callable(get_order_health):
                ws_pair: Any = get_order_health()
                ws_ok = bool(ws_pair[0])
                ws_reason = str(ws_pair[1] if len(ws_pair) > 1 else "")
                order_ws_status = "READY" if ws_ok else f"FAILED: {ws_reason}"
                checks["order_stream"] = order_ws_status
                if ws_ok:
                    self._set_leg(spec.broker_id, "order_ws", "READY", success=True)
                else:
                    self._set_leg(spec.broker_id, "order_ws", "FAILED", str(ws_reason))
        except Exception as exc:
            checks["order_stream"] = f"FAILED: {type(exc).__name__}"
            self._set_leg(spec.broker_id, "order_ws", "FAILED", str(exc))
        md = state.get("market_data")
        if md is not None:
            try:
                md_ok, md_reason = md.health()
                # Reconnect ONLY the failed market leg (bounded, one attempt).
                if not md_ok and hasattr(md, "connect"):
                    with contextlib.suppress(Exception):
                        md.connect()
                    md_ok, md_reason = md.health()
                checks["market_data"] = "READY" if md_ok else f"FAILED: {md_reason}"
                if md_ok:
                    self._set_leg(spec.broker_id, "market_ws", "READY", success=True)
                else:
                    self._set_leg(spec.broker_id, "market_ws", "FAILED", str(md_reason))
                details["market_data_last_tick"] = getattr(md, "last_tick_time", "")
                details["market_data_latency_ms"] = getattr(md, "last_latency_ms", None)
            except Exception as exc:
                checks["market_data"] = f"FAILED: {type(exc).__name__}"
                self._set_leg(spec.broker_id, "market_ws", "FAILED", str(exc))

        with self._lock:
            live = self._states.get(spec.broker_id)
            if live is None:
                return
            live["checks"] = checks
            live["details"] = details
            live["last_sync"] = self._now_stamp()
            if is_rate_limited and (
                is_session_rate_limited
                or checks.get("connection") != "READY"
                or checks.get("account") != "READY"
            ):
                failures = 0
                with contextlib.suppress(Exception):
                    failures = int(
                        self._unified.get(spec.broker_id, {})
                        .get("legs", {})
                        .get("rest", {})
                        .get("consecutive_failures", 0)
                        or 0
                    )
                delay = backoff_with_jitter(min(failures, 5), base_s=5.0)
                self._arm_retry(spec.broker_id, delay)
                live["status"] = BrokerStatus.ERROR
                live["reason"] = redact(f"rate limited — retry in {delay:.0f}s")
                self._touch_error(spec.broker_id, "RATE_LIMITED", rate_msg or "HTTP 429")
            elif is_auth_error:
                config = self._config_values(spec)
                auto_auth = getattr(spec, "auto_authenticate", None)
                if auto_auth is not None and config:
                    generation = self._generation(spec.broker_id)
                    _LOGGER.info(
                        "AUTH_EXPIRED broker=%s — attempting auto-authentication recovery",
                        spec.broker_id,
                    )
                    if self._try_auto_auth(spec, config, generation=generation):
                        return
                live["status"] = BrokerStatus.LOGIN_REQUIRED
                live["reason"] = f"session expired — {redact(auth_error_msg) or 'login required'}"
                self._touch_error(spec.broker_id, "AUTH_EXPIRED", auth_error_msg)
                self._audit.record(
                    broker_id=spec.broker_id,
                    action="verification failed",
                    result="LOGIN_REQUIRED",
                    reason=auth_error_msg,
                )
                if adapter is not None:
                    with contextlib.suppress(Exception):
                        adapter._connected = False
            elif checks.get("connection") != "READY":
                rest_failures = int(
                    self._unified.get(spec.broker_id, {})
                    .get("legs", {})
                    .get("rest", {})
                    .get("consecutive_failures", 0)
                    or 0
                )
                if rest_failures < 3:
                    conn_err = redact(checks.get("connection", ""))
                    live["status"] = BrokerStatus.MARKET_DATA_NOT_READY
                    live["reason"] = f"REST leg degraded ({rest_failures}/3): {conn_err}"
                    self._touch_error(spec.broker_id, "NETWORK", live["reason"])
                else:
                    live["status"] = BrokerStatus.DISCONNECTED
                    live["reason"] = redact(checks.get("connection", "connection check failed"))
                    self._touch_error(spec.broker_id, "NETWORK", live["reason"])
                    if adapter is not None:
                        with contextlib.suppress(Exception):
                            adapter._connected = False
            elif checks.get("account") != "READY":
                live["status"] = BrokerStatus.ACCOUNT_NOT_READY
                live["reason"] = redact(checks.get("account", "account check failed"))
                self._touch_error(spec.broker_id, "VERIFY", live["reason"])
            else:
                failed_checks = [k for k, v in checks.items() if str(v).startswith("FAILED")]
                if is_rate_limited:
                    failures = 0
                    with contextlib.suppress(Exception):
                        failures = int(
                            self._unified.get(spec.broker_id, {})
                            .get("legs", {})
                            .get("rest", {})
                            .get("consecutive_failures", 0)
                            or 0
                        )
                    delay = backoff_with_jitter(min(failures, 5), base_s=5.0)
                    self._arm_retry(spec.broker_id, delay)
                    if "orders" in failed_checks or "positions" in failed_checks:
                        live["status"] = BrokerStatus.EXECUTION_NOT_READY
                    else:
                        live["status"] = BrokerStatus.MARKET_DATA_NOT_READY
                    live["reason"] = (
                        f"account verified; degraded: {', '.join(failed_checks)} "
                        f"(venue rate limited — cooldown {delay:.0f}s)"
                    )
                    self._touch_verified(spec.broker_id)
                elif failed_checks:
                    if "orders" in failed_checks or "positions" in failed_checks:
                        live["status"] = BrokerStatus.EXECUTION_NOT_READY
                    elif "market_data" in failed_checks or "order_stream" in failed_checks:
                        live["status"] = BrokerStatus.MARKET_DATA_NOT_READY
                    else:
                        live["status"] = BrokerStatus.EXECUTION_NOT_READY
                    live["reason"] = (
                        f"account verified; degraded leg(s): {', '.join(failed_checks)} — "
                        + redact(str(checks.get(failed_checks[0], "")))
                    )
                    self._touch_verified(spec.broker_id)
                else:
                    live["status"] = BrokerStatus.CONNECTED
                    live["reason"] = "all checks passed"
                    self._clear_retry(spec.broker_id)
                    self._touch_verified(spec.broker_id)
            self._bump_version(spec.broker_id)
        self.state_changed.emit(spec.broker_id)

    # ── internals ────────────────────────────────────────────────

    def _intent_path(self) -> Path:
        return Path(self._data_dir) / "broker" / "session_intent.json"

    def _load_intents(self) -> None:
        try:
            import json

            path = self._intent_path()
            if not path.is_file():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            with self._lock:
                for broker_id, row in data.items():
                    if broker_id not in self._specs or not isinstance(row, dict):
                        continue
                    desired = str(row.get("desired", "") or "").upper()
                    if desired in ("CONNECTED", "DISCONNECTED"):
                        self._desired[broker_id] = desired
                    with contextlib.suppress(TypeError, ValueError):
                        self._generations[broker_id] = max(
                            int(self._generations.get(broker_id, 0)),
                            int(row.get("generation", 0) or 0),
                        )
        except Exception:
            pass

    def _save_intents(self) -> None:
        try:
            import json

            path = self._intent_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    bid: {
                        "desired": self._desired.get(bid, "DISCONNECTED"),
                        "generation": int(self._generations.get(bid, 0)),
                    }
                    for bid in self._specs
                }
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _bump_version(self, broker_id: str) -> int:
        with self._lock:
            uni = self._unified.setdefault(broker_id, {})
            uni["version"] = int(uni.get("version", 0) or 0) + 1
            return int(uni["version"])

    def _generation(self, broker_id: str) -> int:
        with self._lock:
            return int(self._generations.get(broker_id, 0))

    def _desired_state(self, broker_id: str) -> str:
        with self._lock:
            return str(self._desired.get(broker_id, "DISCONNECTED"))

    def _set_leg(
        self, broker_id: str, leg: str, status: str, error: str = "", *, success: bool = False
    ) -> None:
        with self._lock:
            uni = self._unified.setdefault(broker_id, {})
            legs = uni.setdefault("legs", {})
            row = legs.setdefault(
                leg,
                {
                    "status": "UNKNOWN",
                    "last_success": "",
                    "last_error": "",
                    "consecutive_failures": 0,
                },
            )
            row["status"] = str(status or "UNKNOWN")
            if success:
                row["last_success"] = self._now_stamp()
                row["consecutive_failures"] = 0
                if not error:
                    row["last_error"] = ""
            elif error:
                row["last_error"] = redact(str(error))[:300]
                try:
                    row["consecutive_failures"] = int(row.get("consecutive_failures", 0) or 0) + 1
                except (TypeError, ValueError):
                    row["consecutive_failures"] = 1

    def _touch_verified(self, broker_id: str) -> None:
        with self._lock:
            uni = self._unified.setdefault(broker_id, {})
            uni["last_verified"] = self._now_stamp()
            uni["last_verified_epoch"] = time.monotonic()
            uni["last_error"] = ""
            uni["last_error_kind"] = ""
            uni["token_reused"] = bool(uni.get("token_reused", False))

    def _touch_error(self, broker_id: str, kind: str, reason: str) -> None:
        with self._lock:
            uni = self._unified.setdefault(broker_id, {})
            uni["last_error"] = redact(str(reason or ""))[:500]
            uni["last_error_kind"] = str(kind or "UNKNOWN")

    def _retry_armed(self, broker_id: str) -> bool:
        import time as _time

        with self._lock:
            uni = self._unified.get(broker_id, {})
            return float(uni.get("retry_until", 0.0) or 0.0) > _time.monotonic()

    def _arm_retry(self, broker_id: str, delay_s: float) -> None:
        import time as _time

        with self._lock:
            uni = self._unified.setdefault(broker_id, {})
            uni["retry_after_s"] = max(0.0, float(delay_s))
            uni["retry_until"] = _time.monotonic() + max(0.0, float(delay_s))

    def _clear_retry(self, broker_id: str) -> None:
        with self._lock:
            uni = self._unified.setdefault(broker_id, {})
            uni["retry_after_s"] = 0.0
            uni["retry_until"] = 0.0

    def read_only_adapter(self, broker_id: str) -> Any | None:
        """A fresh adapter for READ-ONLY venue reads, or None if unavailable.

        The manager's own adapter carries the order WebSocket, and two
        adapters on one stream means two reconnect drivers — the exact
        thrash the order socket's flap breaker exists to prevent. Data reads
        (quotes, historical candles) never need the order socket, so this
        builds one with it disabled and leaves the live one alone.
        """
        spec = self._specs.get(broker_id)
        if spec is None or spec.build_adapter is None:
            return None
        config: dict[str, str] = self._config_values(spec) or {}
        stored = self._session_store(spec).load_token() or {}
        token = str(stored.get("access_token", "") or "")
        key = str(config.get(spec.key_field, "") or "")
        if not token or not key:
            return None
        # The build hook is a provider-owned callable whose signature differs
        # per venue, so it is invoked through a widened type rather than a
        # guessed parameter list: the flag is offered, and a hook that does
        # not take it still gets a working read-only adapter.
        build: Any = spec.build_adapter
        try:
            return build(key, token, enable_order_ws=False)
        except TypeError:
            return build(key, token)

    def _adapter_verified(self, broker_id: str) -> bool:
        with self._lock:
            state = self._states.get(broker_id, {})
            adapter = state.get("adapter")
        if adapter is None:
            return False
        is_conn = getattr(adapter, "connected", None)
        if is_conn is None:
            is_conn = getattr(adapter, "_connected", False)
        return bool(is_conn)

    def _activate(
        self,
        spec: BrokerSpec,
        config: dict[str, str],
        token: str,
        why: str,
        generation: int | None = None,
        token_reused: bool = False,
    ) -> None:
        """Build the authenticated adapter, register the venue, health-check."""
        if generation is None:
            generation = self._generation(spec.broker_id)
        # Latest-intent-wins: a DISCONNECT issued while auth ran must win.
        if (
            generation != self._generation(spec.broker_id)
            or self._desired_state(spec.broker_id) != "CONNECTED"
        ):
            self._audit.record(
                broker_id=spec.broker_id,
                action="ACTIVATE refused",
                result="stale-generation",
                reason=f"gen {generation} vs current {self._generation(spec.broker_id)}",
            )
            return
        assert spec.build_adapter is not None
        self._set_status(spec.broker_id, BrokerStatus.CONFIGURING, "verifying account…")
        try:
            adapter = spec.build_adapter(config[spec.key_field], token)
            adapter.connect()
        except Exception as exc:
            kind, safe = classify_exception(exc)
            safe = redact(safe)
            if kind == "RATE_LIMITED":
                delay = backoff_with_jitter(1, base_s=5.0)
                self._arm_retry(spec.broker_id, delay)
                self._set_status(
                    spec.broker_id, BrokerStatus.ERROR, f"rate limited — retry in {delay:.0f}s"
                )
            elif kind in ("AUTH_EXPIRED", "AUTH_INVALID"):
                self._set_status(
                    spec.broker_id, BrokerStatus.LOGIN_REQUIRED, f"login required — {safe}"
                )
                self._audit.record(
                    broker_id=spec.broker_id,
                    action="authorization required",
                    result="LOGIN_REQUIRED",
                    reason=safe,
                )
            elif kind in ("NETWORK", "TIMEOUT"):
                self._set_status(spec.broker_id, BrokerStatus.DISCONNECTED, safe)
            elif kind == "BAD_REQUEST":
                self._set_status(spec.broker_id, BrokerStatus.ERROR, safe)
            else:
                text_lower = safe.lower()
                if any(
                    k in text_lower
                    for k in (
                        "unreachable",
                        "timeout",
                        "timed out",
                        "oserror",
                        "connection refused",
                    )
                ):
                    self._set_status(spec.broker_id, BrokerStatus.DISCONNECTED, safe)
                elif (
                    "credentials" in str(getattr(exc, "code", "")).lower()
                    or "auth" in text_lower
                    or "401" in text_lower
                ):
                    self._set_status(
                        spec.broker_id,
                        BrokerStatus.LOGIN_REQUIRED,
                        f"venue rejected the session: {safe}",
                    )
                else:
                    self._set_status(
                        spec.broker_id, BrokerStatus.ERROR, f"connection failed: {safe}"
                    )
            self._touch_error(spec.broker_id, kind, safe)
            self._set_leg(spec.broker_id, "rest", "FAILED", safe)
            self._bump_version(spec.broker_id)
            return
        # Re-check intent after network I/O: DISCONNECT during auth wins.
        if (
            generation != self._generation(spec.broker_id)
            or self._desired_state(spec.broker_id) != "CONNECTED"
        ):
            with contextlib.suppress(Exception):
                if hasattr(adapter, "disconnect"):
                    adapter.disconnect()
            self._audit.record(
                broker_id=spec.broker_id,
                action="ACTIVATE discarded",
                result="stale-generation",
                reason="disconnect won during auth",
            )
            return
        md = None
        with contextlib.suppress(Exception):
            if spec.build_market_data is not None:
                md = spec.build_market_data(config.get(spec.key_field, ""), token)
                if hasattr(md, "connect"):
                    md.connect()
        registered, reg_reason = False, "venue registration unavailable"
        with contextlib.suppress(Exception):
            if spec.venue_register is not None:
                registered, reg_reason = spec.venue_register(adapter, md)
        state = self._states[spec.broker_id]
        with self._lock:
            state["adapter"] = adapter
            state["market_data"] = md
            state["checks"] = {"connection": "READY", "account": "READY"}
        details = self._empty_details()
        account_id = ""
        with contextlib.suppress(Exception):
            account_id = self._account_id_of(adapter.account())
            details["account_id"] = account_id
        if not account_id:
            self._set_status(
                spec.broker_id,
                BrokerStatus.ACCOUNT_NOT_READY,
                "connected but account identity not verified",
            )
            self._touch_error(spec.broker_id, "VERIFY", "account identity missing")
            self._bump_version(spec.broker_id)
            return
        with self._lock:
            state["details"] = details
            state["last_sync"] = self._now_stamp()
            state["status"] = BrokerStatus.CONNECTED
            state["reason"] = f"{why}; {reg_reason}" if not registered else why
            uni = self._unified.setdefault(spec.broker_id, {})
            uni["token_reused"] = bool(token_reused)
        self._clear_retry(spec.broker_id)
        self._set_leg(spec.broker_id, "rest", "READY", success=True)
        self._touch_verified(spec.broker_id)
        self._bump_version(spec.broker_id)
        self._audit.record(
            broker_id=spec.broker_id,
            action="token reused" if token_reused else "token verified",
            result="CONNECTED",
            reason=f"account {account_id}",
        )
        self._note(spec.broker_id, "connected")
        self._auto_select_if_unset(spec.broker_id)
        self.state_changed.emit(spec.broker_id)
        # First freshness check runs once; later checks are throttled by the
        # snapshot path (no polling thread, no repeated REST bursts).
        self._queue_health_refresh(spec.broker_id, force=True)

    def _auto_select_if_unset(self, broker_id: str) -> None:
        """Record a first-run selection when none exists — never overwrite.

        A successful connection used to call ``selection.select(broker_id)``
        unconditionally, so connecting a second venue silently REPLACED the
        operator's SYSTEM choice and every surface (capital, order routing,
        market data) followed the newcomer. Selection is a user decision:
        only seed it while no explicit choice has been recorded yet.
        """
        try:
            from app.services.broker_selection_service import (
                BrokerSelectionService,
                app_selection_store,
            )

            selection_svc = BrokerSelectionService(app_selection_store(self._data_dir))
            selection = selection_svc.current_or_none()
            if selection is not None and selection.reason == "user-selected":
                # An explicit SYSTEM choice is authoritative — connecting
                # another venue must never steal it.
                return
            selection_svc.select(broker_id)
        except Exception:
            # Selection bookkeeping is advisory here; never fail a connect
            # because it could not be recorded.
            pass

    def _config_values(self, spec: BrokerSpec) -> dict[str, str] | None:
        try:
            values = self._store.load(spec.config_service)
        except Exception:
            return None
        if not isinstance(values, dict):
            return None
        clean = {str(k): str(v or "").strip() for k, v in values.items()}
        if any(not clean.get(key) for key in spec.required_config):
            return None
        return clean

    def _session_store(self, spec: BrokerSpec) -> Any:
        if self._session_factory is not None:
            return self._session_factory(self._store, spec.session_service)
        assert spec.build_session_store is not None
        return spec.build_session_store(self._store, spec.session_service)

    def _fresh_state(self, spec: BrokerSpec) -> dict[str, Any]:
        config = self._config_values(spec)
        return {
            "status": BrokerStatus.NOT_CONFIGURED if not config else BrokerStatus.LOGIN_REQUIRED,
            "reason": "" if config else "no configuration saved",
            "checks": {},
            "details": self._empty_details(),
            "last_sync": "",
            "configured": bool(config),
            "adapter": None,
            "market_data": None,
            "api_key_masked": self._mask(config.get(spec.key_field, "") if config else ""),
        }

    @staticmethod
    def _empty_details() -> dict[str, Any]:
        """Safe UI scalars only — never secrets, never full payloads."""
        return {
            "account_id": "",
            "funds_available": None,
            "funds_used": None,
            "funds_total": None,
            "positions_open": None,
            "orders_open": None,
        }

    @staticmethod
    def _now_stamp() -> str:
        with contextlib.suppress(Exception):
            return datetime.now().strftime("%H:%M:%S")
        return ""

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number

    @staticmethod
    def _account_id_of(account: Any) -> str:
        """Reduce an account payload to its display id (no emails/usernames)."""
        if not isinstance(account, dict):
            return ""
        for key in ("account_id", "user_id", "client_id", "id"):
            candidate = account.get(key)
            if candidate:
                return str(candidate)[:64]
        return ""

    @staticmethod
    def _mask(value: str) -> str:
        """Full redaction — even partial key material never crosses IPC."""
        if not value:
            return ""
        return "••••"

    def _set_status(self, broker_id: str, status: BrokerStatus, reason: str) -> None:
        import time as _time

        with self._lock:
            state = self._states.setdefault(broker_id, {})
            state["status"] = status
            state["reason"] = reason
            if status in (BrokerStatus.AUTHENTICATING, BrokerStatus.CONFIGURING):
                self._auth_started_at[broker_id] = _time.monotonic()
            else:
                self._auth_started_at.pop(broker_id, None)
            if status is BrokerStatus.NOT_CONFIGURED:
                spec = self._specs.get(broker_id)
                if spec is not None:
                    fresh = self._fresh_state(spec)
                    keys = ("configured", "api_key_masked", "details", "last_sync")
                    state.update({k: fresh[k] for k in keys})
        self.state_changed.emit(broker_id)

    def _note(self, broker_id: str, text: str) -> None:
        self.message.emit(f"{self.display_name(broker_id)}: {text}")

    def _callback_url(self, broker_id: str = "") -> str:
        """Callback URL for one venue (never the first spec's for another)."""
        spec = self._specs.get(broker_id) if broker_id else None
        if spec is None:
            spec = self._specs.get(next(iter(self._specs), ""))
        return spec.callback_url if spec else ""


_SHARED_MANAGERS: dict[str, BrokerManager] = {}
_SHARED_LOCK = threading.Lock()


def shared_manager(data_dir: str | Path) -> BrokerManager:
    """One authoritative BrokerManager per data_dir (fixes dual-manager drift).

    Headless IPC and the EC2 remote gateway share this instance so there is a
    single worker, a single venue registration and a single snapshot source.
    """
    key = str(Path(data_dir).resolve() if Path(data_dir).is_absolute() else Path(data_dir))
    with _SHARED_LOCK:
        manager = _SHARED_MANAGERS.get(key)
        if manager is None:
            manager = BrokerManager(data_dir=data_dir)
            for broker_id in manager.broker_ids():
                manager.submit_check(broker_id)
            _SHARED_MANAGERS[key] = manager
        return manager


__all__ = ["CALLBACK_PORT", "BrokerManager", "shared_manager"]
