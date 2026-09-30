"""Interactive Zerodha auth proofs — official flow, scripted SDK, real callback.

Covers: login URL generation, request_token→access_token exchange, session
validation (valid / expired / network), secure session storage roundtrip,
the localhost callback server (capture + 404 + stop), and the full
``wait_for_login_token`` round-trip with a stubbed browser. No real
network beyond loopback; no real-money anything.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

from data.provider.credentials_store import FileCredentialStore
from data.provider.zerodha.live_auth import (
    CALLBACK_PATH,
    AuthError,
    KiteAuthFlow,
    RequestTokenCapture,
    ZerodhaSessionStore,
    wait_for_login_token,
)


class FakeKite:
    """Scripted KiteConnect double for the three auth calls."""

    def __init__(self, api_key: str = "key123") -> None:
        self.api_key = api_key
        self.token: str | None = None
        self.profile_error: Exception | None = None
        self.exchange_error: Exception | None = None

    def login_url(self) -> str:
        return f"https://kite.zerodha.com/connect/login?api_key={self.api_key}"

    def set_access_token(self, token: str) -> None:
        self.token = token

    def generate_session(self, request_token: str, api_secret: str) -> dict:
        if self.exchange_error is not None:
            raise self.exchange_error
        if not request_token:
            raise ValueError("empty request_token")
        assert api_secret  # used once, never stored by VAYREN
        return {"access_token": f"tok-{request_token}", "user_id": "AB1234"}

    def profile(self) -> dict:
        if self.profile_error is not None:
            raise self.profile_error
        return {"user_id": "AB1234"}


class _TokenError(Exception):
    pass


class _NetworkError(Exception):
    pass


def _flow() -> tuple[KiteAuthFlow, list[FakeKite]]:
    made: list[FakeKite] = [FakeKite("key123")]

    def factory(api_key: str) -> FakeKite:
        made[0].api_key = api_key
        return made[0]

    return KiteAuthFlow(kite_factory=factory), made


def test_login_url_official_shape() -> None:
    flow, made = _flow()
    url = flow.login_url("key123")
    assert url.startswith("https://kite.zerodha.com/connect/login")
    assert made[0].api_key == "key123"


def test_exchange_returns_session_without_storing_secret() -> None:
    flow, _ = _flow()
    session = flow.exchange("key123", "req-1", "secret-xyz")
    assert session["access_token"] == "tok-req-1"
    assert session["user_id"] == "AB1234"
    assert "secret" not in str(session)


def test_exchange_failure_is_normalized() -> None:
    flow, made = _flow()
    made[0].exchange_error = _TokenError("bad request token")
    try:
        flow.exchange("key123", "req-1", "secret-xyz")
        raise AssertionError("expected AuthError")
    except AuthError as exc:
        assert exc.code == "EXCHANGE"
        assert "secret-xyz" not in str(exc)


def test_validate_maps_expired_vs_network() -> None:
    flow, made = _flow()
    ok, reason = flow.validate("key123", "tok-1")
    assert ok and "AB1234" in reason

    made[0].profile_error = _TokenError("invalid token")
    ok, reason = flow.validate("key123", "tok-1")
    assert not ok and "expired" in reason

    made[0].profile_error = _NetworkError("network timeout")
    ok, reason = flow.validate("key123", "tok-1")
    assert not ok and "unreachable" in reason


def test_session_store_roundtrip(tmp_path) -> None:
    store = ZerodhaSessionStore(FileCredentialStore(tmp_path))
    assert store.load_token() is None
    store.save_token("tok-abc", "AB1234")
    loaded = store.load_token()
    assert loaded is not None and loaded["access_token"] == "tok-abc"
    assert loaded["user_id"] == "AB1234"
    store.clear()
    assert store.load_token() is None


def test_callback_capture_roundtrip() -> None:
    capture = RequestTokenCapture()
    bound = capture.start(port=0)  # OS-picked port: hermetic
    assert bound > 0

    def hit() -> None:
        time.sleep(0.05)
        url = f"http://127.0.0.1:{bound}{CALLBACK_PATH}?status=success&request_token=RT-9"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.status == 200

    thread = threading.Thread(target=hit)
    thread.start()
    query = capture.wait(timeout_s=5.0)
    thread.join(timeout=5)
    capture.stop()
    assert query is not None
    assert query["request_token"] == "RT-9"
    assert query["status"] == "success"
    assert capture.wait(timeout_s=0.1) is None  # one-shot


def test_callback_unknown_path_404() -> None:
    capture = RequestTokenCapture()
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
    """Browser stub → redirect → exchange → store → validate: zero copy/paste."""
    flow, _ = _flow()
    store = ZerodhaSessionStore(FileCredentialStore(tmp_path))
    opened: list[str] = []
    # pick a free port deterministically: bind-close-reuse
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    result: dict = {}

    def browser(url: str) -> None:
        opened.append(url)
        # the "browser" performs the Zerodha redirect back to VAYREN
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}{CALLBACK_PATH}?status=success&request_token=RT-77",
            timeout=5,
        )

    def run() -> None:
        result["out"] = wait_for_login_token(
            flow, "key123", "secret-xyz", store, port=port, timeout_s=10.0, open_browser=browser
        )

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=15.0)
    assert not thread.is_alive()
    ok, message = result["out"]
    assert ok, message
    assert "AB1234" in message
    assert opened and opened[0].startswith("https://kite.zerodha.com")
    stored = store.load_token()
    assert stored is not None and stored["access_token"] == "tok-RT-77"


def test_login_failure_status_reported(tmp_path) -> None:
    flow, _ = _flow()
    store = ZerodhaSessionStore(FileCredentialStore(tmp_path))

    def browser(_url: str) -> None:
        import socket

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        urllib.request.urlopen(f"http://127.0.0.1:{port}{CALLBACK_PATH}?status=failure", timeout=5)

    # the failure redirect needs the SAME port the capture bound; use a
    # chosen port directly (retry a couple of times for robustness)
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    def browser2(_url: str) -> None:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{CALLBACK_PATH}?status=failure", timeout=5)

    ok, message = wait_for_login_token(
        flow, "key123", "secret-xyz", store, port=port, timeout_s=10.0, open_browser=browser2
    )
    assert not ok
    assert "failed login" in message
    assert store.load_token() is None
    assert browser  # first stub unused (kept for signature clarity)
