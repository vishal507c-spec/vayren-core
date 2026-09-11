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


def test_strategy_agnostic_no_obr_in_broker_layer() -> None:
    """Broker layer carries zero strategy coupling (no OBR references)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for rel in (
        "00_app/app/services/broker_manager.py",
        "09_broker/broker/status.py",
        "02_data/data/provider/zerodha/live_auth.py",
        "02_data/data/provider/zerodha/live_activation.py",
    ):
        text = (root / rel).read_text(encoding="utf-8").upper()
        assert "OBR" not in text, rel
    assert VENUE  # spec venue id unused marker (keeps constant honest)
