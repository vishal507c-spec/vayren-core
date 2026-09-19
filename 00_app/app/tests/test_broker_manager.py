"""BrokerManager proofs — states, secure config, auth flow, registry, gates.

A scripted spec replaces the real Zerodha wiring (same _BrokerSpec shape),
so every manager path runs with zero network. Covers: config save/load
missing-field rejection, session check (no config / no session / expired /
valid), venue registration with the AUTHENTICATED instance, health checks
(read-only), interactive login through the manager, disconnect vs remove,
snapshot secret hygiene, and the LIVE gate consumption contract.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

import pytest
from broker import BrokerStatus
from broker.management import BrokerSpec
from PySide6.QtCore import QCoreApplication

from app.services.broker_manager import BrokerManager

VENUE = "spec-venue-live"


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {}

    def save(self, service: str, values: dict[str, str]) -> None:
        self.data[service] = dict(values)

    def load(self, service: str) -> dict[str, str] | None:
        return dict(self.data[service]) if service in self.data else None

    def delete(self, service: str) -> None:
        self.data.pop(service, None)


class FakeFlow:
    def __init__(self, spec: SpecHooks) -> None:
        self._spec = spec

    def validate(self, api_key: str, token: str) -> tuple[bool, str]:  # noqa: ARG002
        if not token:
            return False, "credentials missing"
        if self._spec.expired:
            return False, "session expired or invalid"
        return True, "authenticated as AB1234"


class FakeAdapter:
    _spec_ref: Any = None

    def __init__(self, api_key: str, token: str) -> None:
        self.api_key = api_key
        self.token = token
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def health(self) -> tuple[bool, str]:
        return (True, "ok") if not self._spec().failing else (False, "simulated drop")

    def account(self) -> dict:
        if self._spec().failing:
            raise RuntimeError("account unavailable")
        return {"account_id": "AB1234"}

    def funds(self) -> dict:
        return {"available": 100.0, "used": 0.0, "equity": 100.0}

    def positions(self) -> list:
        return []

    def open_orders(self) -> list:
        return []

    def _spec(self) -> SpecHooks:
        return self._spec_ref()


class SpecHooks:
    """Mutable knobs the tests flip between manager operations."""

    def __init__(self) -> None:
        self.expired = False
        self.failing = False
        self.registered: list[Any] = []
        self.unregistered = 0

    def _spec_ref(self) -> SpecHooks:
        return self


class _FakeMD:
    def health(self) -> tuple[bool, str]:
        return True, "quotes ok"


def _spec(hooks: SpecHooks) -> BrokerSpec:
    def adapter(api_key: str, token: str) -> FakeAdapter:
        a = FakeAdapter(api_key, token)
        a._spec_ref = lambda: hooks  # type: ignore[method-assign]
        return a

    def flow() -> FakeFlow:
        return FakeFlow(hooks)

    def register(adapter: Any, md: Any) -> tuple[bool, str]:
        hooks.registered.append((adapter, md))
        return True, "registered"

    def unregister() -> None:
        hooks.unregistered += 1

    return BrokerSpec(
        broker_id="zerodha",
        display_name="Zerodha",
        config_service="vayren:zerodha",
        session_service="vayren:zerodha:session",
        required_config=("api_key", "api_secret"),
        masked_config=("api_key", "api_secret"),
        build_adapter=adapter,
        build_market_data=lambda _api_key, _token: _FakeMD(),
        build_flow=flow,
        venue_register=register,
        venue_unregister=unregister,
        extra={"callback_port": 0},
        callback_port=9474,
        callback_url="http://127.0.0.1:9474/vayren/callback",
    )


@pytest.fixture()
def manager(qt_app: Any, tmp_path: Any):  # noqa: ARG001 (Qt app must exist first)
    QCoreApplication.instance()  # noqa: B018 — fixture ensures Qt app exists
    hooks = SpecHooks()
    store = FakeStore()
    mgr = BrokerManager(
        data_dir=tmp_path,
        credential_store=store,
        session_store_factory=lambda st, service: _FakeSessionStore(st, service),
    )
    mgr._specs["zerodha"] = _spec(hooks)
    mgr._states["zerodha"] = mgr._fresh_state(mgr._specs["zerodha"])
    yield mgr, hooks, store
    mgr.stop_worker()


class _FakeSessionStore:
    def __init__(self, store: FakeStore, service: str) -> None:
        self._store = store
        self._service = service

    def save_token(self, token: str, user_id: str = "") -> None:
        self._store.save(self._service, {"access_token": token, "user_id": user_id})

    def load_token(self) -> dict[str, str] | None:
        values = self._store.load(self._service)
        if not values or not values.get("access_token"):
            return None
        return dict(values)

    def clear(self) -> None:
        self._store.delete(self._service)


def _settle(mgr: BrokerManager) -> None:
    for _ in range(200):
        if mgr._worker._jobs.empty() and not mgr._worker.isRunning():
            break
        QCoreApplication.processEvents()
        import time

        time.sleep(0.01)
    QCoreApplication.processEvents()


def test_unconfigured_state_and_exact_reason(manager) -> None:
    mgr, _, _ = manager
    mgr.submit_check("zerodha")
    _settle(mgr)
    state = mgr.state("zerodha")
    assert state["status"] is BrokerStatus.NOT_CONFIGURED
    assert state["configured"] is False


def test_configure_requires_all_fields(manager) -> None:
    mgr, _, store = manager
    ok, reason = mgr.configure("zerodha", {"api_key": "K1234567890"})
    assert not ok and "api_secret" in reason
    ok, reason = mgr.configure(
        "zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"}
    )
    assert ok
    stored = store.data["vayren:zerodha"]
    assert stored["api_key"] == "K1234567890"


def test_session_check_expired_is_login_required(manager) -> None:
    mgr, hooks, store = manager
    store.save(
        "vayren:zerodha",
        {"api_key": "K1234567890", "api_secret": "S-super-secret"},
    )
    store.save("vayren:zerodha:session", {"access_token": "tok-old"})
    hooks.expired = True
    mgr.submit_check("zerodha")
    _settle(mgr)
    assert mgr.state("zerodha")["status"] is BrokerStatus.LOGIN_REQUIRED
    assert hooks.registered == []


def test_valid_session_activates_venue_instance(manager) -> None:
    mgr, hooks, store = manager
    store.save(
        "vayren:zerodha",
        {"api_key": "K1234567890", "api_secret": "S-super-secret"},
    )
    store.save("vayren:zerodha:session", {"access_token": "tok-fresh"})
    mgr.submit_check("zerodha")
    _settle(mgr)
    state = mgr.state("zerodha")
    assert state["status"] in (BrokerStatus.CONNECTED, BrokerStatus.ACCOUNT_NOT_READY)
    assert hooks.registered and hooks.registered[0][0].token == "tok-fresh"
    assert mgr.is_connected("zerodha")


def test_health_failure_marks_not_ready(manager) -> None:
    mgr, hooks, store = manager
    store.save("vayren:zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    store.save("vayren:zerodha:session", {"access_token": "tok-fresh"})
    hooks.failing = True
    mgr.submit_check("zerodha")
    _settle(mgr)
    state = mgr.state("zerodha")
    assert state["status"] is BrokerStatus.ACCOUNT_NOT_READY
    assert any("FAILED" in v for v in state["checks"].values())


def test_disconnect_keeps_config_clears_session(manager) -> None:
    mgr, hooks, store = manager
    store.save("vayren:zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    store.save("vayren:zerodha:session", {"access_token": "tok"})
    ok, _ = mgr.disconnect_broker("zerodha")
    assert ok
    assert hooks.unregistered == 1
    assert store.data.get("vayren:zerodha:session") is None
    assert store.data.get("vayren:zerodha") is not None
    assert mgr.state("zerodha")["status"] is BrokerStatus.LOGIN_REQUIRED


def test_remove_clears_everything(manager) -> None:
    mgr, hooks, store = manager
    store.save("vayren:zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    ok, _ = mgr.remove("zerodha")
    assert ok
    assert not store.data
    assert mgr.state("zerodha")["status"] is BrokerStatus.NOT_CONFIGURED


def test_snapshot_never_carries_secrets(manager) -> None:
    mgr, _, store = manager
    mgr.configure("zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    snap = mgr.snapshot()
    text = repr(snap)
    assert "S-super-secret" not in text
    card = snap["brokers"][0]
    assert card["api_key_masked"].startswith("K123")
    assert "api_secret" not in card


def test_interactive_login_updates_state(manager) -> None:
    mgr, hooks, store = manager
    store.save("vayren:zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})

    def fake_login(_flow, _api_key, _api_secret, session_store, **_kwargs):
        session_store.save_token("tok-new", "AB1234")
        return True, "connected: authenticated as AB1234"

    # interactive_login is the injectable seam (production wires the real
    # wait_for_login_token in the factory spec; tests script it here)
    mgr._specs["zerodha"].interactive_login = fake_login
    ok, _ = mgr.start_login("zerodha")
    assert ok
    _settle(mgr)
    assert mgr.state("zerodha")["status"] in (
        BrokerStatus.CONNECTED,
        BrokerStatus.ACCOUNT_NOT_READY,
    )
    assert store.data["vayren:zerodha:session"]["access_token"] == "tok-new"


def test_live_gate_consumes_manager_state(manager) -> None:
    """The LIVE service refuses LIVE when the manager says LOGIN_REQUIRED."""
    from types import SimpleNamespace

    from app.services.live_trading_service import LiveTradingService

    mgr, hooks, store = manager
    store.save("vayren:zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    store.save("vayren:zerodha:session", {"access_token": "tok-old"})
    hooks.expired = True
    mgr.submit_check("zerodha")
    _settle(mgr)
    service = LiveTradingService(
        data_dir=".",
        strategy_dir=".",
        selection_service=SimpleNamespace(
            current=lambda: SimpleNamespace(name="zerodha", environment="live", reason="test"),
            current_or_none=lambda: None,
        ),
        broker_manager=mgr,
    )
    blockers = service._live_blockers()
    assert any("not authenticated" in b and "LOGIN_REQUIRED" in b for b in blockers)


def test_snapshot_carries_safe_read_only_details(manager) -> None:
    """Health values are reduced to safe UI scalars (no secrets, no payloads)."""
    mgr, _, store = manager
    ok, _ = mgr.configure("zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    assert ok
    store.save("vayren:zerodha:session", {"access_token": "tok-fresh"})
    mgr.submit_check("zerodha")
    _settle(mgr)
    snap = mgr.snapshot()
    card = snap["brokers"][0]
    assert card["account_id"] == "AB1234"
    assert card["funds"] == {"available": 100.0, "used": 0.0, "total": 100.0}
    assert card["positions_open"] == 0
    assert card["orders_open"] == 0
    assert card["last_sync"]
    assert card["can_refresh"] is True
    assert "S-super-secret" not in repr(snap)
    assert "tok-fresh" not in repr(snap)


def test_health_failure_clears_unavailable_details(manager) -> None:
    mgr, hooks, store = manager
    store.save("vayren:zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    store.save("vayren:zerodha:session", {"access_token": "tok-fresh"})
    hooks.failing = True
    mgr.submit_check("zerodha")
    _settle(mgr)
    snap = mgr.snapshot()["brokers"][0]
    assert "FAILED" in snap["checks"]["connection"]
    assert snap["account_id"] == ""
    assert snap["last_sync"]


def test_disconnect_clears_details_keeps_config(manager) -> None:
    mgr, _, store = manager
    ok, _ = mgr.configure("zerodha", {"api_key": "K1234567890", "api_secret": "S-super-secret"})
    assert ok
    store.save("vayren:zerodha:session", {"access_token": "tok"})
    mgr.disconnect_broker("zerodha")
    snap = mgr.snapshot()["brokers"][0]
    assert snap["configured"] is True
    assert snap["account_id"] == ""
    assert snap["funds"] == {"available": None, "used": None, "total": None}


def test_strategy_agnostic_no_obr_in_broker_layer() -> None:
    """Broker layer carries zero strategy coupling (no OBR references)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for rel in (
        "00_app/app/services/broker_manager.py",
        "09_broker/broker/status.py",
        "02_data/data/provider/zerodha/live_auth.py",
        "02_data/data/provider/zerodha/live_activation.py",
        "02_data/data/provider/fyers/live_auth.py",
        "02_data/data/provider/fyers/session_adapter.py",
    ):
        text = (root / rel).read_text(encoding="utf-8").upper()
        assert "OBR" not in text, rel
    assert VENUE  # spec venue id unused marker (keeps constant honest)


# ── universal auth: second venue through the same manager, zero branching ──


def _fyers_shaped_spec(hooks: SpecHooks) -> BrokerSpec:
    """FYERS-shaped scripted spec: own storage keys, UI key map, redirect URI."""

    def adapter(app_id: str, token: str) -> FakeAdapter:
        a = FakeAdapter(app_id, token)
        a._spec_ref = lambda: hooks  # type: ignore[method-assign]
        return a

    def flow() -> FakeFlow:
        return FakeFlow(hooks)

    def register(adapter: Any, md: Any) -> tuple[bool, str]:
        hooks.registered.append((adapter, md))
        return True, "registered"

    def unregister() -> None:
        hooks.unregistered += 1

    return BrokerSpec(
        broker_id="fyers",
        display_name="Fyers",
        config_service="vayren:fyers",
        session_service="vayren:fyers:session",
        required_config=("app_id", "secret"),
        masked_config=("app_id", "secret"),
        key_field="app_id",
        secret_field="secret",
        redirect_uri_field="redirect_uri",
        config_key_map={"api_key": "app_id", "api_secret": "secret"},
        build_adapter=adapter,
        build_market_data=None,
        build_flow=flow,
        build_session_store=lambda st, service: _FakeSessionStore(st, service),
        venue_register=register,
        venue_unregister=unregister,
        callback_port=9475,
        callback_url="http://127.0.0.1:9475/vayren/fyers-callback",
        extra={"venue_subtitle": "FYERS API v3"},
    )


def test_default_specs_cover_both_venues(manager) -> None:
    mgr, _, _ = manager
    assert mgr.broker_ids() == ("zerodha", "fyers")
    assert mgr.display_name("fyers") == "Fyers"
    snap = mgr.snapshot()
    assert [card["id"] for card in snap["brokers"]] == ["zerodha", "fyers"]
    by_id = {card["id"]: card for card in snap["brokers"]}
    assert by_id["zerodha"]["callback_url"] == "http://127.0.0.1:9474/vayren/callback"
    assert by_id["fyers"]["callback_url"] == "http://127.0.0.1:9475/vayren/fyers-callback"
    # Top-level URL stays the compatibility default (first venue).
    assert snap["callback_url"] == "http://127.0.0.1:9474/vayren/callback"


def test_fyers_shaped_configure_maps_ui_keys_to_storage_keys(manager) -> None:
    mgr, _, store = manager
    mgr._specs["fyers"] = _fyers_shaped_spec(SpecHooks())
    mgr._states["fyers"] = mgr._fresh_state(mgr._specs["fyers"])
    ok, reason = mgr.configure("fyers", {"api_key": "APP-1234567890"})
    assert not ok and "secret" in reason
    ok, reason = mgr.configure(
        "fyers", {"api_key": "APP-1234567890", "api_secret": "SHH-super-secret"}
    )
    assert ok, reason
    stored = store.data["vayren:fyers"]
    assert stored == {"app_id": "APP-1234567890", "secret": "SHH-super-secret"}
    snap = mgr.snapshot()
    text = repr(snap)
    assert "SHH-super-secret" not in text
    card = {entry["id"]: entry for entry in snap["brokers"]}["fyers"]
    assert card["api_key_masked"].startswith("APP-")
    assert "secret" not in card


def test_fyers_shaped_session_check_uses_own_key_field(manager) -> None:
    mgr, hooks, store = manager
    mgr._specs["fyers"] = _fyers_shaped_spec(hooks)
    mgr._states["fyers"] = mgr._fresh_state(mgr._specs["fyers"])
    store.save("vayren:fyers", {"app_id": "APP-1234567890", "secret": "SHH-secret"})
    store.save("vayren:fyers:session", {"access_token": "tok-fyers"})
    mgr.submit_check("fyers")
    _settle(mgr)
    state = mgr.state("fyers")
    assert state["status"] in (BrokerStatus.CONNECTED, BrokerStatus.ACCOUNT_NOT_READY)
    assert hooks.registered and hooks.registered[0][0].api_key == "APP-1234567890"
    assert mgr.is_connected("fyers")


def test_fyers_shaped_login_receives_redirect_uri(manager) -> None:
    mgr, _, store = manager
    mgr._specs["fyers"] = _fyers_shaped_spec(SpecHooks())
    mgr._states["fyers"] = mgr._fresh_state(mgr._specs["fyers"])
    store.save("vayren:fyers", {"app_id": "APP-1234567890", "secret": "SHH-secret"})
    seen: dict[str, Any] = {}

    def fake_login(_flow, _app_id, _secret, session_store, **kwargs):
        seen.update(kwargs)
        assert _app_id == "APP-1234567890"
        assert _secret == "SHH-secret"
        session_store.save_token("tok-fyers-new", "FY1234")
        return True, "connected: authenticated as FY1234"

    mgr._specs["fyers"].interactive_login = fake_login
    ok, _ = mgr.start_login("fyers")
    assert ok
    _settle(mgr)
    assert seen.get("port") == 9475
    assert seen.get("redirect_uri") == "http://127.0.0.1:9475/vayren/fyers-callback"
    assert store.data["vayren:fyers:session"]["access_token"] == "tok-fyers-new"


def test_real_fyers_spec_wires_without_network(manager) -> None:
    """Production FYERS spec through configure + session check (zero network:
    no session stored and the real spec's auto-auth reports the missing
    triple, so the manager stops at LOGIN_REQUIRED with the exact reason)."""
    mgr, _, store = manager
    ok, _ = mgr.configure("fyers", {"api_key": "APP-1", "api_secret": "S-1"})
    assert ok
    assert store.data["vayren:fyers"] == {"app_id": "APP-1", "secret": "S-1"}
    mgr.submit_check("fyers")
    _settle(mgr)
    state = mgr.state("fyers")
    assert state["status"] is BrokerStatus.LOGIN_REQUIRED
    assert state["configured"] is True
    assert "Client ID" in state["reason"]


def test_session_check_runs_wired_auto_auth(manager) -> None:
    mgr, hooks, store = manager
    spec = _fyers_shaped_spec(hooks)
    auto_calls: list[dict[str, str]] = []

    def fake_auto(config: dict[str, str], session_store: Any) -> tuple[bool, str]:
        auto_calls.append(dict(config))
        session_store.save_token("tok-auto", "FY1234")
        return True, "connected: authenticated as FY1234"

    spec.auto_authenticate = fake_auto
    mgr._specs["fyers"] = spec
    mgr._states["fyers"] = mgr._fresh_state(spec)
    store.save("vayren:fyers", {"app_id": "APP-1", "secret": "S-1"})
    mgr.submit_check("fyers")  # no session stored → auto-auth runs
    _settle(mgr)
    assert len(auto_calls) == 1
    assert auto_calls[0]["app_id"] == "APP-1"
    assert mgr.state("fyers")["status"] in (
        BrokerStatus.CONNECTED,
        BrokerStatus.ACCOUNT_NOT_READY,
    )


def test_failing_auto_auth_stays_login_required(manager) -> None:
    mgr, hooks, store = manager
    spec = _fyers_shaped_spec(hooks)

    def failing_auto(_config: dict[str, str], _session_store: Any) -> tuple[bool, str]:
        return False, "automatic login failed — check the log"

    spec.auto_authenticate = failing_auto
    mgr._specs["fyers"] = spec
    mgr._states["fyers"] = mgr._fresh_state(spec)
    store.save("vayren:fyers", {"app_id": "APP-1", "secret": "S-1"})
    mgr.submit_check("fyers")
    _settle(mgr)
    assert mgr.state("fyers")["status"] is BrokerStatus.LOGIN_REQUIRED


def test_exploding_auto_auth_never_kills_the_check(manager) -> None:
    mgr, hooks, store = manager
    spec = _fyers_shaped_spec(hooks)

    def exploding_auto(_config: dict[str, str], _session_store: Any) -> tuple[bool, str]:
        raise RuntimeError("boom")

    spec.auto_authenticate = exploding_auto
    mgr._specs["fyers"] = spec
    mgr._states["fyers"] = mgr._fresh_state(spec)
    store.save("vayren:fyers", {"app_id": "APP-1", "secret": "S-1"})
    mgr.submit_check("fyers")
    _settle(mgr)
    assert mgr.state("fyers")["status"] is BrokerStatus.LOGIN_REQUIRED


def test_real_fyers_spec_is_auth_only_and_registry_clean(manager) -> None:
    """Production FYERS spec: own keys, auth-only venue registration (the
    UBL registry gains no trading venue in this phase)."""
    from broker.registry import default_registry
    from data.provider.factory import fyers_management_spec

    spec = fyers_management_spec()
    assert spec.broker_id == "fyers"
    assert spec.required_config == ("app_id", "secret")
    assert spec.key_field == "app_id"
    assert spec.secret_field == "secret"
    assert spec.redirect_uri_field == "redirect_uri"
    assert spec.config_key_map == {"api_key": "app_id", "api_secret": "secret"}
    assert spec.callback_port == 9475
    assert "fyers-callback" in spec.callback_url
    assert spec.venue_register is not None
    ok, _ = spec.venue_register(object(), None)
    assert ok
    assert "fyers-live" not in default_registry()
    mgr, _, _ = manager
    assert "fyers" in mgr.broker_ids()


def test_login_passes_config_only_to_capable_callables() -> None:
    """Newer venue callables may accept ``config``; legacy ones keep their exact call."""
    from app.services.broker_manager import _accepts_keyword

    def legacy(_flow: Any, _key: str, _secret: str, _store: Any) -> bool:
        return True

    def tolerant(_flow: Any, _key: str, _secret: str, _store: Any, **_kwargs: Any) -> bool:
        return True

    def modern(
        _flow: Any, _key: str, _secret: str, _store: Any, config: dict | None = None
    ) -> bool:
        assert config is None or isinstance(config, dict)
        return True

    # Zerodha-shaped (strict positional): untouched historical call.
    assert _accepts_keyword(legacy, "config") is False
    assert _accepts_keyword(tolerant, "config") is True
    assert _accepts_keyword(modern, "config") is True
    assert _accepts_keyword(42, "config") is False
    # The real Zerodha seam keeps its exact signature (no config param).
    from data.provider.zerodha.live_auth import wait_for_login_token

    assert _accepts_keyword(wait_for_login_token, "config") is False
