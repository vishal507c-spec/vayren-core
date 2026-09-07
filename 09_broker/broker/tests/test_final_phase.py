"""FINAL phase UBL tests: identity, credential lifecycle, skeleton adapter,
market-data lifecycle surface (FINAL §C/§D/§F/§N, no future phases)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
for entry in ("09_broker",):
    sys.path.insert(0, str(ROOT / entry))

from broker.adapters.skeleton import BROKER_ID as SKELETON_ID  # noqa: E402
from broker.adapters.skeleton import (  # noqa: E402
    SkeletonHistorical,
    SkeletonMarketData,
    SkeletonTrading,
    skeleton_record,
)
from broker.capabilities import CapabilitySet, CapabilityStatus, Caps, Domain  # noqa: E402
from broker.credentials import (  # noqa: E402
    CredentialMetadata,
    CredentialRef,
    CredentialScope,
    validate_metadata,
    validate_refs,
)
from broker.faces import MarketDataFace, TradingFace  # noqa: E402
from broker.identity import BrokerIdentity, identity_of  # noqa: E402
from broker.registry import BrokerRegistry  # noqa: E402
from broker.selection import BrokerSelection, surface_status  # noqa: E402
from broker.vocab import Environment, ErrorCode, UnsupportedCapabilityError  # noqa: E402

TS = "2026-09-07T00:00:00+05:30"


class _MapResolver:
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


# ── §C: one typed identity ──────────────────────────────────────────────


def test_identity_distinguishes_venue_environment_account() -> None:
    identity = identity_of("zerodha", "Zerodha", Environment.PAPER, account_id="u1")
    assert identity.broker_id == "zerodha"
    assert identity.environment is Environment.PAPER
    assert identity.account_id == "u1"
    assert identity.describe() == "Zerodha (paper) [u1]"
    assert identity_of("paper", "Paper", Environment.PAPER).describe() == "Paper (paper)"


def test_identity_validation() -> None:
    with pytest.raises(ValueError):
        BrokerIdentity("", "X", Environment.PAPER)
    with pytest.raises(ValueError):
        BrokerIdentity("x", "", Environment.PAPER)
    with pytest.raises(ValueError):
        BrokerIdentity("x", "X", "live")  # type: ignore[arg-type]


# ── §D: credential lifecycle ─────────────────────────────────────────────


def test_broker_bound_ref_rejects_wrong_broker() -> None:
    refs = (
        CredentialRef(
            key="API_KEY",
            scope=CredentialScope.TRADING,
            environment=Environment.SANDBOX,
            broker="sandbox",
        ),
    )
    ok, _ = validate_refs(refs, _MapResolver({"API_KEY": "v"}), expected_broker="sandbox")
    assert ok is True
    ok, reasons = validate_refs(refs, _MapResolver({"API_KEY": "v"}), expected_broker="paper")
    assert ok is False and any("sandbox" in reason for reason in reasons)


def test_scope_mismatch_fails_closed() -> None:
    refs = (
        CredentialRef(key="K", scope=CredentialScope.HISTORICAL, environment=Environment.PAPER),
    )
    ok, reasons = validate_refs(
        refs, _MapResolver({"K": "v"}), expected_scope=CredentialScope.TRADING
    )
    assert ok is False and any("historical" in reason for reason in reasons)


def test_venue_agnostic_ref_has_no_broker() -> None:
    ref = CredentialRef(key="K", scope=CredentialScope.HISTORICAL, environment=Environment.PAPER)
    assert ref.broker is None
    ok, _ = validate_refs((ref,), _MapResolver({"K": "v"}), expected_broker="anything")
    assert ok is True  # unbound refs never broker-mismatch


def test_expiry_and_rotation_metadata() -> None:
    live = CredentialMetadata(key="K", expires_at="2030-01-01T00:00:00+00:00")
    assert live.is_expired(1_700_000_000.0) is False
    dead = CredentialMetadata(key="K", expires_at="2020-01-01T00:00:00+00:00")
    assert dead.is_expired(1_700_000_000.0) is True
    ok, reasons = validate_metadata((dead,), 1_700_000_000.0)
    assert ok is False and reasons == ("credential expired: K",)
    unknown = CredentialMetadata(key="K")
    assert unknown.is_expired(1_700_000_000.0) is False  # unknown never forces
    assert unknown.needs_rotation(1_700_000_000.0, 60.0) is False
    old = CredentialMetadata(key="K", rotated_at="2020-01-01T00:00:00+00:00")
    assert old.needs_rotation(1_700_000_000.0, 60.0) is True


# ── §N: skeleton reference adapter ───────────────────────────────────────


def test_skeleton_record_shape() -> None:
    record = skeleton_record()
    assert record.name == SKELETON_ID == "skeleton"
    assert set(record.faces) == {Domain.HISTORICAL_DATA, Domain.MARKET_DATA, Domain.TRADING}
    assert record.capabilities == CapabilitySet()  # undeclared → NOT_CONFIGURED
    assert record.capabilities.status(Caps.ORDERS_MARKET) is (CapabilityStatus.NOT_CONFIGURED)


def test_skeleton_faces_satisfy_protocols_but_refuse_work() -> None:
    import datetime

    record = skeleton_record()
    hist = record.plugin.face(Domain.HISTORICAL_DATA)
    assert isinstance(hist, SkeletonHistorical)
    assert hist.available() == (False, "skeleton transport NOT_CONFIGURED")
    with pytest.raises(UnsupportedCapabilityError):
        hist.fetch_candles("X", "15m", datetime.datetime.now(), datetime.datetime.now())
    md = record.plugin.face(Domain.MARKET_DATA)
    assert isinstance(md, SkeletonMarketData)
    assert md.poll() == () and md.health() == (False, "skeleton transport NOT_CONFIGURED")
    with pytest.raises(UnsupportedCapabilityError):
        md.open(("X",), "15m")
    trading = record.plugin.face(Domain.TRADING)
    assert isinstance(trading, SkeletonTrading)
    assert trading.environment is Environment.PAPER  # safest default, never LIVE
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        trading.place_order(object(), "c1", idempotency_key="k1")
    assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED
    with pytest.raises(UnsupportedCapabilityError):
        trading.funds()


def test_skeleton_never_registers_itself_live() -> None:
    registry = BrokerRegistry()
    assert SKELETON_ID not in registry
    assert "skeleton" not in {r.name for r in registry.list()}


def test_future_broker_registers_without_core_changes() -> None:
    """FINAL §O: a new venue needs only package + record + registration."""
    registry = BrokerRegistry()  # isolated: never touches the global default
    record = skeleton_record()
    registry.register(record)
    assert registry.get(SKELETON_ID).display_name == "Skeleton (reference template)"
    assert registry.find_with_domain(Domain.TRADING)[0].name == SKELETON_ID
    trading = registry.get(SKELETON_ID).plugin.face(Domain.TRADING)
    assert isinstance(trading, SkeletonTrading)
    registry.unregister(SKELETON_ID)
    assert SKELETON_ID not in registry


# ── §F: market-data lifecycle surface ────────────────────────────────────


def test_market_data_face_lifecycle_methods() -> None:
    for method in (
        "connect",
        "open",
        "subscribe",
        "unsubscribe",
        "poll",
        "health",
        "disconnect",
        "reconnect",
        "close",
    ):
        assert hasattr(MarketDataFace, method), f"MarketDataFace missing {method}"
    params = __import__("inspect").signature(TradingFace.place_order).parameters
    assert "idempotency_key" in params


def test_surface_status_config_awareness() -> None:
    sel = BrokerSelection(
        name="skeleton", environment=Environment.PAPER, selected_at=TS, reason="t"
    )
    status, _ = surface_status(sel, CapabilitySet(), Domain.TRADING)
    assert status is CapabilityStatus.NOT_CONFIGURED
    status, _ = surface_status(None, CapabilitySet(), Domain.TRADING)
    assert status is CapabilityStatus.NOT_CONFIGURED
