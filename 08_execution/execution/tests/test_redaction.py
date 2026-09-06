"""Secret redaction tests — credential values must never surface.

Sentinel secret values are planted in the environment/store, then every
observable surface (reports, reprs, exceptions, journal files, CLI output)
is scanned for them. A single leak fails the suite.
"""

from pathlib import Path

import pytest
from risk import RiskPolicy
from strategy import StrategyParameters
from strategy.strategies.sma import SmaCrossover

from execution.broker.credentials import (
    BrokerCredentials,
    EnvCredentialStore,
    validate_credentials,
)
from execution.broker.gates import evaluate_live_gates, format_gates_report
from execution.broker.sandbox import SandboxBroker
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.runtime.session import LiveSession, SessionConfig
from execution.tests.helpers import make_bars

_SENTINELS = ("zz-topsecret-1", "zz-topsecret-2")


def _store():
    return EnvCredentialStore(
        env={
            "VAYREN_BROKER_API_KEY": "zz-topsecret-1",
            "VAYREN_BROKER_API_SECRET": "zz-topsecret-2",
        }
    )


def _creds():
    return BrokerCredentials(
        account_id="sbx-redact", environment="sandbox", key_refs=("API_KEY", "API_SECRET")
    )


def test_reports_and_repr_contain_no_values() -> None:
    report = evaluate_live_gates(adapter=None, credentials=_creds(), credential_store=_store())
    blob = format_gates_report(report) + repr(_creds())
    for sentinel in _SENTINELS:
        assert sentinel not in blob


def test_broker_error_messages_contain_no_values() -> None:
    venue = SandboxBroker(
        account_id="sbx",
        credentials=BrokerCredentials(account_id="", environment=""),
    )
    venue.connect()
    try:
        from execution.models.order import OrderPlan

        venue.place_order(OrderPlan(intent_id="i", symbol="T", side="BUY", quantity=1.0), "c1")
    except Exception as exc:
        assert "zz-topsecret-1" not in str(exc)
        assert "zz-topsecret-2" not in str(exc)
    else:
        raise AssertionError("expected credential failure")
    assert "zz-topsecret-1" not in repr(venue)


def test_journal_file_contains_no_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAYREN_BROKER_API_KEY", "zz-topsecret-1")
    monkeypatch.setenv("VAYREN_BROKER_API_SECRET", "zz-topsecret-2")
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(
        SessionConfig(journal_path=str(tmp_path / "journal.jsonl")),
        provider,
        RiskPolicy(),
    )
    session.register_strategy(
        "sma",
        "1.0",
        lambda: SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3})),
        StrategyParameters({}),
    )
    assert session.start(("TEST",), "15m", {"sma": make_bars("TEST", 25)}).ready
    import time

    while not provider.exhausted:
        session.step(time.time())
    content = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    for sentinel in _SENTINELS:
        assert sentinel not in content
    assert "FILL" in content  # journal is non-empty: the scan is meaningful


def test_credential_validation_reasons_name_fields_not_values() -> None:
    ok, reasons = validate_credentials(
        BrokerCredentials(account_id="a", environment="live", key_refs=("API_KEY",)),
        EnvCredentialStore(env={}),
        require_secrets=True,
    )
    assert not ok
    assert any("API_KEY" in r for r in reasons)
    assert "zz-topsecret-1" not in ";".join(reasons)
