"""Unified broker vocabulary — one error language for every face.

The historical layer speaks 7 ``ProviderError`` codes and the execution
layer speaks free-form ``BrokerError`` codes; both map onto
:class:`ErrorCode` here (see :func:`error_code_from_legacy`). Sentinels
(``TOKEN_EXPIRED``/``RATE_LIMITED``) deliberately stay in their owning
layer — their object identity drives the historical engine's control flow
and this package must not create a second copy.
"""

from __future__ import annotations

from enum import StrEnum


class Domain(StrEnum):
    """The three independent capability domains a broker may serve."""

    HISTORICAL_DATA = "historical_data"
    MARKET_DATA = "market_data"
    TRADING = "trading"


class Environment(StrEnum):
    """Account environment carried by trading faces and selections.

    Exactly two environments ever mattered to this architecture: a real
    LIVE broker, and the (now retired) simulated venues. Simulated venues are
    gone, so only LIVE remains; legacy ``paper``/``sandbox`` values are
    refused explicitly instead of being reinterpreted.
    """

    LIVE = "live"

    @classmethod
    def parse(cls, value: object) -> Environment:
        text = str(value or "").strip().lower()
        if text in ("paper", "sandbox"):
            raise ValueError(
                f"environment {text!r} was retired with simulated execution; "
                "only 'live' is supported"
            )
        try:
            return cls(text)
        except ValueError as exc:
            raise ValueError(
                f"unknown environment {value!r}; valid: {[e.value for e in cls]}"
            ) from exc


class ErrorCode(StrEnum):
    """Normalized broker-layer failure codes (design §4.1)."""

    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_CONNECTED = "NOT_CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    CREDENTIALS_NOT_READY = "CREDENTIALS_NOT_READY"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    NOT_REGISTERED = "NOT_REGISTERED"
    UNKNOWN = "UNKNOWN"


class BrokerError(RuntimeError):
    """A normalized UBL failure; ``code`` is always an :class:`ErrorCode`."""

    def __init__(self, message: str, code: ErrorCode = ErrorCode.UNKNOWN) -> None:
        super().__init__(message)
        try:
            self.code = ErrorCode(code)
        except ValueError:
            self.code = ErrorCode.UNKNOWN


class BrokerNotRegisteredError(BrokerError):
    """No plugin is registered under the requested name."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.NOT_REGISTERED)


class UnsupportedCapabilityError(BrokerError):
    """The broker exists but does not serve the requested capability."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.CAPABILITY_UNSUPPORTED)


class CredentialsNotReadyError(BrokerError):
    """Identity/secret resolution failed before any transport call."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.CREDENTIALS_NOT_READY)


# Legacy code → ErrorCode translation table (design §4.1: translation at the
# adapter edge). The data layer's 7 codes are a subset; the execution layer's
# historical free-form codes map where unambiguous and fall back to UNKNOWN.
_LEGACY_CODE_MAP: dict[str, ErrorCode] = {
    "AUTHENTICATION_FAILED": ErrorCode.AUTHENTICATION_FAILED,
    "RATE_LIMITED": ErrorCode.RATE_LIMITED,
    "INVALID_SYMBOL": ErrorCode.INVALID_SYMBOL,
    "NETWORK_ERROR": ErrorCode.NETWORK_ERROR,
    "PROVIDER_UNAVAILABLE": ErrorCode.PROVIDER_UNAVAILABLE,
    "INVALID_REQUEST": ErrorCode.INVALID_REQUEST,
    "UNKNOWN_PROVIDER_ERROR": ErrorCode.UNKNOWN,
    "BROKER_ERROR": ErrorCode.UNKNOWN,
    "NOT_CONNECTED": ErrorCode.NOT_CONNECTED,
    "DISCONNECTED": ErrorCode.DISCONNECTED,
    "DUPLICATE": ErrorCode.DUPLICATE_ORDER,
    "INVALID_ORDER": ErrorCode.INVALID_REQUEST,
    "CREDENTIALS": ErrorCode.CREDENTIALS_NOT_READY,
}


def error_code_from_legacy(legacy_code: str) -> ErrorCode:
    """Translate any legacy provider/broker code into the unified vocabulary.

    Unknown codes never get invented meanings — they map to
    :attr:`ErrorCode.UNKNOWN` (honest fallback, design §12 test 1).
    """
    return _LEGACY_CODE_MAP.get(str(legacy_code), ErrorCode.UNKNOWN)
