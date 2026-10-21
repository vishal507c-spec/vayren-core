"""Regression tests for LIVE readiness truth (SYSTEM/LIVE auth consistency).

Pins the exact EC2 incident class:

* SYSTEM reported FYERS authenticated while LIVE still claimed
  ``broker status: CONFIGURING`` / ``no live trading venue`` — a stale
  failed-START reason outliving the facts it was computed from.
* LIVE consumed the raw transient ``BrokerStatus`` instead of the
  authoritative unified session, so a verdict computed mid-authentication
  was frozen by the validation cache.
* A data-only registration could satisfy the live venue lookup.

The broker session snapshot is SYSTEM's one auth truth; LIVE only consumes
it, and a refused START's reason is reconciled against the current verdict.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.live_trading_service import LiveConfigError, LiveTradingService
from broker.status import BrokerStatus
from execution.broker.adapter import BrokerAdapter
from execution.models.order import OrderPlan

_AUTH_BLOCKER = "not authenticated (broker session:"


class _FakeManager:
    """Minimal BrokerManager surface: unified session + raw status leg.

    The raw ``state()['status']`` deliberately stays a transient
    ``CONFIGURING``/``CONNECTED`` value: it is a different fact from the
    unified session and must never outrank it.
    """

    def __init__(
        self,
        *,
        session_state: str = "CONNECTED",
        auth_valid: bool = True,
        version: int = 1,
        adapter_connected: bool = True,
        raw_status: BrokerStatus = BrokerStatus.CONFIGURING,
    ) -> None:
        self.session: dict[str, object] = {
            "state": session_state,
            "auth_valid": auth_valid,
            "version": version,
            "account_id": "XV29244",
            "funds": {"available": 100.0, "used": 0.0, "total": 100.0},
        }
        self._adapter_connected = adapter_connected
        self._raw_status = raw_status

    def get_session_snapshot(self, broker_id: str) -> dict[str, object]:
        return dict(self.session) if broker_id else {}

    def is_connected(self, broker_id: str) -> bool:
        return bool(broker_id) and self._adapter_connected

    def state(self, broker_id: str) -> dict[str, object]:
        if not broker_id:
            return {}
        return {
            "status": self._raw_status,
            "configured": True,
            "reason": "verifying account…",
            "adapter": None,
            "market_data": None,
            "details": {"account_id": "XV29244", "funds_available": 100.0},
        }

    def snapshot(self) -> dict[str, object]:
        return {
            "active_broker": "fyers",
            "brokers": [
                {
                    "id": "fyers",
                    "display": "FYERS",
                    "connected": self._adapter_connected,
                    "configured": True,
                    "status": "CONNECTED" if self._adapter_connected else "DISCONNECTED",
                    "funds": {"available": 100.0},
                }
            ],
        }


class _TradingFace(BrokerAdapter):
    """A venue face that satisfies the order interface + connect/health."""

    @property
    def name(self) -> str:
        return "fyers-live"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ()

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def account(self) -> dict:
        return {}

    def positions(self) -> list:
        return []

    def open_orders(self) -> list:
        return []

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:
        _ = plan
        return f"oid-{client_order_id}"

    def cancel_order(self, broker_order_id: str) -> bool:
        _ = broker_order_id
        return True

    def modify_order(
        self, broker_order_id: str, quantity: float | None = None, price: float | None = None
    ) -> bool:
        _ = (broker_order_id, quantity, price)
        return True

    def stream_events(self) -> tuple:
        return ()

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        _ = (symbol, price, timestamp)
        return None


class _OrderStreamFace(_TradingFace):
    """Trading face exposing the bounded order-stream proof START requires."""

    def __init__(self, ws_result: tuple[bool, str]) -> None:
        self.ws_result = ws_result
        self.verify_calls = 0

    def verify_order_stream(self, timeout_s: float = 5.0) -> tuple[bool, str]:
        _ = timeout_s
        self.verify_calls += 1
        return self.ws_result


class _Plugin:
    def __init__(self, face: object) -> None:
        self._face = face
        self.domains: list[object] = []

    def face(self, domain: object) -> object:
        self.domains.append(domain)
        return self._face


class _Record:
    def __init__(self, face: object) -> None:
        self.plugin = _Plugin(face)


class _Registry:
    def __init__(self, records: dict[str, _Record]) -> None:
        self._records = records

    def get(self, name: str) -> _Record:
        if name in self._records:
            return self._records[name]
        raise KeyError(name)


@pytest.fixture()
def service(tmp_path) -> LiveTradingService:
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(str(data_dir), str(strat_dir))
    svc._universes.save("OBR C1C4", ("NSE:KAYNES",))
    svc.configure(
        strategy_name="OBR C1C4",
        symbols=("NSE:KAYNES",),
        timeframe="5m",
        mode="LIVE",
        quantity=1.0,
    )
    return svc


def _connected_view(*, account_id: str = "XV29244", available: float = 100.0) -> dict:
    return {
        "id": "fyers",
        "display": "FYERS",
        "connected": True,
        "status": "CONNECTED",
        "reason": "session verified",
        "account_id": account_id,
        "configured": True,
        "funds": {"available": available, "used": 0.0, "total": available},
    }


def _attach(service: LiveTradingService, manager: _FakeManager) -> None:
    service._broker_manager = manager
    service._selection = SimpleNamespace(current=lambda: SimpleNamespace(name="fyers"))


def test_connected_session_outranks_transient_raw_status(service: LiveTradingService) -> None:
    """SYSTEM connected (unified session) + raw status CONFIGURING must not
    produce an authentication blocker: the session is the auth truth."""
    _attach(service, _FakeManager(raw_status=BrokerStatus.CONFIGURING))
    service.confirm_account("XV29244")
    service.confirm_live_execution()
    with (
        patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"),
        patch.object(service, "_broker_view", return_value=_connected_view()),
        patch.object(service, "broker_name", return_value="fyers"),
        patch.object(service, "_compiled", return_value=object()),
        patch("execution.modes.gates_from_env") as gates,
    ):
        gates.return_value.missing.return_value = ()
        blockers = service._live_blockers()
    assert blockers == [], blockers
    assert not any("CONFIGURING" in b for b in blockers)


def test_unfinished_session_blocks_even_when_raw_status_says_connected(
    service: LiveTradingService,
) -> None:
    """The reverse mismatch: raw status CONNECTED while the unified session
    is still CONNECTING must block, with the session state named."""
    _attach(
        service,
        _FakeManager(
            session_state="CONNECTING",
            auth_valid=False,
            raw_status=BrokerStatus.CONNECTED,
        ),
    )
    with (
        patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"),
        patch.object(service, "broker_name", return_value="fyers"),
    ):
        blockers = service._live_blockers()
    assert any(_AUTH_BLOCKER in b and "CONNECTING" in b for b in blockers), blockers
    assert not any("broker status" in b for b in blockers)


def test_missing_auth_and_missing_venue_both_block_live(service: LiveTradingService) -> None:
    """Fail-closed: no authenticated session and no trading venue are two
    separate, exact blockers — neither is inferred from the other."""
    _attach(
        service,
        _FakeManager(session_state="DISCONNECTED", auth_valid=False, adapter_connected=False),
    )
    service.confirm_account("XV29244")
    service.confirm_live_execution()
    with (
        patch.object(service, "_resolve_live_venue_id", return_value=None),
        patch.object(service, "broker_name", return_value="fyers"),
        patch("execution.modes.gates_from_env") as gates,
    ):
        gates.return_value.missing.return_value = ()
        blockers = service._live_blockers()
    assert any(_AUTH_BLOCKER in b and "DISCONNECTED" in b for b in blockers), blockers
    assert any("no live trading venue" in b for b in blockers), blockers


def test_connection_only_session_cannot_pose_as_live_ready(service: LiveTradingService) -> None:
    """Session and capital can be READY while the adapter itself is not
    connected; that half-state must block instead of reporting ready."""
    _attach(service, _FakeManager(adapter_connected=False))
    service.confirm_account("XV29244")
    service.confirm_live_execution()
    with (
        patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"),
        patch.object(service, "_broker_view", return_value=_connected_view()),
        patch.object(service, "broker_name", return_value="fyers"),
        patch("execution.modes.gates_from_env") as gates,
    ):
        gates.return_value.missing.return_value = ()
        blockers = service._live_blockers()
    assert any("adapter is not connected" in b for b in blockers), blockers


def test_validate_cache_retires_pre_auth_verdict_when_session_completes(
    service: LiveTradingService,
) -> None:
    """The validation cache key rides the unified session version, so a
    verdict computed mid-authentication cannot outlive the authentication."""
    manager = _FakeManager(session_state="CONNECTING", auth_valid=False, version=1)
    _attach(service, manager)
    service.confirm_account("XV29244")
    service.confirm_live_execution()
    with (
        patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"),
        patch.object(service, "_broker_view", return_value=_connected_view()),
        patch.object(service, "broker_name", return_value="fyers"),
        patch.object(service, "_compiled", return_value=object()),
        patch("execution.modes.gates_from_env") as gates,
    ):
        gates.return_value.missing.return_value = ()
        first = service.validate()
        assert any(_AUTH_BLOCKER in b for b in first), first
        # Authentication completes — no explicit cache invalidation here:
        # the authoritative session (state + version) must carry it.
        manager.session.update({"state": "CONNECTED", "auth_valid": True, "version": 2})
        second = service.validate()
    assert not any(_AUTH_BLOCKER in b for b in second), second
    assert second == (), second


def test_failed_start_reason_is_retired_once_readiness_recovers(
    service: LiveTradingService,
) -> None:
    """The exact incident: START refused while the broker was CONNECTING and
    the venue was unregistered. After the broker authenticates and the venue
    registers, the stale reason must not keep contradicting the verdict."""
    manager = _FakeManager(session_state="CONNECTING", auth_valid=False, version=1)
    _attach(service, manager)
    service.confirm_account("XV29244")
    service.confirm_live_execution()

    with (
        patch.object(service, "_resolve_live_venue_id", return_value=None),
        patch.object(service, "_broker_view", return_value=_connected_view()),
        patch.object(service, "broker_name", return_value="fyers"),
        patch.object(service, "_compiled", return_value=object()),
        patch("execution.modes.gates_from_env") as gates,
    ):
        gates.return_value.missing.return_value = ()
        ok, reasons = service.start(confirmed=True)
        assert not ok
        assert any(_AUTH_BLOCKER in r for r in reasons), reasons
        assert any("no live trading venue" in r for r in reasons), reasons
        snap = service.snapshot()
        assert _AUTH_BLOCKER in snap["status_reason"]
        assert _AUTH_BLOCKER in snap["execution"]["reason"]

    # SYSTEM finishes the authentication and the live venue registers.
    manager.session.update({"state": "CONNECTED", "auth_valid": True, "version": 2})
    with (
        patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"),
        patch.object(service, "_broker_view", return_value=_connected_view()),
        patch.object(service, "broker_name", return_value="fyers"),
        patch.object(service, "_compiled", return_value=object()),
        patch("execution.modes.gates_from_env") as gates,
    ):
        gates.return_value.missing.return_value = ()
        snap = service.snapshot()

    assert snap["start_blockers"] == ()
    assert snap["status_reason"] == ""
    assert _AUTH_BLOCKER not in snap["execution"]["reason"]
    assert "no live trading venue" not in snap["execution"]["reason"]
    assert snap["execution"]["reason"] == "session not running"


def test_data_only_registration_is_not_a_live_venue(
    service: LiveTradingService, monkeypatch
) -> None:
    """A registered face that only advertises data must never satisfy the
    live venue lookup, even if its id looks live."""
    from broker import registry as broker_registry

    data_only = SimpleNamespace(connect=lambda: None, health=lambda: (True, "ok"))
    monkeypatch.setattr(
        broker_registry,
        "default_registry",
        lambda: _Registry({"fyers-live": _Record(data_only)}),
    )
    assert service._resolve_live_venue_id("fyers") is None


def test_trading_face_satisfies_live_venue_lookup(service: LiveTradingService, monkeypatch) -> None:
    """A real order interface + connect/health satisfies the lookup."""
    from broker import registry as broker_registry

    monkeypatch.setattr(
        broker_registry,
        "default_registry",
        lambda: _Registry({"fyers-live": _Record(_TradingFace())}),
    )
    assert service._resolve_live_venue_id("fyers") == "fyers-live"


def test_raw_broker_name_is_accepted_when_it_is_the_trading_face(
    service: LiveTradingService, monkeypatch
) -> None:
    """Some venues register the trading face under the plain broker name."""
    from broker import registry as broker_registry

    monkeypatch.setattr(
        broker_registry,
        "default_registry",
        lambda: _Registry({"fyers": _Record(_TradingFace())}),
    )
    assert service._resolve_live_venue_id("fyers") == "fyers"


def test_probe_live_venue_blocks_on_unverified_order_stream(
    service: LiveTradingService, monkeypatch
) -> None:
    """REST health alone cannot satisfy START; the order socket is proven."""
    from broker import registry as broker_registry

    face = _OrderStreamFace((False, "order stream not connected within 0.1s"))
    monkeypatch.setattr(
        broker_registry,
        "default_registry",
        lambda: _Registry({"fyers-live": _Record(face)}),
    )
    with pytest.raises(LiveConfigError, match="order stream not verified"):
        service._probe_live_venue("fyers-live")
    assert face.verify_calls == 1


def test_probe_live_venue_accepts_a_freshly_verified_order_stream(
    service: LiveTradingService, monkeypatch
) -> None:
    from broker import registry as broker_registry

    face = _OrderStreamFace((True, "connected — no order updates received yet"))
    monkeypatch.setattr(
        broker_registry,
        "default_registry",
        lambda: _Registry({"fyers-live": _Record(face)}),
    )
    md_faces = service._probe_live_venue("fyers-live")
    assert face.verify_calls == 1
    assert set(md_faces) == {"NSE:KAYNES"}
    assert service._account_state["ready"] is True


def test_attach_broker_view_triggers_reconcile_on_active_sessions(
    service: LiveTradingService,
) -> None:
    reconciled = []
    mock_session = SimpleNamespace(reconcile_now=lambda: reconciled.append(True))
    service._sessions = {"NSE:KAYNES": mock_session}  # type: ignore[dict-item]

    manager = _FakeManager(session_state="CONNECTED", auth_valid=True, version=10)
    service.attach_broker_view(manager, SimpleNamespace(active_broker=lambda: "fyers"))

    assert len(reconciled) == 1
