"""Broker transport — protocol, venues, credentials, gates, mode-gated factory."""

from execution.broker.adapter import (
    BrokerAdapter,
    BrokerCapabilities,
    BrokerError,
    NotConfiguredError,
    adapter_supports,
)
from execution.broker.credentials import (
    BrokerCredentials,
    CredentialStore,
    EnvCredentialStore,
    default_account_id,
    validate_credentials,
)
from execution.broker.factory import register_adapter, resolve_broker
from execution.broker.gates import (
    ACCOUNT_CONFIRMED,
    BROKER_ADAPTER_READY,
    CREDENTIALS_READY,
    EXECUTION_SAFETY_ENABLED,
    GATE_NAMES,
    RISK_CONFIGURATION_VALID,
    GateResult,
    LiveGatesReport,
    confirm_account,
    evaluate_live_gates,
    format_gates_report,
    risk_configuration_valid,
)
from execution.broker.paper import PaperBroker
from execution.broker.readonly import ReadOnlyBroker
from execution.broker.resilience import (
    RateLimiter,
    ResilienceState,
    RetryKind,
    classify_retry,
    clock_drift_ok,
)
from execution.broker.sandbox import SandboxBroker

__all__ = [
    "BrokerAdapter",
    "BrokerCapabilities",
    "BrokerError",
    "NotConfiguredError",
    "adapter_supports",
    "PaperBroker",
    "SandboxBroker",
    "ReadOnlyBroker",
    "ResilienceState",
    "RateLimiter",
    "RetryKind",
    "classify_retry",
    "clock_drift_ok",
    "BrokerCredentials",
    "CredentialStore",
    "EnvCredentialStore",
    "default_account_id",
    "validate_credentials",
    "GATE_NAMES",
    "BROKER_ADAPTER_READY",
    "CREDENTIALS_READY",
    "ACCOUNT_CONFIRMED",
    "RISK_CONFIGURATION_VALID",
    "EXECUTION_SAFETY_ENABLED",
    "GateResult",
    "LiveGatesReport",
    "confirm_account",
    "evaluate_live_gates",
    "format_gates_report",
    "risk_configuration_valid",
    "register_adapter",
    "resolve_broker",
]
