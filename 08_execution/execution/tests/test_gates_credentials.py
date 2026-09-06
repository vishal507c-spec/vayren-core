"""Credentials + five live gates + account confirmation tests."""

from risk import RiskPolicy

from execution.broker.credentials import (
    BrokerCredentials,
    EnvCredentialStore,
    default_account_id,
    validate_credentials,
)
from execution.broker.gates import (
    ACCOUNT_CONFIRMED,
    BROKER_ADAPTER_READY,
    CREDENTIALS_READY,
    EXECUTION_SAFETY_ENABLED,
    GATE_NAMES,
    RISK_CONFIGURATION_VALID,
    confirm_account,
    evaluate_live_gates,
    format_gates_report,
    risk_configuration_valid,
)
from execution.broker.paper import PaperBroker
from execution.broker.sandbox import SandboxBroker


def test_secret_values_never_appear() -> None:
    store = EnvCredentialStore(
        env={"VAYREN_BROKER_API_KEY": "sekret-123", "VAYREN_BROKER_API_SECRET": "sekret-456"}
    )
    assert store.get("API_KEY") == "sekret-123"
    assert store.get("MISSING") is None
    assert EnvCredentialStore(env={}).get("API_KEY") is None
    creds = BrokerCredentials(
        account_id="a", environment="live", key_refs=("API_KEY", "API_SECRET")
    )
    ok, _ = validate_credentials(creds, store, require_secrets=True)
    assert ok
    blob = repr(creds) + format_gates_report(
        evaluate_live_gates(
            adapter=None,
            credentials=creds,
            credential_store=store,
            risk_policy=RiskPolicy(),
        )
    )
    assert "sekret-123" not in blob and "sekret-456" not in blob


def test_credential_validation_matrix() -> None:
    store = EnvCredentialStore(env={"VAYREN_BROKER_API_KEY": "k"})
    ok, _ = validate_credentials(
        BrokerCredentials(account_id="a", environment="sandbox"), None, require_secrets=False
    )
    assert ok
    ok, reasons = validate_credentials(BrokerCredentials(), None, require_secrets=False)
    assert not ok and any("account_id" in r for r in reasons)
    ok, reasons = validate_credentials(
        BrokerCredentials(account_id="a", environment="live", key_refs=("API_KEY",)),
        store,
        require_secrets=True,
    )
    assert ok
    ok, reasons = validate_credentials(
        BrokerCredentials(account_id="a", environment="live", key_refs=("NOPE",)),
        store,
        require_secrets=True,
    )
    assert not ok and any("NOPE" in r for r in reasons)
    ok, reasons = validate_credentials(
        BrokerCredentials(account_id="a", environment="sandbox"),
        store,
        require_secrets=True,
        expected_environment="live",
    )
    assert not ok and any("mismatch" in r for r in reasons)
    ok, _ = validate_credentials(
        BrokerCredentials(account_id="a", environment="live"), None, require_secrets=True
    )
    assert not ok
    assert default_account_id() == ""


def test_confirm_account_matrix() -> None:
    ok, _ = confirm_account(None, "a", "live")
    assert not ok
    sandbox = SandboxBroker(account_id="sbx-1")
    sandbox.connect()
    ok, _ = confirm_account(sandbox, "sbx-1", "sandbox")
    assert ok
    ok, reasons = confirm_account(sandbox, "other", "sandbox")
    assert not ok and any("mismatch" in r for r in reasons)
    # sandbox identity can never confirm as LIVE, even with matching id
    ok, reasons = confirm_account(sandbox, "sbx-1", "live")
    assert not ok and any("never be treated as LIVE" in r for r in reasons)
    paper = PaperBroker()
    paper.connect()
    ok, reasons = confirm_account(paper, "", "live")
    assert not ok  # paper reports no account_id


def test_risk_policy_validity() -> None:
    ok, _ = risk_configuration_valid(RiskPolicy())
    assert ok
    ok, reasons = risk_configuration_valid(RiskPolicy(max_position_qty=0.0))
    assert not ok and any("max_position_qty" in r for r in reasons)
    ok, reasons = risk_configuration_valid(RiskPolicy(max_order_qty=9999.0, max_position_qty=10.0))
    assert not ok and any("exceeds" in r for r in reasons)
    ok, reasons = risk_configuration_valid(RiskPolicy(max_notional=-5.0))
    assert not ok


def test_five_gates_all_fail_by_default() -> None:
    report = evaluate_live_gates(adapter=None, risk_policy=None, kill_halted=True)
    assert not report.ready
    assert [g.name for g in report.gates] == list(GATE_NAMES)
    assert len(report.failed()) == 5
    text = format_gates_report(report)
    assert text.startswith("LIVE NOT READY")
    for name in GATE_NAMES:
        assert name in text


def test_five_gates_all_pass_when_truly_ready() -> None:
    sandbox = SandboxBroker(account_id="sbx-1")
    sandbox.connect()
    store = EnvCredentialStore(env={"VAYREN_BROKER_API_KEY": "k", "VAYREN_BROKER_API_SECRET": "s"})
    creds = BrokerCredentials(
        account_id="sbx-1", environment="sandbox", key_refs=("API_KEY", "API_SECRET")
    )
    report = evaluate_live_gates(
        adapter=sandbox,
        credentials=creds,
        credential_store=store,
        expected_account_id="sbx-1",
        expected_environment="sandbox",
        risk_policy=RiskPolicy(),
        kill_halted=False,
    )
    assert report.ready
    assert report.failed() == ()
    assert format_gates_report(report).startswith("LIVE READY")


def test_gate_names_stable() -> None:
    assert GATE_NAMES == (
        BROKER_ADAPTER_READY,
        CREDENTIALS_READY,
        ACCOUNT_CONFIRMED,
        RISK_CONFIGURATION_VALID,
        EXECUTION_SAFETY_ENABLED,
    )
