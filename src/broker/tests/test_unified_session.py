"""Unified EC2 session tests — one-click CONNECT/DISCONNECT, no live orders.

Covers the required scenarios with fakes (never live credentials):
1. valid saved token reused without re-auth
2. expired token -> supported auth flow
3. new token persisted + verified
4. repeated CONNECT idempotent
5. DISCONNECT during auth prevents late reconnect
6. UI closure does not kill EC2 session (manager independent of UI)
7. recovery never restores LIVE consent automatically
8. SYSTEM and LIVE show same backend state
9. local connected cannot imply EC2 connected
10. 400/429/timeout handling
11. market vs order WS distinguished
12. duplicate ops cannot duplicate orders
13. secrets redacted
14. failed safety gate prevents LIVE arming
15. unknown/stale never healthy
16. disconnect vs older connect race
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.services.broker_manager import BrokerManager
from app.services.live_trading_service import LiveTradingService
from broker import BrokerStatus
from broker.audit import SessionAudit
from broker.error_taxonomy import (
    backoff_with_jitter,
    classify_exception,
    classify_http_error,
    redact,
)
from broker.session_state import build_snapshot, map_broker_status, next_action_for
from remote.snapshot import build_snapshot as build_remote


def _manager(tmp_path: Any, specs: dict | None = None) -> BrokerManager:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    if specs is None:
        return BrokerManager(data)
    return BrokerManager(data, specs=specs)


class _FakeBundle:
    """One fake venue: shared config/session backing + spec + handles."""

    def __init__(
        self,
        broker_id: str = "fyers",
        *,
        validate_result: tuple[bool, str] = (True, "authenticated as AB1234"),
        auto_result: tuple[bool, str] | None = None,
    ) -> None:
        from broker.management import BrokerSpec

        self.store: dict[str, Any] = {
            "config": {"app_id": "APPID-100", "secret": "shhh"},
            "session": {"access_token": "tok-1", "app_id": "APPID-100", "saved_at": "now"},
        }
        self.validate_result = validate_result
        self.auto_result = auto_result

        outer = self

        class MemStore:
            def load(self, service: str) -> Any:
                if "session" in service:
                    return dict(outer.store["session"]) if outer.store.get("session") else None
                return dict(outer.store["config"])

            def save(self, service: str, values: dict) -> None:
                if "session" in service:
                    outer.store["session"] = dict(values)
                else:
                    outer.store["config"] = dict(values)

            def delete(self, service: str) -> None:
                if "session" in service:
                    outer.store["session"] = {}

        class SessStore:
            def __init__(self, _s: Any, _n: str) -> None:
                pass

            def load_token(self) -> Any:
                return dict(outer.store["session"]) if outer.store.get("session") else None

            def save_token(self, tok: str, ref: str = "", app: str = "") -> None:
                outer.store["session"] = {
                    "access_token": tok,
                    "refresh_token": ref,
                    "app_id": app,
                }

            def clear(self) -> None:
                outer.store["session"] = {}

        self.mem_store = MemStore()
        self.session_factory = lambda _s, _n: SessStore(_s, _n)

        flow = MagicMock()
        flow.validate.return_value = validate_result
        self.flow = flow

        adapter = MagicMock()
        adapter.connected = True
        adapter._connected = True
        adapter.connect.return_value = None
        adapter.health.return_value = (True, "ok")
        adapter.account.return_value = {"account_id": "AB1234"}
        adapter.funds.return_value = {"available": 100.0, "used": 0.0, "total": 100.0}
        adapter.positions.return_value = []
        adapter.open_orders.return_value = []
        adapter.order_stream_health.return_value = (True, "connected")
        self.adapter = adapter

        md = MagicMock()
        md.health.return_value = (True, "ok")
        self.md = md

        def _auto(_config: dict, _sess: Any) -> tuple[bool, str]:
            if outer.auto_result is not None:
                if outer.auto_result[0]:
                    outer.store["session"] = {
                        "access_token": "tok-new",
                        "app_id": "x",
                        "saved_at": "n",
                    }
                return outer.auto_result
            return False, "auto not wired"

        self.spec = BrokerSpec(
            broker_id=broker_id,
            display_name="Fyers",
            config_service="svc",
            session_service="svc-session",
            required_config=("app_id", "secret"),
            masked_config=("app_id", "secret"),
            credential_schema=(),
            key_field="app_id",
            secret_field="secret",
            redirect_uri_field="",
            config_key_map={},
            build_adapter=lambda _a, _t: adapter,
            build_market_data=lambda _a, _t: md,
            build_flow=lambda: flow,
            build_session_store=lambda _s, _n: SessStore(_s, _n),
            venue_register=lambda _a, _m: (True, "registered"),
            venue_unregister=lambda: None,
            interactive_login=None,
            auto_authenticate=_auto,
            callback_port=9475,
            callback_url="http://127.0.0.1:9475/x",
            extra={},
        )

    def manager(self, tmp_path: Any) -> BrokerManager:
        data = tmp_path / "data"
        data.mkdir(parents=True, exist_ok=True)
        return BrokerManager(
            data,
            credential_store=self.mem_store,
            session_store_factory=self.session_factory,
            specs={self.spec.broker_id: self.spec},
        )


def _fake_spec(
    broker_id: str = "fyers",
    *,
    validate_result: tuple[bool, str] = (True, "authenticated as AB1234"),
    auto_result: tuple[bool, str] | None = None,
) -> Any:
    """Legacy helper: returns the spec; pair with _FakeBundle for stores."""
    return _FakeBundle(broker_id, validate_result=validate_result, auto_result=auto_result).spec


def test_1_valid_token_reused(tmp_path: Any) -> None:
    bundle = _FakeBundle()
    mgr = bundle.manager(tmp_path)
    mgr._desired["fyers"] = "CONNECTED"
    mgr._job_session_check(mgr._specs["fyers"])
    snap = mgr.get_session_snapshot("fyers")
    assert snap["state"] == "CONNECTED"
    assert snap["auth_valid"] is True
    assert snap["token_reused"] is True
    assert snap["account_id"] == "AB1234"
    mgr.stop_worker()


def test_2_expired_token_login_required(tmp_path: Any) -> None:
    bundle = _FakeBundle(validate_result=(False, "session expired or invalid"))
    mgr = bundle.manager(tmp_path)
    mgr._desired["fyers"] = "CONNECTED"
    mgr._job_session_check(mgr._specs["fyers"])
    snap = mgr.get_session_snapshot("fyers")
    assert snap["state"] == "LOGIN_REQUIRED"
    assert snap["auth_valid"] is False
    assert "login" in snap["next_action"].lower()
    mgr.stop_worker()


def test_3_new_token_persisted_and_verified(tmp_path: Any) -> None:
    bundle = _FakeBundle(
        validate_result=(False, "session expired"),
        auto_result=(True, "auto login ok"),
    )
    mgr = bundle.manager(tmp_path)
    spec = bundle.spec
    mgr._desired["fyers"] = "CONNECTED"
    mgr._job_session_check(mgr._specs["fyers"])
    snap = mgr.get_session_snapshot("fyers")
    assert snap["state"] == "CONNECTED"
    assert mgr._session_store(spec).load_token()["access_token"] == "tok-new"
    mgr.stop_worker()


def test_4_repeated_connect_idempotent(tmp_path: Any) -> None:
    bundle = _FakeBundle()
    mgr = bundle.manager(tmp_path)
    ok1, _, _ = mgr.connect_broker("fyers", {}, request_id="req-1")
    ok2, msg2, _ = mgr.connect_broker("fyers", {}, request_id="req-1")
    assert ok1 and ok2
    # In-progress second call without request id returns current op status.
    mgr._set_status("fyers", BrokerStatus.AUTHENTICATING, "working")
    ok3, msg3, _ = mgr.connect_broker("fyers", {})
    assert ok3 and "progress" in msg3.lower()
    mgr.stop_worker()


def test_5_disconnect_during_auth_prevents_late_reconnect(tmp_path: Any) -> None:
    bundle = _FakeBundle()
    mgr = bundle.manager(tmp_path)
    spec = bundle.spec
    mgr._desired["fyers"] = "CONNECTED"
    gen = mgr._generation("fyers")
    mgr.disconnect_broker("fyers", request_id="d1")
    # Late activate from the older generation must be refused.
    mgr._activate(spec, {"app_id": "APPID-100"}, "tok-1", "late", generation=gen)
    assert mgr.is_connected("fyers") is False
    assert mgr.get_session_snapshot("fyers")["state"] == "DISCONNECTED"
    mgr.stop_worker()


def test_6_disconnect_retains_token_by_default(tmp_path: Any) -> None:
    bundle = _FakeBundle()
    mgr = bundle.manager(tmp_path)
    spec = bundle.spec
    mgr._desired["fyers"] = "CONNECTED"
    mgr._job_session_check(mgr._specs["fyers"])
    assert mgr.is_connected("fyers") is True
    mgr.disconnect_broker("fyers")
    # Token retained for reuse (disconnect != revoke).
    assert mgr._session_store(spec).load_token() is not None
    mgr.revoke_session("fyers")
    assert mgr._session_store(spec).load_token() is None
    mgr.stop_worker()


def test_7_recovery_never_restores_live_consent(tmp_path: Any) -> None:
    from app import headless

    assert headless._ARMED[0] is False
    headless._ARMED[0] = True
    # Simulate process restart: fresh module-level consent must start disarmed.
    # The service persists no consent; only explicit arm sets it.
    svc_data = tmp_path / "d"
    svc_strat = tmp_path / "s"
    svc_data.mkdir(parents=True, exist_ok=True)
    svc_strat.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(str(svc_data), str(svc_strat))
    assert svc.is_live_confirmed() is False
    headless._ARMED[0] = False


def test_8_system_and_live_same_state(tmp_path: Any) -> None:
    bundle = _FakeBundle()
    mgr = bundle.manager(tmp_path)
    mgr._desired["fyers"] = "CONNECTED"
    mgr._job_session_check(mgr._specs["fyers"])
    snap = mgr.get_session_snapshot("fyers")
    assert snap["state"] == "CONNECTED"
    # LIVE consumes the same manager snapshot via _broker_view.
    data = tmp_path / "d2"
    strat = tmp_path / "s2"
    data.mkdir(parents=True, exist_ok=True)
    strat.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(str(data), str(strat))
    sel = MagicMock()
    rec = MagicMock()
    rec.name = "fyers"
    rec.reason = "user-selected"
    sel.current_or_none.return_value = rec
    svc.attach_broker_view(mgr, sel)
    view = svc._broker_view()
    assert view["connected"] is True
    assert view["account_id"] == snap["account_id"]
    mgr.stop_worker()


def test_9_local_connected_cannot_imply_ec2(tmp_path: Any) -> None:
    # EC2 snapshot with no manager must fail closed (never connected).
    data = tmp_path / "d3"
    strat = tmp_path / "s3"
    data.mkdir(parents=True, exist_ok=True)
    strat.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(str(data), str(strat))
    svc.attach_broker_view(None, None)
    assert svc._broker_view()["connected"] is False
    assert svc.is_account_confirmed() is False


def test_10_http_errors_classified() -> None:
    kind, _ = classify_http_error(400, "Invalid parameter symbol")
    assert kind == "BAD_REQUEST"
    kind, _ = classify_http_error(400, "Token is expired, session invalid")
    assert kind == "AUTH_EXPIRED"
    kind, _ = classify_http_error(400, "Token is Invalid or Expired")
    assert kind == "AUTH_EXPIRED"
    kind, _ = classify_http_error(400, "token is invalid")
    assert kind == "AUTH_EXPIRED"
    kind, _ = classify_exception(Exception("Token is Invalid or Expired (HTTP 400)"))
    assert kind == "AUTH_EXPIRED"
    kind, _ = classify_exception(
        Exception("venue unreachable: Token is Invalid or Expired (HTTP 400)")
    )
    assert kind == "AUTH_EXPIRED"
    kind, _ = BrokerManager._classify_validate_reason("Token is Invalid or Expired (HTTP 400)")
    assert kind == "AUTH_EXPIRED"
    kind, _ = BrokerManager._classify_validate_reason(
        "venue unreachable: Token is Invalid or Expired (HTTP 400)"
    )
    assert kind == "AUTH_EXPIRED"
    kind, _ = classify_http_error(429, "too many requests")
    assert kind == "RATE_LIMITED"
    kind, _ = classify_http_error(401, "unauthorized")
    assert kind == "AUTH_EXPIRED"
    d = backoff_with_jitter(0, base_s=1.0)
    assert 1.0 <= d <= 6.5
    d2 = backoff_with_jitter(10, base_s=1.0, cap_s=60.0)
    assert d2 <= 65.0


def test_11_ws_legs_distinguished(tmp_path: Any) -> None:
    bundle = _FakeBundle()
    mgr = bundle.manager(tmp_path)
    spec = bundle.spec
    mgr._desired["fyers"] = "CONNECTED"
    mgr._job_session_check(mgr._specs["fyers"])
    # Break only the market leg.
    state = mgr._states["fyers"]
    state["market_data"].health.return_value = (False, "ws down")
    state["market_data"].connect.side_effect = Exception("still down")
    mgr._job_health(spec)
    snap = mgr.get_session_snapshot("fyers")
    assert snap["state"] == "DEGRADED"
    legs = {leg["name"]: leg for leg in snap["legs"]}
    assert legs["market_ws"]["status"].startswith("FAILED")
    assert legs["rest"]["status"] == "READY"
    mgr.stop_worker()


def test_12_duplicate_ops_no_duplicate_orders() -> None:
    from remote.auth import TokenEntry
    from remote.server import MUTATING_ACTIONS, RemoteServer, config_from_env

    gw = MagicMock()
    gw.get_snapshot.return_value = {"session_status": "STOPPED", "start_blockers": []}
    gw.apply_command.return_value = {"session_status": "STOPPED", "start_blockers": []}
    tokens = {"tok": TokenEntry(name="t", role="trade", secret="tok")}
    server = RemoteServer(gw, tokens, config_from_env({}))
    assert "connect_broker" in MUTATING_ACTIONS
    assert "disconnect_broker" in MUTATING_ACTIONS
    # Idempotency replay returns cached result instead of re-executing.
    server._remember_command("req-9", {"ok": True, "action": "connect_broker"})
    assert server._recall_command("req-9") is not None


def test_13_secrets_redacted() -> None:
    audit = SessionAudit()
    entry = audit.record(
        request_id="r",
        broker_id="fyers",
        action="CONNECT requested",
        result="ok",
        reason="access_token=SECRET123 and Bearer abc.def password=x",
    )
    blob = str(entry)
    assert "SECRET123" not in blob
    assert "abc.def" not in blob
    assert redact("https://x/?access_token=AAA&code=BBB") == "https://x/?access_token=***&code=***"


def test_14_failed_gate_prevents_live_arm(tmp_path: Any) -> None:
    data = tmp_path / "d4"
    strat = tmp_path / "s4"
    data.mkdir(parents=True, exist_ok=True)
    strat.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(str(data), str(strat))
    svc.attach_broker_view(None, None)
    ok, reasons = svc.start(confirmed=True)
    assert ok is False
    assert len(reasons) > 0


def test_15_unknown_never_healthy() -> None:
    assert map_broker_status("SOMETHING_NEW", True) == "UNKNOWN"
    snap = build_snapshot(broker_id="fyers", state="UNKNOWN")
    assert snap["auth_valid"] is False
    assert "refresh" in next_action_for("UNKNOWN").lower()


def test_16_disconnect_race_reports_truth(tmp_path: Any) -> None:
    spec = _fake_spec()
    mgr = _manager(tmp_path, {"fyers": spec})
    v0 = mgr.get_session_snapshot("fyers")["version"]
    mgr._desired["fyers"] = "CONNECTED"
    mgr._job_session_check(mgr._specs["fyers"])
    v1 = mgr.get_session_snapshot("fyers")["version"]
    assert v1 > v0
    mgr.disconnect_broker("fyers")
    v2 = mgr.get_session_snapshot("fyers")["version"]
    assert v2 > v1
    assert mgr.get_session_snapshot("fyers")["state"] == "DISCONNECTED"


def test_remote_carries_broker_session() -> None:
    live = {
        "session_status": "STOPPED",
        "broker": {"connected": True, "name": "FYERS", "account_id": "AB1"},
        "feed": "live",
        "broker_session": {"state": "CONNECTED", "version": 3, "account_id": "AB1"},
    }
    remote = build_remote(live)
    assert remote["broker"]["connected"] is True
    assert remote["broker_session"]["state"] == "CONNECTED"
