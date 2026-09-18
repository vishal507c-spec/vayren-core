"""Broker — the Unified Broker Layer (Phase 20 M1–M3 + Phase 21 M4 + Phase 23 M6 + Phase 25 M8).

One coordination boundary between VAYREN and any venue:

- ``vocab`` — unified error vocabulary (``ErrorCode``) + typed errors.
- ``capabilities`` — ``CapabilitySet`` + capability ids across three domains
  (historical_data / market_data / trading) + tri-state ``CapabilityStatus``
  (M8: SUPPORTED / NOT_SUPPORTED / NOT_CONFIGURED).
- ``faces`` — the three protocol faces + ``BrokerPlugin`` implementations.
- ``funds`` — UBL-owned funds/margin abstraction (M6): frozen
  ``FundsSnapshot`` + ``require_funds`` capability gate over the existing
  ``TradingFace.funds()`` dict contract.
- ``credentials`` — production-safe credential boundary (M8): key-reference
  based, environment-aware, redacted; values never in selection/logs/UI.
- ``health`` — generic connection-health model (M8): ``HealthState`` +
  immutable ``BrokerHealth``; health never implies LIVE readiness.
- ``identity`` — one typed broker identity (FINAL): broker_id /
  display_name / environment / account_id, for display/audit/journal only.
- ``registry`` — the ONLY name→plugin map.
- ``selection`` — the ``BrokerSelection`` contract (single source of truth).
- ``selection_store`` — file-backed persistence for that selection (M4).
- ``auth`` — universal broker authentication contract (``BrokerAuthContract``,
  ``AuthCapability`` schema vocabulary, secret-free results/states).

Fail-closed by design: unknown broker → :class:`BrokerNotRegisteredError`,
unsupported capability → :class:`UnsupportedCapabilityError`, corrupt
selection file → :class:`SelectionLoadError`. No concrete broker SDK is ever
imported here; adapters live in isolated packages.

Scope (per ``90_brain/broker_layer_design.md``): M1–M3 vocabulary,
capability model, faces, registry with Paper/Sandbox/Zerodha-history
wrappers, selection contract; M4 file-backed selection store; M6
funds surface (Paper/Sandbox cash-only, Zerodha history-only).
NO real broker integration (M5+ done for identity, M7+), NO LIVE.
"""

from broker.auth import (
    AccountIdentity,
    AuthCapability,
    AuthErrorCode,
    AuthField,
    AuthResult,
    BrokerAuthContract,
    ConnectionState,
    describe_schema,
    mask_secret,
    schema_keys,
)
from broker.capabilities import CapabilitySet, CapabilityStatus, Caps, Domain, capability_status
from broker.credentials import (
    CredentialMetadata,
    CredentialRef,
    CredentialResolver,
    CredentialScope,
    validate_metadata,
    validate_refs,
)
from broker.faces import (
    BrokerPlugin,
    FactoryPlugin,
    HistoricalFace,
    MarketDataFace,
    PluginLike,
    StaticPlugin,
    TradingFace,
)
from broker.funds import (
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
from broker.health import BrokerHealth, HealthState
from broker.identity import BrokerIdentity, identity_of
from broker.management import BrokerSpec
from broker.registry import (
    BrokerRecord,
    BrokerRegistry,
    DuplicateBrokerError,
    default_registry,
)
from broker.selection import (
    BrokerSelection,
    MemorySelectionStore,
    SelectionError,
    SelectionStore,
    surface_resolution,
    surface_status,
)
from broker.selection_store import FileSelectionStore, SelectionLoadError
from broker.status import READY_STATES, BrokerStatus
from broker.vocab import (
    BrokerError,
    BrokerNotRegisteredError,
    CredentialsNotReadyError,
    Environment,
    ErrorCode,
    UnsupportedCapabilityError,
    error_code_from_legacy,
)

__all__ = [
    "AccountIdentity",
    "AuthCapability",
    "AuthErrorCode",
    "AuthField",
    "AuthResult",
    "BrokerAuthContract",
    "BrokerError",
    "BrokerHealth",
    "BrokerIdentity",
    "BrokerNotRegisteredError",
    "BrokerPlugin",
    "BrokerRecord",
    "BrokerRegistry",
    "BrokerSelection",
    "BrokerSpec",
    "BrokerStatus",
    "Caps",
    "CapabilitySet",
    "CapabilityStatus",
    "ConnectionState",
    "CredentialsNotReadyError",
    "CredentialMetadata",
    "CredentialRef",
    "CredentialResolver",
    "CredentialScope",
    "Domain",
    "DuplicateBrokerError",
    "Environment",
    "ErrorCode",
    "FUNDS_AVAILABLE",
    "FUNDS_EQUITY",
    "FUNDS_UNKNOWN",
    "FUNDS_UNSUPPORTED",
    "FUNDS_USED",
    "FactoryPlugin",
    "FileSelectionStore",
    "FundsSnapshot",
    "HealthState",
    "HistoricalFace",
    "MarketDataFace",
    "MemorySelectionStore",
    "PluginLike",
    "READY_STATES",
    "SelectionError",
    "SelectionLoadError",
    "SelectionStore",
    "StaticPlugin",
    "TradingFace",
    "UnsupportedCapabilityError",
    "capability_status",
    "default_registry",
    "describe_schema",
    "error_code_from_legacy",
    "identity_of",
    "is_unknown",
    "is_unsupported",
    "mask_secret",
    "require_funds",
    "schema_keys",
    "surface_resolution",
    "surface_status",
    "validate_metadata",
    "validate_refs",
]
