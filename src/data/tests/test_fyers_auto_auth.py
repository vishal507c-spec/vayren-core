"""FYERS automatic-auth proofs — Zerodha-experience parity, scripted HTTP.

Covers: step helpers (OTP key / TOTP verify / PIN token / auth-code fetch
with defensive parsing), ``split_app_id`` vectors, ``ensure_session``
fast paths (valid stored / unconfigured / triple-incomplete — all with
zero vagator calls), the full automatic sequence (payload shapes,
appIdHash, Bearer usage, 6-digit TOTP, store + validate), failure mapping
(secret-free messages, store untouched), unreachable handling, and the
stdlib transport's browser User-Agent (FYERS edge 403s ``Python-urllib``).
No real network beyond loopback; secrets never echoed.
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from data.provider.credentials_store import FileCredentialStore
from data.provider.fyers.auto_auth import (
    FyersAutoAuthEngine,
    fetch_auth_code,
    request_otp_key,
    split_app_id,
    verify_pin_token,
    verify_totp_key,
)
from data.provider.fyers.credentials import FyersCredentials
from data.provider.fyers.live_auth import (
    USER_AGENT,
    AuthError,
    FyersSessionStore,
    _UrllibTransport,
)

APP_ID = "SPXXXXE7-100"
SECRET = "SHH-SECRET-9"
PIN = "908172"
FULL = FyersCredentials(APP_ID, SECRET, client_id="AB1234", totp_secret="JBSWY3DPEHPK3PXP", pin=PIN)


class FakeTransport:
    """Scripted HTTP double keyed by URL path fragment."""

    def __init__(self, script: dict[str, Any]) -> None:
        self._script = script
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append((url, dict(payload), dict(headers or {})))
        for fragment, outcome in self._script.items():
            if fragment in url:
                if isinstance(outcome, Exception):
                    raise outcome
                assert isinstance(outcome, dict)
                return dict(outcome)
        raise AssertionError(f"unexpected POST {url}")

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append((url, {}, dict(headers or {})))
        for fragment, outcome in self._script.items():
            if fragment in url:
                if isinstance(outcome, Exception):
                    raise outcome
                assert isinstance(outcome, dict)
                return dict(outcome)
        raise AssertionError(f"unexpected GET {url}")

    def paths(self) -> list[str]:
        return [call[0] for call in self.calls]


def _ok_profile() -> dict:
    return {"s": "ok", "code": 200, "data": {"fy_id": "AB1234", "name": "Test"}}


def _full_script() -> dict[str, object]:
    return {
        "/send_login_otp": {"request_key": "RK-1"},
        "/verify_otp": {"request_key": "RK-2"},
        "/verify_pin": {"s": "ok", "data": {"access_token": "TRADE-1"}},
        "/api/v3/token": {"Url": "http://127.0.0.1:9475/vayren/fyers-callback?auth_code=AC-1"},
        "/validate-authcode": {
            "s": "ok",
            "code": 200,
            "access_token": "TOK-1",
            "refresh_token": "REF-1",
        },
        "/profile": _ok_profile(),
    }


def _store(tmp_path, token: str = "") -> FyersSessionStore:
    store = FyersSessionStore(FileCredentialStore(tmp_path))
    if token:
        store.save_token(token, "REF-0", APP_ID)
    return store


def test_valid_stored_session_needs_no_vagator_calls(tmp_path) -> None:
    transport = FakeTransport({"/profile": _ok_profile()})
    ok, message = FyersAutoAuthEngine(FULL, transport).ensure_session(_store(tmp_path, "TOK-0"))
    assert ok and "AB1234" in message
    assert transport.paths() == [p for p in transport.paths() if p.endswith("/profile")]
    assert len(transport.paths()) == 1


def test_unconfigured_reports_pair_first(tmp_path) -> None:
    transport = FakeTransport({})
    ok, message = FyersAutoAuthEngine(FyersCredentials("", ""), transport).ensure_session(
        _store(tmp_path)
    )
    assert not ok and "App ID" in message
    assert transport.paths() == []


def test_triple_incomplete_names_missing_pieces_without_network(tmp_path) -> None:
    transport = FakeTransport({"/profile": {"s": "error", "code": -15, "message": "expired"}})
    creds = FyersCredentials(APP_ID, SECRET)  # pair only
    ok, message = FyersAutoAuthEngine(creds, transport).ensure_session(_store(tmp_path))
    assert not ok and "Client ID" in message
    assert all("vagator" not in path for path in transport.paths())


def test_full_automatic_sequence_shape(tmp_path) -> None:
    transport = FakeTransport(_full_script())
    store = _store(tmp_path, "TOK-OLD")  # expired below via script swap
    transport._script["/profile"] = {"s": "error", "code": -15, "message": "token expired"}

    # First call sees the expired profile, then the automatic flow runs.
    profile_calls = {"n": 0}
    real_get = transport.get_json

    def counting_get(url: str, headers: dict | None = None) -> dict:
        if url.endswith("/profile"):
            profile_calls["n"] += 1
            if profile_calls["n"] == 1:
                return {"s": "error", "code": -15, "message": "token expired"}
            return _ok_profile()
        return real_get(url, headers)

    transport.get_json = counting_get  # type: ignore[method-assign]
    ok, message = FyersAutoAuthEngine(FULL, transport).ensure_session(store)
    assert ok, message
    assert "AB1234" in message

    by_path = {call[0].rsplit("/", 1)[-1]: call for call in transport.calls}
    otp_call = by_path["send_login_otp"]
    assert otp_call[1] == {"fy_id": "AB1234", "app_id": "2"}
    totp_call = by_path["verify_otp"]
    assert re.fullmatch(r"\d{6}", totp_call[1]["otp"]) is not None
    assert totp_call[1]["request_key"] == "RK-1"
    pin_call = by_path["verify_pin"]
    assert pin_call[1]["identity_type"] == "pin"
    assert pin_call[1]["identifier"] == PIN
    token_call = by_path["token"]
    assert token_call[2].get("Authorization", "").startswith("Bearer TRADE-1")
    assert token_call[1]["app_id"] == "SPXXXXE7"
    assert token_call[1]["appType"] == "100"
    exchange_call = by_path["validate-authcode"]
    assert exchange_call[1]["code"] == "AC-1"
    assert len(exchange_call[1]["appIdHash"]) == 64

    stored = store.load_token()
    assert stored is not None and stored["access_token"] == "TOK-1"
    assert stored["refresh_token"] == "REF-1"
    # Secret hygiene: the user-facing message carries no secret values
    # (request payloads necessarily carry the PIN to the venue itself —
    # that is the login working, not a leak).
    for secret in (SECRET, "JBSWY3DPEHPK3PXP", PIN, "AC-1", "TRADE-1"):
        assert secret not in message


def test_exchange_failure_leaves_store_untouched(tmp_path) -> None:
    script = _full_script()
    script["/validate-authcode"] = {"s": "error", "code": -8, "message": "bad code"}
    transport = FakeTransport(script)
    store = _store(tmp_path)
    ok, message = FyersAutoAuthEngine(FULL, transport).ensure_session(store)
    assert not ok
    assert store.load_token() is None
    for secret in (SECRET, "AC-1"):
        assert secret not in message


def test_unreachable_short_circuits_without_burn(tmp_path) -> None:
    transport = FakeTransport({"/profile": AuthError("venue unreachable: boom", code="NETWORK")})
    ok, message = FyersAutoAuthEngine(FULL, transport).ensure_session(_store(tmp_path, "T"))
    assert not ok and "unreachable" in message
    assert len(transport.paths()) == 1  # probe only, no vagator burn


def test_step_helpers_reject_garbled_venue_output() -> None:
    garbled = {"s": "error", "code": -8, "message": "nope"}
    transport = FakeTransport(
        {
            "/send_login_otp": garbled,
            "/verify_otp": garbled,
            "/verify_pin": garbled,
            "/api/v3/token": garbled,
        }
    )
    with pytest.raises(AuthError):
        request_otp_key(transport, "AB1234")
    with pytest.raises(AuthError):
        verify_totp_key(transport, "RK", "123456")
    with pytest.raises(AuthError):
        verify_pin_token(transport, "RK", "1234")
    with pytest.raises(AuthError):
        fetch_auth_code(
            transport,
            fy_id="AB1234",
            app_id=APP_ID,
            redirect_uri="https://example.invalid/cb",
            trade_token="T",
        )


def test_split_app_id_vectors() -> None:
    assert split_app_id("SPXXXXE7-100") == ("SPXXXXE7", "100")
    assert split_app_id("NO-SUFFIX-") == ("NO-SUFFIX-", "100")
    assert split_app_id("PLAIN") == ("PLAIN", "100")


def test_stdlib_transport_sends_browser_user_agent() -> None:
    """FYERS edge 403s the default ``Python-urllib`` UA — every request
    must carry a browser-like one (loopback probe, no venue involved)."""
    seen: dict[str, str] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            seen["ua"] = self.headers.get("User-Agent", "")
            body = b'{"s": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            seen["ua-get"] = self.headers.get("User-Agent", "")
            body = b'{"s": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        http = _UrllibTransport()
        http.post_json(f"http://127.0.0.1:{port}/p", {"a": 1})
        http.get_json(f"http://127.0.0.1:{port}/g")
    finally:
        server.shutdown()
        server.server_close()
    assert "Python-urllib" not in seen.get("ua", "")
    assert "Python-urllib" not in seen.get("ua-get", "")
    assert "Mozilla" in seen.get("ua", "")
    assert seen.get("ua", "") == USER_AGENT
