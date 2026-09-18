"""Universal broker authentication contract tests (broker-agnostic auth).

Proves the ``broker.auth`` vocabulary: capability declaration (never
assumed credentials), secret-free results, the session lifecycle, and
structural conformance of venue adapters to ``BrokerAuthContract``.
No network, no SDKs, no secrets.
"""

from __future__ import annotations

import sys
from pathlib import Path

BROKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER_DIR))

from broker.auth import (  # noqa: E402
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


class ScriptedAuthAdapter:
    """Minimal venue adapter shaped like a real one (Zerodha/FYERS parity)."""

    def __init__(self, schema: tuple[AuthField, ...]) -> None:
        self._schema = schema
        self._state = ConnectionState.DISCONNECTED
        self._session: dict[str, str] | None = None

    def broker_id(self) -> str:
        return "scripted"

    def display_name(self) -> str:
        return "Scripted"

    def authentication_schema(self) -> tuple[AuthField, ...]:
        return self._schema

    def auth_capabilities(self) -> tuple[AuthCapability, ...]:
        return (AuthCapability.API_KEY, AuthCapability.BROWSER_LOGIN)

    def connect(self) -> AuthResult:
        if self._session is None:
            return AuthResult(False, "scripted", "no session", AuthErrorCode.AUTH_REQUIRED.value)
        self._state = ConnectionState.CONNECTED
        return AuthResult(True, "scripted", "connected")

    def disconnect(self) -> AuthResult:
        self._session = None
        self._state = ConnectionState.DISCONNECTED
        return AuthResult(True, "scripted", "disconnected")

    def is_connected(self) -> bool:
        return self._state is ConnectionState.CONNECTED

    def connection_status(self) -> ConnectionState:
        return self._state

    def authenticate(self) -> AuthResult:
        self._session = {"access_token": "tok"}
        self._state = ConnectionState.CONNECTED
        return AuthResult(True, "scripted", "connected", account_id="AB1234")

    def refresh_session_if_supported(self) -> AuthResult:
        return AuthResult(
            False, "scripted", "refresh not supported", AuthErrorCode.SESSION_EXPIRED.value
        )

    def validate_credentials(self, values: dict[str, str]) -> str | None:
        for field in self._schema:
            if field.required and not values.get(field.key, "").strip():
                return f"{field.label} is required."
        return None

    def get_account_identity(self) -> AccountIdentity | None:
        if self._state is not ConnectionState.CONNECTED:
            return None
        return AccountIdentity(broker_id="scripted", account_id="AB1234", display_name="Scripted")

    def get_connection_metadata(self) -> dict[str, str]:
        return {"venue": "Scripted"}

    def secure_store_credentials(self, values: dict[str, str]) -> AuthResult:
        error = self.validate_credentials(values)
        if error is not None:
            return AuthResult(False, "scripted", error, AuthErrorCode.INVALID_CREDENTIALS.value)
        return AuthResult(True, "scripted", "configuration saved")

    def secure_store_session(self, session: dict[str, str]) -> AuthResult:
        if not session.get("access_token"):
            return AuthResult(False, "scripted", "empty session", AuthErrorCode.AUTH_REQUIRED.value)
        self._session = dict(session)
        return AuthResult(True, "scripted", "session stored")

    def load_session(self) -> dict[str, str] | None:
        return dict(self._session) if self._session else None

    def clear_session(self) -> AuthResult:
        self._session = None
        self._state = ConnectionState.DISCONNECTED
        return AuthResult(True, "scripted", "session cleared")


def _schema() -> tuple[AuthField, ...]:
    return (
        AuthField("app_id", "App ID", required=True, capabilities=("CLIENT_ID",)),
        AuthField("secret", "Secret", secret=True, required=True),
    )


def test_capability_vocabulary_covers_all_auth_models() -> None:
    names = {cap.value for cap in AuthCapability}
    for expected in (
        "API_KEY",
        "API_SECRET",
        "CLIENT_ID",
        "USERNAME",
        "PASSWORD",
        "TOTP",
        "ACCESS_TOKEN",
        "OAUTH",
        "AUTH_CODE",
        "REDIRECT_URI",
        "BROWSER_LOGIN",
        "SESSION_REFRESH",
        "CUSTOM",
    ):
        assert expected in names


def test_lifecycle_states_are_distinct() -> None:
    states = {state.value for state in ConnectionState}
    assert states == {
        "DISCONNECTED",
        "AUTH_REQUIRED",
        "AUTHENTICATING",
        "CONNECTED",
        "EXPIRED",
        "REAUTH_REQUIRED",
        "ERROR",
    }


def test_error_codes_are_canonical() -> None:
    codes = {code.value for code in AuthErrorCode}
    assert codes == {
        "AUTH_REQUIRED",
        "INVALID_CREDENTIALS",
        "AUTH_TIMEOUT",
        "REDIRECT_FAILED",
        "TOKEN_EXCHANGE_FAILED",
        "SESSION_EXPIRED",
        "CONNECTION_FAILED",
        "BROKER_UNAVAILABLE",
    }


def test_mask_secret_never_echoes_values() -> None:
    assert mask_secret("") == ""
    assert mask_secret("short") == "••••"
    masked = mask_secret("SUPER-SECRET-VALUE-12345")
    assert "SUPER-SECRET-VALUE-12345" not in masked
    assert masked.startswith("SUPE") and masked.endswith("45")


def test_schema_helpers_carry_no_values() -> None:
    schema = _schema()
    assert schema_keys(schema) == ("app_id", "secret")
    projected = describe_schema(schema)
    assert projected[0]["key"] == "app_id"
    assert projected[0]["label"] == "App ID"
    assert projected[1]["secret"] is True
    blob = str(projected)
    assert "SECRET-VALUE" not in blob


def test_auth_results_carry_no_secrets() -> None:
    result = AuthResult(True, "scripted", "connected", account_id="AB1234")
    blob = str(result)
    for token in ("SECRET", "token=", "password"):
        assert token not in blob


def test_scripted_adapter_satisfies_the_contract() -> None:
    adapter = ScriptedAuthAdapter(_schema())
    assert isinstance(adapter, BrokerAuthContract)
    assert adapter.broker_id() == "scripted"
    assert adapter.display_name() == "Scripted"
    assert [f.key for f in adapter.authentication_schema()] == ["app_id", "secret"]
    assert adapter.connection_status() is ConnectionState.DISCONNECTED
    assert not adapter.is_connected()
    assert adapter.get_account_identity() is None

    assert adapter.validate_credentials({"app_id": "ID-1"}) == "Secret is required."
    assert adapter.validate_credentials({"app_id": "ID-1", "secret": "S"}) is None

    outcome = adapter.authenticate()
    assert outcome.ok and outcome.account_id == "AB1234"
    assert adapter.is_connected()
    identity = adapter.get_account_identity()
    assert identity is not None and identity.account_id == "AB1234"

    assert not adapter.refresh_session_if_supported().ok
    assert adapter.disconnect().ok
    assert not adapter.is_connected()
    assert adapter.connect().error_code == AuthErrorCode.AUTH_REQUIRED.value

    assert adapter.secure_store_session({}).error_code == AuthErrorCode.AUTH_REQUIRED.value
    assert adapter.secure_store_session({"access_token": "tok"}).ok
    assert adapter.load_session() == {"access_token": "tok"}
    assert adapter.clear_session().ok
    assert adapter.load_session() is None
