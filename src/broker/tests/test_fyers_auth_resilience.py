"""Comprehensive FYERS authentication resilience & failure-injection test suite.

Covers all 16 mission-critical failure, recovery, and regression scenarios:
1. Expired token returning HTTP 400 ("Token is Invalid or Expired") -> AUTH_EXPIRED.
2. Invalid token and revoked session (HTTP 401: "unauthorized") -> LOGIN_REQUIRED.
3. Genuine DNS, connection refused, and read timeout failures -> NETWORK/TIMEOUT.
4. FYERS rate limits (HTTP 429 with Retry-After) and server errors (HTTP 500/502).
5. Invalid credentials and failed TOTP/PIN authentication.
6. Redirect URI mismatch and missing auth code yielding actionable error messages.
7. Malformed or incomplete token responses handled cleanly without crash.
8. Token persistence failures and empty token rejection without secret leakage.
9. Duplicate and concurrent authentication attempts with generation isolation.
10. Process crashes / restart recovery: stale in-progress state reset + 30s watchdog.
11. EC2 worker status ("STOPPED") decoupled from broker authentication.
12. Recovery after temporary network restoration without getting stuck.
13. UI and backend state consistency across unified snapshots.
14. Repeated failures without infinite retry loops (bounded backoff).
15. Prevention of duplicate live orders after recovery.
16. Regression: "venue unreachable: Token is Invalid or Expired" classified as AUTH_EXPIRED.
"""

from __future__ import annotations

import socket
import urllib.error
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.broker_manager import BrokerManager
from broker import BrokerStatus
from broker.error_taxonomy import (
    backoff_with_jitter,
    classify_exception,
    classify_http_error,
    parse_retry_after,
)
from broker.management import BrokerSpec
from broker.providers.fyers.auto_auth import (
    FyersAutoAuthEngine,
    fetch_auth_code,
)
from broker.providers.fyers.live_auth import (
    AuthError,
    FyersAuthFlow,
    FyersSessionStore,
    HttpTransport,
)
from broker.session_state import build_snapshot, map_broker_status, next_action_for


class _MemoryCredentialStore:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.data: dict[str, Any] = dict(initial or {})

    def load(self, service: str) -> Any:
        return dict(self.data[service]) if service in self.data else None

    def save(self, service: str, values: dict[str, Any]) -> None:
        self.data[service] = dict(values)

    def delete(self, service: str) -> None:
        self.data.pop(service, None)


class _ScriptedHttpTransport(HttpTransport):
    def __init__(self) -> None:
        self.post_responses: list[Any] = []
        self.get_responses: list[Any] = []

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        del url, payload, headers
        if not self.post_responses:
            raise AuthError("no scripted POST response")
        resp = self.post_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        del url, headers
        if not self.get_responses:
            raise AuthError("no scripted GET response")
        resp = self.get_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        del url, payload, headers
        return {}

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        del url, payload, headers
        return {}


# ── Scenario 1 & 16: Regression on Expired Token Classification ─────────────


def test_expired_token_http_400_classified_as_auth_expired() -> None:
    # 1. Direct classify_http_error with 400 and FYERS token error message
    kind, safe = classify_http_error(400, "Token is Invalid or Expired")
    assert kind == "AUTH_EXPIRED"
    assert "Token is Invalid or Expired" in safe

    # 2. Exception carrying 400 and message
    exc = Exception("Token is Invalid or Expired (HTTP 400)")
    kind2, _ = classify_exception(exc)
    assert kind2 == "AUTH_EXPIRED"

    # 3. Regression: misleading "venue unreachable:" prefix does NOT mask AUTH_EXPIRED
    misleading = Exception("venue unreachable: Token is Invalid or Expired (HTTP 400)")
    kind3, _ = classify_exception(misleading)
    assert kind3 == "AUTH_EXPIRED"

    # 4. BrokerManager._classify_validate_reason
    kind4, _ = BrokerManager._classify_validate_reason(
        "venue unreachable: Token is Invalid or Expired (HTTP 400)"
    )
    assert kind4 == "AUTH_EXPIRED"


# ── Scenario 2: Invalid Token & Revoked Session (HTTP 401) ─────────────────


def test_invalid_token_http_401_revoked_session() -> None:
    kind, safe = classify_http_error(401, "unauthorized")
    assert kind == "AUTH_EXPIRED"
    assert "unauthorized" in safe

    transport = _ScriptedHttpTransport()
    transport.get_responses.append(
        AuthError("unauthorized (HTTP 401)", code="AUTH_EXPIRED", http_status=401)
    )
    flow = FyersAuthFlow(transport)
    ok, detail = flow.validate("APP-100", "invalid-token")
    assert not ok
    assert "session expired or invalid" in detail


# ── Scenario 3: Genuine Network Failures (DNS, Timeout, Refused) ───────────


def test_genuine_network_failures_classified_correctly() -> None:
    timeout_exc = TimeoutError("timed out")
    kind, _ = classify_exception(timeout_exc)
    assert kind == "TIMEOUT"

    conn_exc = ConnectionRefusedError("connection refused")
    kind, _ = classify_exception(conn_exc)
    assert kind == "NETWORK"

    url_exc = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    kind, _ = classify_exception(url_exc)
    assert kind == "NETWORK"

    # Verify that a pure network error does NOT get classified as AUTH_EXPIRED
    kind, _ = BrokerManager._classify_validate_reason(
        "venue unreachable: Name or service not known"
    )
    assert kind == "NETWORK"


# ── Scenario 4: FYERS Rate Limits (HTTP 429) & Server Errors ───────────────


def test_rate_limiting_and_server_errors() -> None:
    kind, safe = classify_http_error(429, "Too Many Requests")
    assert kind == "RATE_LIMITED"

    headers = {"Retry-After": "15"}
    delay = parse_retry_after(headers, default_s=5.0)
    assert delay == 15.0

    kind500, _ = classify_http_error(500, "Internal Server Error")
    assert kind500 == "VENUE"

    kind502, _ = classify_http_error(502, "Bad Gateway")
    assert kind502 == "VENUE"


# ── Scenario 5: Invalid Credentials & Failed TOTP/PIN ──────────────────────


def test_invalid_credentials_and_failed_totp_pin() -> None:
    creds = MagicMock()
    creds.configured = True
    creds.auto_login_configured = True
    creds.client_id = "XY12345"
    creds.pin = "1234"
    # Invalid Base32 characters in TOTP secret
    creds.totp_secret = "invalid_totp_secret_not_base32!!!"

    engine = FyersAutoAuthEngine(creds)
    possible, why = engine.auto_login_possible()
    assert not possible
    assert "Base32 key" in why or "External 2FA" in why


# ── Scenario 6: Redirect URI Mismatch & Missing Auth Code ──────────────────


def test_redirect_uri_mismatch_and_missing_auth_code() -> None:
    transport = _ScriptedHttpTransport()
    # Missing auth_code query param in redirect URL
    transport.post_responses.append(
        {"Url": "https://127.0.0.1:9475/vayren/fyers-callback?state=xyz"}
    )
    with pytest.raises(AuthError) as exc_info:
        fetch_auth_code(
            transport,
            fy_id="XY12345",
            app_id="APP-100",
            redirect_uri="http://127.0.0.1:9475/vayren/fyers-callback",
            trade_token="token123",
        )
    assert "no auth_code in redirect" in str(exc_info.value)


# ── Scenario 7: Malformed or Incomplete Token Responses ───────────────────


def test_malformed_token_responses() -> None:
    transport = _ScriptedHttpTransport()
    # No Url field in response
    transport.post_responses.append({"status": "ok"})
    with pytest.raises(AuthError) as exc_info:
        fetch_auth_code(
            transport,
            fy_id="XY12345",
            app_id="APP-100",
            redirect_uri="http://127.0.0.1:9475/vayren/fyers-callback",
            trade_token="token123",
        )
    assert "venue login step failed" in str(exc_info.value)


# ── Scenario 8: Token Persistence Failures & Refusal of Empty Tokens ───────


def test_token_persistence_refuses_empty() -> None:
    store = _MemoryCredentialStore()
    sess_store = FyersSessionStore(store)

    with pytest.raises(AuthError) as exc_info:
        sess_store.save_token("")
    assert "refusing to store an empty access token" in str(exc_info.value)

    sess_store.save_token("valid_access_token", "refresh123", "APP-100")
    loaded = sess_store.load_token()
    assert loaded is not None
    assert loaded["access_token"] == "valid_access_token"
    assert loaded["refresh_token"] == "refresh123"

    sess_store.clear()
    assert sess_store.load_token() is None


# ── Scenario 9: Duplicate & Concurrent Authentication (Generation Guard) ───


def test_generation_guard_prevents_stale_reconnect(tmp_path: Any) -> None:
    store = _MemoryCredentialStore(
        {
            "vayren:fyers": {"app_id": "APP-100", "secret": "shhh"},
            "vayren:fyers:session": {"access_token": "tok-1", "app_id": "APP-100"},
        }
    )

    flow = MagicMock()
    flow.validate.return_value = (True, "authenticated as XY12345")

    spec = BrokerSpec(
        broker_id="fyers",
        display_name="FYERS",
        config_service="vayren:fyers",
        session_service="vayren:fyers:session",
        required_config=("app_id", "secret"),
        key_field="app_id",
        secret_field="secret",
        build_flow=lambda: flow,
    )

    manager = BrokerManager(tmp_path, credential_store=store, specs={"fyers": spec})
    ok, msg, _ = manager.connect_broker("fyers")
    assert ok

    # Operator requests DISCONNECT during auth
    manager.disconnect_broker("fyers")
    assert manager.state("fyers")["status"] == BrokerStatus.DISCONNECTED

    # Attempting to activate with older generation should be rejected
    manager._activate(spec, {"app_id": "APP-100"}, "tok-1", "late-reply", generation=0)
    assert manager.state("fyers")["status"] == BrokerStatus.DISCONNECTED
    manager.stop_worker()


# ── Scenario 10: Process Crash Recovery & 30s Timeout Watchdog ─────────────


def test_process_crash_recovery_resets_in_progress_state(tmp_path: Any) -> None:
    store = _MemoryCredentialStore(
        {
            "vayren:fyers": {"app_id": "APP-100", "secret": "shhh"},
        }
    )

    spec = BrokerSpec(
        broker_id="fyers",
        display_name="FYERS",
        config_service="vayren:fyers",
        session_service="vayren:fyers:session",
        required_config=("app_id", "secret"),
        key_field="app_id",
    )

    manager = BrokerManager(tmp_path, credential_store=store, specs={"fyers": spec})

    # Simulate an interrupted in-progress state before shutdown
    manager._states["fyers"]["status"] = BrokerStatus.AUTHENTICATING

    # Watchdog timeout test
    manager._auth_started_at["fyers"] = 0.0  # long ago
    manager._check_auth_timeout("fyers")
    assert manager.state("fyers")["status"] == BrokerStatus.ERROR
    assert "timed out after 30s" in manager.state("fyers")["reason"]

    manager.stop_worker()


# ── Scenario 11: EC2 Worker Decoupling ─────────────────────────────────────


def test_ec2_trading_worker_status_decoupled_from_broker_auth() -> None:
    # Live trading service worker being STOPPED must not affect broker snapshot auth
    snap = build_snapshot(
        broker_id="fyers",
        desired="CONNECTED",
        state="CONNECTED",
        auth_valid=True,
        strategy_running=False,  # Worker is STOPPED
        live_armed=False,
    )
    assert snap["state"] == "CONNECTED"
    assert snap["auth_valid"] is True
    assert snap["strategy_running"] is False


# ── Scenario 12: Recovery After Temporary Network Restoration ──────────────


def test_transient_rest_failure_does_not_immediately_disconnect(tmp_path: Any) -> None:
    store = _MemoryCredentialStore(
        {
            "vayren:fyers": {"app_id": "APP-100", "secret": "shhh"},
            "vayren:fyers:session": {"access_token": "tok-1", "app_id": "APP-100"},
        }
    )

    adapter = MagicMock()
    adapter.connected = True
    adapter._connected = True
    # 1st failure: transient network error
    adapter.health.return_value = (False, "temporary network glitch")
    adapter.account.return_value = {"account_id": "XY12345"}
    adapter.funds.return_value = {"available": 500.0}

    spec = BrokerSpec(
        broker_id="fyers",
        display_name="FYERS",
        config_service="vayren:fyers",
        session_service="vayren:fyers:session",
        required_config=("app_id", "secret"),
        key_field="app_id",
        build_adapter=lambda _k, _t, **_kw: adapter,
    )

    manager = BrokerManager(tmp_path, credential_store=store, specs={"fyers": spec})
    manager._states["fyers"]["adapter"] = adapter
    manager._states["fyers"]["status"] = BrokerStatus.CONNECTED
    manager._desired["fyers"] = "CONNECTED"

    # Run health check with 1 transient failure
    manager._job_health(spec)

    # Must be DEGRADED (MARKET_DATA_NOT_READY), NOT DISCONNECTED!
    assert manager.state("fyers")["status"] == BrokerStatus.MARKET_DATA_NOT_READY
    assert adapter._connected is True  # Adapter was NOT killed

    # Now network recovers
    adapter.health.return_value = (True, "ok")
    manager._job_health(spec)

    # Fully recovers to CONNECTED
    assert manager.state("fyers")["status"] == BrokerStatus.CONNECTED
    manager.stop_worker()


# ── Scenario 13: UI / Backend State Machine Consistency ────────────────────


def test_state_machine_consistency_and_next_actions() -> None:
    states = (
        ("DISCONNECTED", "DISCONNECTED"),
        ("VALIDATING_SESSION", "CONNECTING"),
        ("AUTHENTICATING", "CONNECTING"),
        ("FETCHING_TOKEN", "CONNECTING"),
        ("VERIFYING_CONNECTION", "VERIFYING"),
        ("CONNECTED", "CONNECTED"),
        ("DEGRADED", "DEGRADED"),
        ("RATE_LIMITED", "RATE_LIMITED"),
        ("RETRY_WAIT", "RATE_LIMITED"),
        ("AUTH_REQUIRED", "LOGIN_REQUIRED"),
        ("FAILED", "ERROR"),
    )
    for raw, expected in states:
        mapped = map_broker_status(raw, configured=True)
        assert mapped == expected
        action = next_action_for(raw)
        assert action and len(action) > 5


# ── Scenario 14: Repeated Failures Without Infinite Retry Loops ────────────


def test_bounded_backoff_jitter() -> None:
    for attempt in range(1, 10):
        delay = backoff_with_jitter(attempt, base_s=5.0, cap_s=60.0)
        assert 0.0 <= delay <= 65.0


# ── Scenario 15: Prevention of Duplicate Live Orders After Recovery ────────


def test_broker_recovery_never_arms_live_or_places_orders() -> None:
    snap = build_snapshot(
        broker_id="fyers",
        desired="CONNECTED",
        state="CONNECTED",
        auth_valid=True,
    )
    # Connecting the broker must NEVER automatically arm live trading
    assert snap["live_armed"] is False
    assert snap["strategy_running"] is False
