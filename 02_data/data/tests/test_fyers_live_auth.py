"""FYERS interactive auth proofs — official API v3 flow, scripted transport.

Covers: login URL shape (documented generate-authcode endpoint),
appIdHash vector, auth_code→token exchange, session validation (valid /
expired / network), secure session storage roundtrip, the localhost
callback server (capture + state + 404 + one-shot), and the full
``fyers_interactive_login`` round-trip with a stubbed browser. No real
network beyond loopback; secrets never echoed in messages.
"""

from __future__ import annotations

import hashlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from data.provider.credentials_store import FileCredentialStore
from data.provider.fyers.live_auth import (
    API_BASE,
    CALLBACK_PATH,
    AuthCodeCapture,
    AuthError,
    FyersAuthFlow,
    FyersSessionStore,
    app_id_hash,
    default_redirect_url,
    fyers_interactive_login,
    login_url,
    new_state,
)


class FakeTransport:
    """Scripted HTTP double for the three FYERS auth calls."""

    def __init__(self) -> None:
        self.last_post: tuple | None = None
        self.exchange_response: dict | None = None
        self.exchange_error: Exception | None = None
        self.profile_response: dict | None = None
        self.profile_error: Exception | None = None

    def post_json(  # noqa: ARG002 (scripted double keeps the real signature)
        self,
        url: str,
        payload: dict,
        headers: dict | None = None,  # noqa: ARG002
    ) -> dict:
        self.last_post = (url, dict(payload))
        if self.exchange_error is not None:
            raise self.exchange_error
        if self.exchange_response is not None:
            return dict(self.exchange_response)
        return {"s": "ok", "code": 200, "access_token": "tok-AC-1", "refresh_token": "ref-1"}

    def get_json(self, url: str, headers: dict | None = None) -> dict:  # noqa: ARG002
        if self.profile_error is not None:
            raise self.profile_error
        if self.profile_response is not None:
            return dict(self.profile_response)
        return {"s": "ok", "code": 200, "data": {"fy_id": "AB1234", "name": "Test"}}


def _flow() -> tuple[FyersAuthFlow, FakeTransport]:
    transport = FakeTransport()
    return FyersAuthFlow(transport), transport


def _free_port() -> int:
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_login_url_is_the_documented_endpoint() -> None:
    flow, _ = _flow()
    url = flow.login_url("APP-1", "http://127.0.0.1:9475/vayren/fyers-callback", "st-1")
    assert url.startswith(f"{API_BASE}/generate-authcode?")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["client_id"] == ["APP-1"]
    assert query["redirect_uri"] == ["http://127.0.0.1:9475/vayren/fyers-callback"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["st-1"]
    assert login_url("APP-1", "https://example.invalid/cb", "s") == flow.login_url(
        "APP-1", "https://example.invalid/cb", "s"
    )


def test_login_url_requires_app_id() -> None:
    flow, _ = _flow()
    try:
        flow.login_url("", "https://example.invalid/cb", "s")
        raise AssertionError("expected AuthError")
    except AuthError as exc:
        assert exc.code == "CONFIG"


def test_app_id_hash_matches_documented_scheme() -> None:
    expected = hashlib.sha256(b"APP-1:SECRET-9").hexdigest()
    assert app_id_hash("APP-1", "SECRET-9") == expected
    assert len(expected) == 64


def test_state_is_unpredictable() -> None:
    assert new_state() != new_state()
    assert default_redirect_url().startswith("http://127.0.0.1:9475")


def test_exchange_returns_session_without_storing_secret() -> None:
    flow, transport = _flow()
    session = flow.exchange("APP-1", "AUTH-1", "SECRET-XYZ")
    assert session["access_token"] == "tok-AC-1"
    assert session["refresh_token"] == "ref-1"
    url, payload = transport.last_post or ("", {})
    assert url == f"{API_BASE}/validate-authcode"
    assert payload["grant_type"] == "authorization_code"
    assert payload["code"] == "AUTH-1"
    assert payload["appIdHash"] == app_id_hash("APP-1", "SECRET-XYZ")
    assert "SECRET-XYZ" not in str(session)


def test_exchange_failure_is_normalized_and_secret_free() -> None:
    flow, transport = _flow()
    transport.exchange_response = {"s": "error", "code": -8, "message": "invalid code"}
    try:
        flow.exchange("APP-1", "AUTH-1", "SECRET-XYZ")
        raise AssertionError("expected AuthError")
    except AuthError as exc:
        assert exc.code == "EXCHANGE"
        assert "SECRET-XYZ" not in str(exc)
        assert "AUTH-1" not in str(exc)


def test_exchange_requires_all_inputs() -> None:
    flow, _ = _flow()
    for args in (("", "A", "S"), ("APP", "", "S"), ("APP", "A", "")):
        try:
            flow.exchange(*args)
            raise AssertionError("expected AuthError")
        except AuthError as exc:
            assert exc.code == "CONFIG"


def test_validate_maps_expired_vs_network() -> None:
    flow, transport = _flow()
    ok, reason = flow.validate("APP-1", "tok-1")
    assert ok and "AB1234" in reason

    transport.profile_response = {"s": "error", "code": -15, "message": "token expired"}
    ok, reason = flow.validate("APP-1", "tok-1")
    assert not ok and "expired" in reason

    transport.profile_response = {"s": "error", "code": -16, "message": "token invalid"}
    ok, reason = flow.validate("APP-1", "tok-1")
    assert not ok and "expired" in reason

    transport.profile_response = None
    transport.profile_error = AuthError("venue unreachable: boom", code="NETWORK")
    ok, reason = flow.validate("APP-1", "tok-1")
    assert not ok and "unreachable" in reason

    ok, reason = flow.validate("", "")
    assert not ok and "missing" in reason


def test_session_store_roundtrip(tmp_path) -> None:
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    assert store.load_token() is None
    store.save_token("tok-abc", "ref-abc", "APP-1")
    loaded = store.load_token()
    assert loaded is not None and loaded["access_token"] == "tok-abc"
    assert loaded["refresh_token"] == "ref-abc"
    assert loaded["app_id"] == "APP-1"
    store.clear()
    assert store.load_token() is None


def test_session_store_refuses_empty_token(tmp_path) -> None:
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    try:
        store.save_token("", "", "")
        raise AssertionError("expected AuthError")
    except AuthError as exc:
        assert exc.code == "BAD_TOKEN"


def test_callback_capture_roundtrip() -> None:
    capture = AuthCodeCapture()
    bound = capture.start(port=0)  # OS-picked port: hermetic
    assert bound > 0

    def hit() -> None:
        time.sleep(0.05)
        url = f"http://127.0.0.1:{bound}{CALLBACK_PATH}?auth_code=AC-9&state=st-9"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.status == 200

    thread = threading.Thread(target=hit)
    thread.start()
    query = capture.wait(timeout_s=5.0)
    thread.join(timeout=5)
    capture.stop()
    assert query is not None
    assert query["auth_code"] == "AC-9"
    assert query["state"] == "st-9"
    assert capture.wait(timeout_s=0.1) is None  # one-shot


def test_callback_unknown_path_404() -> None:
    capture = AuthCodeCapture()
    bound = capture.start(port=0)
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{bound}/nope", timeout=5)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        capture.stop()


def test_full_login_roundtrip_browser_stub(tmp_path) -> None:
    """Browser stub → redirect (state verified) → exchange → store → validate."""
    flow, _ = _flow()
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    opened: list[str] = []
    port = _free_port()
    result: dict = {}

    def browser(url: str) -> None:
        opened.append(url)
        # the "browser" performs the FYERS redirect back to VAYREN
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = params["state"][0]
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}{CALLBACK_PATH}?auth_code=AC-77&state={state}",
            timeout=5,
        )

    def run() -> None:
        result["out"] = fyers_interactive_login(
            flow,
            "APP-1",
            "SECRET-XYZ",
            store,
            port=port,
            redirect_uri="http://127.0.0.1:9475/vayren/fyers-callback",
            timeout_s=10.0,
            open_browser=browser,
        )

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=15.0)
    assert not thread.is_alive()
    ok, message = result["out"]
    assert ok, message
    assert "AB1234" in message
    assert opened and opened[0].startswith(f"{API_BASE}/generate-authcode")
    assert "SECRET-XYZ" not in message
    stored = store.load_token()
    assert stored is not None and stored["access_token"] == "tok-AC-1"
    assert stored["refresh_token"] == "ref-1"


def test_login_rejects_state_mismatch(tmp_path) -> None:
    flow, _ = _flow()
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    port = _free_port()

    def browser(_url: str) -> None:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}{CALLBACK_PATH}?auth_code=AC-1&state=wrong",
            timeout=5,
        )

    ok, message = fyers_interactive_login(
        flow, "APP-1", "SECRET-XYZ", store, port=port, timeout_s=10.0, open_browser=browser
    )
    assert not ok
    assert "match" in message
    assert store.load_token() is None


def test_login_reports_missing_auth_code(tmp_path) -> None:
    flow, _ = _flow()
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    port = _free_port()

    def browser(url: str) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = params["state"][0]
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}{CALLBACK_PATH}?state={state}",
            timeout=5,
        )

    ok, message = fyers_interactive_login(
        flow, "APP-1", "SECRET-XYZ", store, port=port, timeout_s=10.0, open_browser=browser
    )
    assert not ok
    assert "no auth_code" in message
    assert store.load_token() is None


def test_login_exchange_failure_is_secret_free(tmp_path) -> None:
    flow, transport = _flow()
    transport.exchange_response = {"s": "error", "code": -8, "message": "bad code"}
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    port = _free_port()

    def browser(url: str) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = params["state"][0]
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}{CALLBACK_PATH}?auth_code=AC-1&state={state}",
            timeout=5,
        )

    ok, message = fyers_interactive_login(
        flow, "APP-1", "SECRET-XYZ", store, port=port, timeout_s=10.0, open_browser=browser
    )
    assert not ok
    assert "SECRET-XYZ" not in message
    assert "AC-1" not in message
    assert store.load_token() is None


def test_login_requires_configuration(tmp_path) -> None:
    flow, _ = _flow()
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    ok, message = fyers_interactive_login(flow, "", "", store, port=_free_port())
    assert not ok
    assert "configured" in message
