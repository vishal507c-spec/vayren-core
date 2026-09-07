"""M6 funds/margin tests: contract, capability, Paper/Sandbox, unsupported,
registry discovery, safety (design D8 resolution, locked M6 decisions).

Cash-only semantics: available/equity track live ``_capital``, ``used`` is
legitimately ``0.0`` (no margin engine). No network, no SDKs, no secrets.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
for entry in ("02_data", "07_risk", "08_execution", "09_broker"):
    sys.path.insert(0, str(ROOT / entry))

from execution.broker.factory import NotConfiguredError, resolve_broker  # noqa: E402
from execution.broker.gates import GATE_NAMES  # noqa: E402
from execution.broker.paper import PaperBroker  # noqa: E402
from execution.broker.sandbox import SandboxBroker  # noqa: E402
from execution.models.order import OrderPlan  # noqa: E402
from execution.modes import ExecutionMode, ModeGates  # noqa: E402

from broker.capabilities import Caps  # noqa: E402
from broker.funds import (  # noqa: E402
    FUNDS_AVAILABLE,
    FUNDS_EQUITY,
    FUNDS_UNKNOWN,
    FUNDS_UNSUPPORTED,
    FUNDS_USED,
    FundsSnapshot,
    is_unknown,
    is_unsupported,
    require_funds,
)
from broker.registry import default_registry  # noqa: E402
from broker.vocab import UnsupportedCapabilityError  # noqa: E402


def _plan(**overrides: object) -> OrderPlan:
    values: dict[str, object] = {
        "intent_id": "i1",
        "symbol": "RELIANCE",
        "side": "BUY",
        "quantity": 10.0,
        "order_type": "MARKET",
    }
    values.update(overrides)
    return OrderPlan(**values)  # type: ignore[arg-type]


def _paper_fill_run(capital: float = 1_000_000.0) -> dict[str, float]:
    broker = PaperBroker(capital=capital)
    broker.connect()
    broker.place_order(_plan(), "c1")
    assert broker.settle("c1", 100.0, "t") is not None
    return broker.funds()


# ── A. CONTRACT ──────────────────────────────────────────────────────────


def test_funds_dict_has_exact_keys() -> None:
    assert set(PaperBroker().funds()) == {"available", "used", "equity"}
    assert (FUNDS_AVAILABLE, FUNDS_USED, FUNDS_EQUITY) == ("available", "used", "equity")


def test_funds_values_are_floats() -> None:
    funds = PaperBroker().funds()
    assert all(type(value) is float for value in funds.values())


def test_snapshot_roundtrip() -> None:
    snapshot = FundsSnapshot(available=123.5, used=0.0, equity=123.5)
    clone = FundsSnapshot.from_dict(snapshot.to_dict())
    assert clone == snapshot
    assert clone.to_dict() == {"available": 123.5, "used": 0.0, "equity": 123.5}


def test_from_dict_rejects_missing_field() -> None:
    with pytest.raises(ValueError):
        FundsSnapshot.from_dict({"available": 1.0, "used": 0.0})


def test_snapshot_rejects_nan() -> None:
    with pytest.raises(ValueError):
        FundsSnapshot(available=math.nan, used=0.0, equity=1.0)
    with pytest.raises(ValueError):
        FundsSnapshot.from_dict({"available": math.nan, "used": 0.0, "equity": 1.0})


def test_snapshot_rejects_negative_money() -> None:
    with pytest.raises(ValueError):
        FundsSnapshot(available=-1.0, used=0.0, equity=0.0)
    with pytest.raises(ValueError):
        FundsSnapshot(available=1.0, used=-0.5, equity=1.0)


def test_legitimate_zero_accepted() -> None:
    snapshot = FundsSnapshot(available=0.0, used=0.0, equity=0.0)
    assert snapshot.to_dict() == {"available": 0.0, "used": 0.0, "equity": 0.0}


def test_unknown_distinct_from_zero() -> None:
    assert is_unknown(FUNDS_UNKNOWN)
    assert not is_unknown(0.0)
    assert not is_unknown(FUNDS_UNSUPPORTED)


def test_unsupported_distinct_from_zero() -> None:
    assert is_unsupported(FUNDS_UNSUPPORTED)
    assert not is_unsupported(0.0)
    assert not is_unsupported(FUNDS_UNKNOWN)


def test_unknown_distinct_from_unsupported() -> None:
    assert FUNDS_UNKNOWN is not FUNDS_UNSUPPORTED
    assert repr(FUNDS_UNKNOWN) != repr(FUNDS_UNSUPPORTED)


# ── B. CAPABILITY ────────────────────────────────────────────────────────


def test_paper_supports_account_funds() -> None:
    assert default_registry().get("paper").capabilities.supports(Caps.ACCOUNT_FUNDS)


def test_sandbox_supports_account_funds() -> None:
    assert default_registry().get("sandbox").capabilities.supports(Caps.ACCOUNT_FUNDS)


def test_zerodha_does_not_support_account_funds() -> None:
    import data.provider.factory  # noqa: F401 — seeds the zerodha record

    assert not default_registry().get("zerodha").capabilities.supports(Caps.ACCOUNT_FUNDS)


# ── C. PAPER ─────────────────────────────────────────────────────────────


def test_paper_initial_funds_default_capital() -> None:
    assert PaperBroker().funds() == {
        "available": 1_000_000.0,
        "used": 0.0,
        "equity": 1_000_000.0,
    }


def test_paper_custom_capital_respected() -> None:
    assert PaperBroker(capital=250_000.0).funds()["available"] == 250_000.0


def test_paper_used_is_zero() -> None:
    assert _paper_fill_run()["used"] == 0.0


def test_paper_equity_tracks_capital() -> None:
    broker = PaperBroker(capital=500_000.0)
    broker.connect()
    funds = broker.funds()
    assert funds["equity"] == funds["available"] == broker.capital == 500_000.0


def test_paper_fill_reduces_funds_via_existing_economics() -> None:
    broker = PaperBroker(capital=1_000_000.0)
    broker.connect()
    before = broker.funds()["available"]
    broker.place_order(_plan(quantity=100.0), "c1")
    fill = broker.settle("c1", 100.0, "t")
    assert fill is not None
    expected = before - (fill.fill_price * fill.fill_qty + fill.commission)
    assert broker.funds()["available"] == pytest.approx(expected)
    assert broker.funds()["available"] < before


def test_paper_funds_deterministic() -> None:
    assert _paper_fill_run() == _paper_fill_run()


# ── D. SANDBOX ───────────────────────────────────────────────────────────


def test_sandbox_initial_funds() -> None:
    assert SandboxBroker().funds() == {
        "available": 1_000_000.0,
        "used": 0.0,
        "equity": 1_000_000.0,
    }


def test_sandbox_account_identity_preserved() -> None:
    broker = SandboxBroker(account_id="sandbox-acct-007")
    broker.connect()
    assert broker.funds()["available"] == 1_000_000.0
    assert broker.account()["account_id"] == "sandbox-acct-007"


def test_sandbox_funds_deterministic() -> None:
    def run() -> dict[str, float]:
        broker = SandboxBroker()
        broker.connect()
        broker.place_order(_plan(), "c1")
        broker.settle("c1", 100.0, "t")
        return broker.funds()

    assert run() == run()


def test_sandbox_fill_effect_follows_capital() -> None:
    broker = SandboxBroker()
    broker.connect()
    before = broker.funds()["available"]
    broker.place_order(_plan(quantity=10.0), "c1")
    assert broker.settle("c1", 100.0, "t") is not None
    assert broker.funds()["available"] == pytest.approx(broker.capital)
    assert broker.funds()["available"] < before


# ── E. UNSUPPORTED ───────────────────────────────────────────────────────


def test_zerodha_funds_path_fails_closed() -> None:
    import data.provider.factory  # noqa: F401

    from broker.vocab import Domain

    record = default_registry().get("zerodha")
    with pytest.raises(UnsupportedCapabilityError):
        record.plugin.face(Domain.TRADING)
    with pytest.raises(UnsupportedCapabilityError):
        require_funds(record.plugin)


def test_unsupported_path_returns_no_fake_values() -> None:
    import data.provider.factory  # noqa: F401

    record = default_registry().get("zerodha")
    try:
        require_funds(record.plugin)
    except UnsupportedCapabilityError as exc:
        assert "0" not in str(exc.code)
        assert exc.code.value == "CAPABILITY_UNSUPPORTED"
    else:  # pragma: no cover — fail-closed must raise
        raise AssertionError("unsupported funds path must raise, never return values")


# ── F. REGISTRY ──────────────────────────────────────────────────────────


def test_registry_discovers_funds_capable_brokers() -> None:
    import data.provider.factory  # noqa: F401

    names = {record.name for record in default_registry().find_with(Caps.ACCOUNT_FUNDS)}
    assert "paper" in names
    assert "sandbox" in names


def test_registry_excludes_zerodha_from_funds() -> None:
    import data.provider.factory  # noqa: F401

    names = {record.name for record in default_registry().find_with(Caps.ACCOUNT_FUNDS)}
    assert "zerodha" not in names


# ── G. SAFETY ────────────────────────────────────────────────────────────


def test_live_gates_unchanged() -> None:
    assert GATE_NAMES == (
        "BROKER_ADAPTER_READY",
        "CREDENTIALS_READY",
        "ACCOUNT_CONFIRMED",
        "RISK_CONFIGURATION_VALID",
        "EXECUTION_SAFETY_ENABLED",
    )


def test_live_remains_not_configured() -> None:
    import data.provider.factory  # noqa: F401

    with pytest.raises(NotConfiguredError) as excinfo:
        resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="zerodha")
    assert "LIVE_BROKER_INTEGRATION = NOT_CONFIGURED" in str(excinfo.value)


def test_funds_creates_no_order() -> None:
    broker = PaperBroker()
    broker.connect()
    broker.funds()
    assert broker.open_orders() == []
    sandbox = SandboxBroker()
    sandbox.connect()
    sandbox.funds()
    assert sandbox.open_orders() == []


def test_funds_needs_no_credentials_or_network() -> None:
    paper = PaperBroker()
    paper.connect()
    assert paper.funds()["available"] == 1_000_000.0
    sandbox = SandboxBroker()  # default credentials, no store, no secrets
    sandbox.connect()
    assert sandbox.funds()["available"] == 1_000_000.0
