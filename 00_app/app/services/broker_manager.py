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
arrive exclusively through Qt signals. No blocking call ever runs on the
UI thread.

This module is STRATEGY-agnostic and BROKER-generic: every concrete
wiring comes from a :class:`~broker.management.BrokerSpec` supplied by
the composition root (``data.provider.factory``). Adding a broker = one
spec; nothing here changes.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from broker import BrokerStatus
from broker.management import BrokerSpec
from PySide6.QtCore import QObject, QThread, Signal

CALLBACK_PORT = 9474
_SESSION_JOB = "session-check"
_LOGIN_JOB = "interactive-login"
_HEALTH_JOB = "health-check"


class _Worker(QThread):
    """One serialized job queue for every network/SDK operation."""

    def __init__(self, on_job: Callable[[str, str], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
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


class BrokerManager(QObject):
    """Owns broker configuration/auth/state. UI reads snapshots only."""

    state_changed = Signal(str)  # broker_id
    message = Signal(str)  # user-safe log line (never secrets)

    def __init__(
        self,
        data_dir: str | Path,
        credential_store: Any | None = None,
        session_store_factory: Callable[[Any, str], Any] | None = None,
        browser_opener: Callable[[str], None] | None = None,
        specs: dict[str, BrokerSpec] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        from data.provider.credentials_store import default_store

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
        self._worker = _Worker(self._run_job, self)
        self._worker.start()
        self._stopping = False

    @staticmethod
    def _default_specs() -> dict[str, BrokerSpec]:
        from data.provider.factory import zerodha_management_spec

        return {"zerodha": zerodha_management_spec()}

    # ── public surface (UI + LIVE consume only these) ────────────

    def broker_ids(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def display_name(self, broker_id: str) -> str:
        spec = self._specs.get(broker_id)
        return spec.display_name if spec else str(broker_id)

    def state(self, broker_id: str) -> dict[str, Any]:
        return dict(self._states.get(broker_id, {}))

    def snapshot(self) -> dict[str, Any]:
        brokers = []
        for broker_id in self._specs:
            state = self.state(broker_id)
            spec = self._specs[broker_id]
            brokers.append(
                {
                    "id": broker_id,
                    "name": spec.display_name,
                    "status": state["status"].value,
                    "reason": state["reason"],
                    "checks": dict(state["checks"]),
                    "configured": state["configured"],
                    "api_key_masked": state["api_key_masked"],
                    "can_login": state["configured"]
                    and state["status"]
                    in (
                        BrokerStatus.LOGIN_REQUIRED,
                        BrokerStatus.DISCONNECTED,
                        BrokerStatus.ERROR,
                    ),
                    "can_disconnect": state["configured"]
                    and state["status"] in (BrokerStatus.CONNECTED, BrokerStatus.LIVE_READY),
                }
            )
        return {"brokers": brokers, "callback_url": self._callback_url()}

    def configure(self, broker_id: str, values: dict[str, str]) -> tuple[bool, str]:
        """Save required config (credentials store). Secret values never echoed."""
        spec = self._specs.get(broker_id)
        if spec is None:
            return False, f"unknown broker: {broker_id}"
        clean = {str(k): str(v or "").strip() for k, v in values.items()}
        missing = [key for key in spec.required_config if not clean.get(key)]
        if missing:
            return False, f"missing configuration: {', '.join(missing)}"
        try:
            self._store.save(spec.config_service, clean)
        except Exception as exc:
            return False, f"secure storage refused: {type(exc).__name__}"
        state = self._states[broker_id]
        state["configured"] = True
        state["api_key_masked"] = self._mask(clean["api_key"])
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

    def disconnect_broker(self, broker_id: str) -> tuple[bool, str]:
        """Clear the session, keep the configuration, stop the venue.

        Named ``disconnect_broker`` (not ``disconnect``) because QObject
        already owns a signal-disconnect method with another signature.
        """
        spec = self._specs.get(broker_id)
        if spec is None:
            return False, f"unknown broker: {broker_id}"
        with contextlib.suppress(Exception):
            self._session_store(spec).clear()
        with contextlib.suppress(Exception):
            if spec.venue_unregister is not None:
                spec.venue_unregister()
        self._set_status(broker_id, BrokerStatus.LOGIN_REQUIRED, "disconnected — login required")
        self._note(broker_id, "disconnected (configuration kept)")
        return True, "disconnected"

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
        self._note(broker_id, "removed")
        self.state_changed.emit(broker_id)
        return True, "broker removed"

    def is_connected(self, broker_id: str) -> bool:
        status = self._states.get(broker_id, {}).get("status")
        return status in (BrokerStatus.CONNECTED, BrokerStatus.LIVE_READY)

    def stop_worker(self) -> None:
        if not self._stopping:
            self._stopping = True
            self._worker.stop()
            with contextlib.suppress(Exception):
                self._worker.wait(3000)

    # ── worker jobs (background thread ONLY) ─────────────────────

    def _run_job(self, kind: str, broker_id: str) -> None:
        spec = self._specs.get(broker_id)
        if spec is None:
            return
        if kind == _SESSION_JOB:
            self._job_session_check(spec)
        elif kind == _LOGIN_JOB:
            self._job_login(spec)
        elif kind == _HEALTH_JOB:
            self._job_health(spec)
        elif kind == "__error__":
            self._set_status(broker_id, BrokerStatus.ERROR, "background operation failed")

    def _job_session_check(self, spec: BrokerSpec) -> None:
        config = self._config_values(spec)
        if not config:
            self._set_status(spec.broker_id, BrokerStatus.NOT_CONFIGURED, "no configuration saved")
            return
        stored = self._session_store(spec).load_token()
        if stored is None:
            self._set_status(spec.broker_id, BrokerStatus.LOGIN_REQUIRED, "no active session")
            return
        flow = spec.build_flow() if spec.build_flow is not None else None
        if flow is None:
            self._set_status(spec.broker_id, BrokerStatus.ERROR, "auth flow not wired")
            return
        ok, reason = flow.validate(config["api_key"], stored["access_token"])
        if not ok:
            if "unreachable" in reason:
                self._set_status(spec.broker_id, BrokerStatus.DISCONNECTED, reason)
            else:
                self._set_status(
                    spec.broker_id, BrokerStatus.LOGIN_REQUIRED, f"session expired — {reason}"
                )
            return
        self._activate(spec, config, stored["access_token"], reason)

    def _job_login(self, spec: BrokerSpec) -> None:
        config = self._config_values(spec)
        if not config:
            self._set_status(spec.broker_id, BrokerStatus.NOT_CONFIGURED, "no configuration saved")
            return
        assert spec.interactive_login is not None and spec.build_flow is not None
        session_store = self._session_store(spec)
        ok, message = spec.interactive_login(
            spec.build_flow(),
            config["api_key"],
            config["api_secret"],
            session_store,
            port=int(spec.callback_port or CALLBACK_PORT),
            open_browser=self._browser,
        )
        self._note(spec.broker_id, message)
        if not ok:
            self._set_status(spec.broker_id, BrokerStatus.LOGIN_REQUIRED, message)
            return
        stored = session_store.load_token() or {}
        self._activate(spec, config, stored.get("access_token", ""), message)

    def _job_health(self, spec: BrokerSpec) -> None:
        """Read-only checks on the active adapter; never places an order."""
        state = self._states.get(spec.broker_id, {})
        adapter = state.get("adapter")
        if adapter is None:
            return
        checks: dict[str, str] = {}
        try:
            healthy, reason = adapter.health()
            checks["connection"] = "READY" if healthy else f"FAILED: {reason}"
        except Exception as exc:
            checks["connection"] = f"FAILED: {type(exc).__name__}"
        for key, fn in (
            ("account", "account"),
            ("funds", "funds"),
            ("positions", "positions"),
            ("orders", "open_orders"),
        ):
            try:
                getattr(adapter, fn)()
                checks[key] = "READY"
            except Exception as exc:
                checks[key] = f"FAILED: {type(exc).__name__}"
        md = state.get("market_data")
        if md is not None:
            try:
                md_ok, md_reason = md.health()
                checks["market_data"] = "READY" if md_ok else f"FAILED: {md_reason}"
            except Exception as exc:
                checks["market_data"] = f"FAILED: {type(exc).__name__}"
        state["checks"] = checks
        failed = [k for k, v in checks.items() if v != "READY"]
        if failed:
            state["status"] = BrokerStatus.ACCOUNT_NOT_READY
            state["reason"] = f"checks failed: {', '.join(failed)}"
        else:
            state["status"] = BrokerStatus.CONNECTED
            state["reason"] = "all checks passed"
        self.state_changed.emit(spec.broker_id)

    # ── internals ────────────────────────────────────────────────

    def _activate(self, spec: BrokerSpec, config: dict[str, str], token: str, why: str) -> None:
        """Build the authenticated adapter, register the venue, health-check."""
        assert spec.build_adapter is not None
        try:
            adapter = spec.build_adapter(config["api_key"], token)
            adapter.connect()
        except Exception as exc:
            text = str(exc)
            if "CREDENTIALS" in str(getattr(exc, "code", "")) or "auth" in text.lower():
                self._set_status(
                    spec.broker_id, BrokerStatus.LOGIN_REQUIRED, "venue rejected the session"
                )
            else:
                self._set_status(spec.broker_id, BrokerStatus.ERROR, f"connection failed: {text}")
            return
        md = None
        with contextlib.suppress(Exception):
            if spec.build_market_data is not None:
                md = spec.build_market_data(config["api_key"], token)
        registered, reg_reason = False, "venue registration unavailable"
        with contextlib.suppress(Exception):
            if spec.venue_register is not None:
                registered, reg_reason = spec.venue_register(adapter, md)
        state = self._states[spec.broker_id]
        state["adapter"] = adapter
        state["market_data"] = md
        state["checks"] = {"connection": "READY", "account": "READY"}
        state["status"] = BrokerStatus.CONNECTED
        state["reason"] = f"{why}; {reg_reason}" if not registered else why
        self._note(spec.broker_id, "connected")
        self.state_changed.emit(spec.broker_id)
        self._worker.submit(_HEALTH_JOB, spec.broker_id)

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
            "configured": bool(config),
            "adapter": None,
            "market_data": None,
            "api_key_masked": self._mask(config.get("api_key", "") if config else ""),
        }

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        return f"{value[:4]}…{value[-2:]}" if len(value) > 8 else "••••"

    def _set_status(self, broker_id: str, status: BrokerStatus, reason: str) -> None:
        state = self._states.setdefault(broker_id, {})
        state["status"] = status
        state["reason"] = reason
        if status is BrokerStatus.NOT_CONFIGURED:
            spec = self._specs.get(broker_id)
            if spec is not None:
                fresh = self._fresh_state(spec)
                state.update({k: fresh[k] for k in ("configured", "api_key_masked")})
        self.state_changed.emit(broker_id)

    def _note(self, broker_id: str, text: str) -> None:
        self.message.emit(f"{self.display_name(broker_id)}: {text}")

    def _callback_url(self) -> str:
        spec = self._specs.get(next(iter(self._specs), ""))
        return spec.callback_url if spec else ""


__all__ = ["CALLBACK_PORT", "BrokerManager"]
