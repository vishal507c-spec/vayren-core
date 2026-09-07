"""M8 generic broker-adapter contract suite (production adapter framework).

One harness every future adapter can run: only genuinely implemented
capabilities are exercised, unsupported surfaces must fail closed with the
unified vocabulary. Paper/Sandbox satisfy the trading subset; Zerodha
satisfies the historical subset. No network, no SDKs, no secrets.
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
for entry in ("02_data", "07_risk", "08_execution", "09_broker"):
    sys.path.insert(0, str(ROOT / entry))

import data.provider.factory  # noqa: E402, F401 — seeds the zerodha record
from data.provider.contract import CANONICAL_INTERVALS, Provider  # noqa: E402
from data.provider.factory import build_provider  # noqa: E402
from data.settings import DownloadSettings  # noqa: E402
from execution.broker.adapter import BrokerAdapter, BrokerError  # noqa: E402
from execution.broker.credentials import (  # noqa: E402
    BrokerCredentials,
    EnvCredentialStore,
    validate_credentials,
)
from execution.broker.gates import GATE_NAMES  # noqa: E402
from execution.broker.paper import PaperBroker  # noqa: E402
from execution.broker.resilience import RetryKind, classify_retry  # noqa: E402
from execution.broker.sandbox import SandboxBroker  # noqa: E402
from execution.engine import ExecutionEngine, IllegalTransitionError  # noqa: E402
from execution.events import CandleEvent, HeartbeatEvent  # noqa: E402
from execution.market_data.normalizer import StreamNormalizer  # noqa: E402
from execution.market_data.replay import ReplayProvider, bars_to_candles  # noqa: E402
from execution.models.order import (  # noqa: E402
    TERMINAL_STATES,
    BrokerOrder,
    OrderPlan,
    OrderState,
)
from market import Bar  # noqa: E402

from broker.adapters.zerodha import BROKER_ID as ZERODHA_ID  # noqa: E402
from broker.adapters.zerodha import HISTORICAL_CAPABILITIES  # noqa: E402
from broker.capabilities import (  # noqa: E402
    CapabilitySet,
    CapabilityStatus,
    Caps,
    Domain,
    capability_status,
)
from broker.credentials import CredentialRef, CredentialScope, validate_refs  # noqa: E402
from broker.faces import MarketDataFace, TradingFace  # noqa: E402
from broker.funds import (  # noqa: E402
    FUNDS_UNKNOWN,
    FUNDS_UNSUPPORTED,
    FundsSnapshot,
    is_unknown,
    is_unsupported,
    require_funds,
)
from broker.health import (  # noqa: E402
    BrokerHealth,
    HealthState,
    health_from_legacy,
)
from broker.registry import default_registry  # noqa: E402
from broker.selection import BrokerSelection, surface_resolution, surface_status  # noqa: E402
from broker.selection_store import FileSelectionStore  # noqa: E402
from broker.vocab import (  # noqa: E402
    BrokerNotRegisteredError,
    Environment,
    ErrorCode,
    UnsupportedCapabilityError,
    error_code_from_legacy,
)

TS = "2026-09-07T00:00:00+05:30"


def _plan(**overrides: object) -> OrderPlan:
    values: dict[str, object] = {
        "intent_id": "m8-intent-1",
        "symbol": "RELIANCE",
        "side": "BUY",
        "quantity": 10.0,
        "order_type": "MARKET",
    }
    values.update(overrides)
    return OrderPlan(**values)  # type: ignore[arg-type]


def _bars() -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol="RELIANCE",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000 + i,
            timestamp=f"2026-09-0{(i % 5) + 1}T09:{15 + i:02d}:00+05:30",
        )
        for i in range(5)
    )


class _MapResolver:
    """In-memory CredentialResolver for contract tests (no env, no secrets)."""

    def __init__(
        self,
        values: dict[str, str] | None = None,
        environment: Environment | None = None,
    ) -> None:
        self._values = dict(values or {})
        self._environment = environment

    def resolve(self, ref: CredentialRef) -> str | None:
        return self._values.get(ref.key)

    def resolver_environment(self) -> Environment | None:
        return self._environment


# ── 1. identity ──────────────────────────────────────────────────────────


def test_paper_identity_is_stable() -> None:
    broker = PaperBroker()
    assert broker.name == "paper"
    assert isinstance(broker, BrokerAdapter)


def test_sandbox_identity_carries_environment() -> None:
    broker = SandboxBroker()
    assert broker.name == "sandbox"
    assert broker.account()["environment"] == "sandbox"
    assert isinstance(broker, BrokerAdapter)


def test_zerodha_identity_is_single_source() -> None:
    assert ZERODHA_ID == "zerodha"
    assert HISTORICAL_CAPABILITIES.items == frozenset({Caps.HIST_CANDLES, Caps.HIST_SYMBOLS})


# ── 2. capabilities + tri-state ──────────────────────────────────────────


def test_tri_state_supported_not_supported_not_configured() -> None:
    trading = CapabilitySet(domains=(Domain.TRADING,), items=frozenset({Caps.ORDERS_MARKET}))
    assert trading.status(Caps.ORDERS_MARKET) is CapabilityStatus.SUPPORTED
    assert trading.status(Caps.ORDERS_LIMIT) is CapabilityStatus.NOT_SUPPORTED
    assert trading.status("warp.drive") is CapabilityStatus.NOT_CONFIGURED
    assert capability_status(None, Caps.ORDERS_MARKET) is CapabilityStatus.NOT_CONFIGURED
    assert CapabilitySet().status(Caps.ORDERS_MARKET) is CapabilityStatus.NOT_CONFIGURED


def test_bool_helpers_unchanged_by_tristate() -> None:
    trading = CapabilitySet(domains=(Domain.TRADING,), items=frozenset({Caps.ORDERS_MARKET}))
    assert trading.supports(Caps.ORDERS_MARKET) is True
    assert trading.supports(Caps.ORDERS_LIMIT) is False
    assert trading.supports_domain(Domain.TRADING) is True
    assert trading.supports_domain(Domain.HISTORICAL_DATA) is False


def test_zerodha_historical_supported_trading_not_supported() -> None:
    assert HISTORICAL_CAPABILITIES.status(Caps.HIST_CANDLES) is CapabilityStatus.SUPPORTED
    assert HISTORICAL_CAPABILITIES.status(Caps.ORDERS_MARKET) is (CapabilityStatus.NOT_SUPPORTED)


def test_surface_status_matrix() -> None:
    sel = BrokerSelection(name="zerodha", environment=Environment.PAPER, selected_at=TS, reason="t")
    record = default_registry().get("zerodha")
    status, _ = surface_status(sel, record.capabilities, Domain.HISTORICAL_DATA)
    assert status is CapabilityStatus.SUPPORTED
    status, reason = surface_status(sel, record.capabilities, Domain.TRADING)
    assert status is CapabilityStatus.NOT_SUPPORTED
    assert "does not provide trading capability" in reason
    status, _ = surface_status(None, record.capabilities, Domain.TRADING)
    assert status is CapabilityStatus.NOT_CONFIGURED
    status, _ = surface_status(sel, None, Domain.TRADING)
    assert status is CapabilityStatus.NOT_CONFIGURED
    # Bool matrix stays compatible with the tri-state matrix.
    allowed, _ = surface_resolution(sel, record.capabilities, Domain.HISTORICAL_DATA)
    assert allowed is True
    allowed, _ = surface_resolution(sel, record.capabilities, Domain.TRADING)
    assert allowed is False


def test_unsupported_capability_fails_closed_no_fallback() -> None:
    record = default_registry().get("zerodha")
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        record.plugin.face(Domain.TRADING)
    assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED


def test_unknown_broker_fails_closed() -> None:
    with pytest.raises(BrokerNotRegisteredError) as excinfo:
        default_registry().get("ghost-venue-m8")
    assert excinfo.value.code is ErrorCode.NOT_REGISTERED


# ── 3. historical ────────────────────────────────────────────────────────


def test_zerodha_historical_face_serves_canonical_intervals(tmp_path: Path) -> None:
    assert set(CANONICAL_INTERVALS) == {"1m", "5m", "15m", "30m", "1h"}
    record = default_registry().get("zerodha")
    settings = DownloadSettings(data_dir=str(tmp_path), provider="zerodha")
    face = record.plugin.face(Domain.HISTORICAL_DATA, settings)
    assert face.capabilities() == HISTORICAL_CAPABILITIES  # type: ignore[attr-defined]
    ready, _why = face.available()  # type: ignore[attr-defined]
    assert isinstance(ready, bool)


def test_broker_interval_ids_never_leave_transport() -> None:
    import re

    sources = "".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "09_broker").rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts
    )
    assert "KITE_INTERVAL_IDS" not in sources
    kite_ids = re.findall(r'"(minute|day|week|hour|\d+minute|\d+hour)"', sources)
    assert kite_ids == [], f"broker interval strings in UBL: {kite_ids}"


# ── 4. market data ───────────────────────────────────────────────────────


def test_replay_subscribe_unsubscribe_poll() -> None:
    events = bars_to_candles(_bars(), "15m")
    provider = ReplayProvider(events)
    for method in ("open", "subscribe", "unsubscribe", "poll", "health", "close"):
        assert callable(getattr(provider, method)), f"replay missing {method}"
    provider.open(("RELIANCE",), "15m")
    provider.subscribe(("RELIANCE", "TCS"))
    assert provider._subscribed == ("RELIANCE", "TCS")
    provider.unsubscribe(("TCS",))
    assert provider._subscribed == ("RELIANCE",)
    first = provider.poll()
    assert len(first) == 1 and first[0].seq == 1
    provider.close()
    assert provider.poll() == ()


def test_market_events_are_normalized_domain_types() -> None:
    events = bars_to_candles(_bars(), "15m")
    assert all(isinstance(event, CandleEvent) for event in events)
    assert [event.seq for event in events] == [1, 2, 3, 4, 5]
    assert all(event.timestamp for event in events)


def test_normalizer_duplicate_out_of_order_stale() -> None:
    normalizer = StreamNormalizer()
    events = bars_to_candles(_bars(), "15m")
    assert len(normalizer.observe(events[0], 1000.0)) == 1
    assert normalizer.observe(events[0], 1001.0) == ()  # duplicate suppressed
    assert len(normalizer.observe(events[2], 1002.0)) == 0  # gap: seq 3 held
    delivered = normalizer.observe(events[1], 1003.0)  # seq 2 fills the hole
    assert [event.seq for event in delivered] == [2, 3]
    healthy, _ = normalizer.check_health("RELIANCE", 1004.0)
    assert healthy is True
    healthy, reason = normalizer.check_health("RELIANCE", 1004.0 + 10_000.0)
    assert healthy is False and "stale" in reason


def test_normalizer_heartbeat_and_disconnect() -> None:
    normalizer = StreamNormalizer()
    assert normalizer.observe(HeartbeatEvent("RELIANCE", TS, 1), 2000.0) == ()
    healthy, _ = normalizer.check_health("RELIANCE", 2001.0)
    assert healthy is True
    healthy, reason = normalizer.check_health("RELIANCE", 2000.0 + 10_000.0)
    assert healthy is False and "heartbeat" in reason


# ── 5. funds (M6 compat) ─────────────────────────────────────────────────


def test_paper_sandbox_funds_supported_and_live_values() -> None:
    paper = PaperBroker()
    paper.connect()
    funds = FundsSnapshot.from_dict(paper.funds())
    assert funds.used == 0.0 and funds.available == funds.equity > 0.0
    sandbox = SandboxBroker()
    sandbox.connect()
    sandbox_funds = FundsSnapshot.from_dict(sandbox.funds())
    assert sandbox_funds.available == sandbox_funds.equity > 0.0


def test_funds_unknown_unsupported_zero_never_collapse() -> None:
    assert is_unknown(FUNDS_UNKNOWN) and not is_unsupported(FUNDS_UNKNOWN)
    assert is_unsupported(FUNDS_UNSUPPORTED) and not is_unknown(FUNDS_UNSUPPORTED)
    assert FUNDS_UNKNOWN != 0.0 and FUNDS_UNSUPPORTED != 0.0
    assert capability_status(None, Caps.ACCOUNT_FUNDS) is CapabilityStatus.NOT_CONFIGURED
    assert HISTORICAL_CAPABILITIES.status(Caps.ACCOUNT_FUNDS) is (CapabilityStatus.NOT_SUPPORTED)


def test_zerodha_funds_not_supported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        face = build_provider(DownloadSettings(data_dir=tmp, provider="zerodha"))
        assert isinstance(face, Provider)
        with pytest.raises(UnsupportedCapabilityError):
            require_funds(face)


# ── 6. health ────────────────────────────────────────────────────────────


def test_health_states_generic_and_legacy_compatible() -> None:
    assert [s.value for s in HealthState] == [
        "DISCONNECTED",
        "CONNECTING",
        "CONNECTED",
        "DEGRADED",
        "AUTH_REQUIRED",
        "AUTH_FAILED",
        "RATE_LIMITED",
        "UNKNOWN",
    ]
    assert health_from_legacy(True, "ok").state is HealthState.CONNECTED
    assert health_from_legacy(False, "down").to_legacy() == (False, "down")
    assert BrokerHealth(state=HealthState.DEGRADED, reason="slow").connected is False
    with pytest.raises(ValueError):
        BrokerHealth(state="CONNECTED")  # type: ignore[arg-type]


def test_paper_health_roundtrips_through_generic_model() -> None:
    broker = PaperBroker()
    assert health_from_legacy(*broker.health()).state is HealthState.DISCONNECTED
    broker.connect()
    assert health_from_legacy(*broker.health()).state is HealthState.CONNECTED
    broker.disconnect()
    assert health_from_legacy(*broker.health()).state is HealthState.DISCONNECTED


def test_health_never_implies_live_readiness() -> None:
    assert GATE_NAMES == (
        "BROKER_ADAPTER_READY",
        "CREDENTIALS_READY",
        "ACCOUNT_CONFIRMED",
        "RISK_CONFIGURATION_VALID",
        "EXECUTION_SAFETY_ENABLED",
    )
    health = BrokerHealth(state=HealthState.CONNECTED, reason="venue reachable")
    assert health.connected is True  # connection only — gates still authoritative


# ── 7. positions / orders / cancel / modify / fills ──────────────────────


def test_paper_order_fill_position_lifecycle() -> None:
    broker = PaperBroker()
    broker.connect()
    broker_id = broker.place_order(_plan(), "m8-c1")
    assert broker_id.startswith("PAPER-")
    assert len(broker.open_orders()) == 1
    fill = broker.settle("m8-c1", 100.0, TS)
    assert fill is not None and fill.fill_qty == 10.0
    assert broker.positions() == [{"symbol": "RELIANCE", "quantity": 10.0}]
    assert broker.stream_events() != ()


def test_sandbox_cancel_modify_policies() -> None:
    broker = SandboxBroker()
    broker.connect()
    broker_id = broker.place_order(_plan(), "m8-s1")
    assert broker.modify_order(broker_id, quantity=5.0, price=None) is True
    assert broker.cancel_order(broker_id) is True
    assert broker.open_orders() == []


def test_sandbox_disconnect_blocks_orders() -> None:
    broker = SandboxBroker()
    broker.connect()
    broker.simulate_disconnect()
    healthy, _ = broker.health()
    assert healthy is False
    with pytest.raises(BrokerError):
        broker.place_order(_plan(), "m8-s9")


# ── 8. order lifecycle incl. modify states ───────────────────────────────


def test_lifecycle_states_cover_m8_contract() -> None:
    assert {s.value for s in OrderState} >= {
        "CREATED",
        "VALIDATED",
        "SUBMITTED",
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "REJECTED",
        "CANCEL_PENDING",
        "CANCELLED",
        "MODIFY_PENDING",
        "MODIFIED",
        "EXPIRED",
        "UNKNOWN",
    }
    assert sorted(state.value for state in TERMINAL_STATES) == [
        "CANCELLED",
        "EXPIRED",
        "FILLED",
        "REJECTED",
    ]


def test_modify_transitions_and_terminal_guards() -> None:
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="m8-m1", intent_id="m8-i1", symbol="X", side="BUY", quantity=1.0
    )
    engine.create(order)
    engine.transition("m8-m1", OrderState.VALIDATED)
    engine.transition("m8-m1", OrderState.SUBMITTED)
    engine.transition("m8-m1", OrderState.ACKNOWLEDGED)
    engine.transition("m8-m1", OrderState.MODIFY_PENDING)
    modified = engine.transition("m8-m1", OrderState.MODIFIED)
    assert modified.state is OrderState.MODIFIED
    engine.transition("m8-m1", OrderState.FILLED)
    with pytest.raises(IllegalTransitionError):
        engine.transition("m8-m1", OrderState.CANCELLED)  # terminal is terminal
    with pytest.raises(IllegalTransitionError):
        engine.transition("m8-m1", OrderState.VALIDATED)  # no backward edges


def test_unknown_only_exits_via_reconcile() -> None:
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="m8-u1", intent_id="m8-ui1", symbol="X", side="BUY", quantity=1.0
    )
    engine.create(order)
    engine.transition("m8-u1", OrderState.VALIDATED)
    engine.transition("m8-u1", OrderState.SUBMITTED)
    engine.transition("m8-u1", OrderState.UNKNOWN, reason="ack timeout")
    with pytest.raises(IllegalTransitionError):
        engine.transition("m8-u1", OrderState.FILLED)  # never blind-resolve
    resolved = engine.reconcile("m8-u1", "FILLED", broker_filled_qty=1.0)
    assert resolved.state is OrderState.FILLED and resolved.filled_qty == 1.0


# ── 9. idempotency ───────────────────────────────────────────────────────


def test_duplicate_client_id_and_intent_fail_closed() -> None:
    engine = ExecutionEngine()
    first = BrokerOrder(
        client_order_id="m8-d1", intent_id="m8-di1", symbol="X", side="BUY", quantity=1.0
    )
    engine.create(first)
    with pytest.raises(IllegalTransitionError):
        engine.create(first)  # duplicate client id
    clash = BrokerOrder(
        client_order_id="m8-d2", intent_id="m8-di1", symbol="X", side="BUY", quantity=1.0
    )
    with pytest.raises(IllegalTransitionError):
        engine.create(clash)  # duplicate intent
    broker = PaperBroker()
    broker.connect()
    broker.place_order(_plan(), "m8-dup")
    with pytest.raises(BrokerError) as excinfo:
        broker.place_order(_plan(), "m8-dup")
    assert excinfo.value.code == "DUPLICATE"
    assert error_code_from_legacy("DUPLICATE") is ErrorCode.DUPLICATE_ORDER


def test_trading_face_supports_optional_idempotency_key() -> None:
    params = inspect.signature(TradingFace.place_order).parameters
    assert "idempotency_key" in params
    assert params["idempotency_key"].default is None
    # Existing venues keep working without the key (backward compatible).
    broker = PaperBroker()
    broker.connect()
    assert broker.place_order(_plan(), "m8-ik1").startswith("PAPER-")


def test_retry_classification_reconcile_first() -> None:
    assert classify_retry("place_order") is RetryKind.NOT_SAFE_TO_RETRY
    assert classify_retry("cancel_order") is RetryKind.MUST_RECONCILE_FIRST
    assert classify_retry("modify_order") is RetryKind.MUST_RECONCILE_FIRST
    assert classify_retry("health") is RetryKind.SAFE_TO_RETRY
    assert classify_retry("something-new") is RetryKind.NOT_SAFE_TO_RETRY
    assert RetryKind.MUST_RECONCILE_FIRST is RetryKind.REQUIRES_RECONCILIATION


# ── 10. error mapping ────────────────────────────────────────────────────


def test_error_codes_normalize_both_layers() -> None:
    assert error_code_from_legacy("AUTHENTICATION_FAILED") is ErrorCode.AUTHENTICATION_FAILED
    assert error_code_from_legacy("RATE_LIMITED") is ErrorCode.RATE_LIMITED
    assert error_code_from_legacy("UNKNOWN_PROVIDER_ERROR") is ErrorCode.UNKNOWN
    assert error_code_from_legacy("SOMETHING_NEW") is ErrorCode.UNKNOWN
    assert error_code_from_legacy("NOT_CONNECTED") is ErrorCode.NOT_CONNECTED
    assert error_code_from_legacy("CREDENTIALS") is ErrorCode.CREDENTIALS_NOT_READY


# ── 11. credential redaction + env isolation ─────────────────────────────


def test_credential_refs_carry_no_values_and_redact() -> None:
    ref = CredentialRef(
        key="API_KEY", scope=CredentialScope.TRADING, environment=Environment.SANDBOX
    )
    assert "API_KEY" in repr(ref) and "secret" not in repr(ref).lower().replace(
        "secrets=<redacted>", ""
    )
    assert "value" not in CredentialRef.__dataclass_fields__


def test_validate_refs_missing_env_mismatch_scope() -> None:
    refs = (
        CredentialRef(
            key="API_KEY", scope=CredentialScope.TRADING, environment=Environment.SANDBOX
        ),
    )
    ok, reasons = validate_refs(refs, None)
    assert ok is False
    ok, reasons = validate_refs(refs, _MapResolver({"API_KEY": "v"}))
    assert ok is True, reasons
    ok, reasons = validate_refs(refs, _MapResolver({}))
    assert ok is False and any("API_KEY" in reason for reason in reasons)
    ok, reasons = validate_refs(
        refs, _MapResolver({"API_KEY": "v"}), expected_environment=Environment.LIVE
    )
    assert ok is False and any("live" in reason.lower() for reason in reasons)
    ok, reasons = validate_refs(refs, _MapResolver({"API_KEY": "v"}, environment=Environment.LIVE))
    assert ok is False and any("sandbox" in reason.lower() for reason in reasons)


def test_broker_credentials_redacted_and_env_separated() -> None:
    creds = BrokerCredentials(account_id="acct", environment="sandbox", key_refs=("API_KEY",))
    text = repr(creds)
    assert "acct" in text and "API_KEY" in text and "<redacted>" in text
    store = EnvCredentialStore(
        prefix="VAYREN_BROKER_TEST_M8_", env={"VAYREN_BROKER_TEST_M8_API_KEY": "s3cret"}
    )
    assert store.get("API_KEY") == "s3cret"
    ok, _ = validate_credentials(creds, store, require_secrets=True, expected_environment="sandbox")
    assert ok is True
    ok, reasons = validate_credentials(
        creds, store, require_secrets=True, expected_environment="live"
    )
    assert ok is False and any("mismatch" in reason for reason in reasons)


def test_selection_persistence_carries_no_secrets(tmp_path: Path) -> None:
    store = FileSelectionStore(tmp_path / "sel.json")
    store.save(
        BrokerSelection(
            name="sandbox", environment=Environment.SANDBOX, selected_at=TS, reason="user-selected"
        )
    )
    payload = json.loads((tmp_path / "sel.json").read_text(encoding="utf-8"))
    assert set(payload) == {"kind", "version", "name", "environment", "selected_at", "reason"}
    lowered = json.dumps(payload).lower()
    for secret_word in ("api_key", "secret", "password", "token", "totp", "s3cret"):
        assert secret_word not in lowered


def test_paper_sandbox_live_environments_distinct() -> None:
    assert Environment.PAPER != Environment.SANDBOX != Environment.LIVE
    paper_env = Environment.PAPER
    sandbox_creds = BrokerCredentials(account_id="s", environment="sandbox")
    ok, _ = validate_credentials(
        sandbox_creds, None, require_secrets=False, expected_environment=paper_env.value
    )
    assert ok is False  # cross-environment identity never validates


# ── 12. SDK isolation at the face boundary ───────────────────────────────


def test_ubl_faces_expose_only_domain_types() -> None:
    import typing

    for face, methods in (
        (
            TradingFace,
            (
                "capabilities",
                "account",
                "funds",
                "positions",
                "open_orders",
                "place_order",
                "cancel_order",
                "modify_order",
                "stream_events",
            ),
        ),
        (
            MarketDataFace,
            ("capabilities", "open", "subscribe", "unsubscribe", "poll", "health", "close"),
        ),
    ):
        for method in methods:
            assert hasattr(face, method), f"{face.__name__} missing {method}"
    hints: list[str] = []
    for face in (TradingFace, MarketDataFace):
        for _name, member in inspect.getmembers(face):
            if inspect.isfunction(member):
                try:
                    resolved = typing.get_type_hints(member)
                except Exception:
                    continue
                hints.extend(str(hint) for hint in resolved.values())
    blob = "\n".join(hints)
    for sdk_token in ("Kite", "kiteconnect", "Sdk", "Ticker", "Websocket"):
        assert sdk_token not in blob, f"SDK type leaks through UBL face: {sdk_token}"


def test_no_sdk_imports_in_ubl_package() -> None:
    import ast as _ast

    ubl = ROOT / "09_broker" / "broker"
    offenders = []
    for path in ubl.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            mod = ""
            if isinstance(node, _ast.Import):
                mod = " ".join(a.name for a in node.names)
            elif isinstance(node, _ast.ImportFrom) and node.module:
                mod = node.module
            if "kiteconnect" in mod or "httpx" in mod or "websocket" in mod:
                offenders.append(f"{path.relative_to(ROOT)} imports {mod}")
    assert not offenders
