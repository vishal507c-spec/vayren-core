"""Zerodha interactive authentication — official flow, automated around it.

Implements exactly the Kite Connect authorization-code flow with ZERO
manual token copy/paste:

  1. ``KiteAuthFlow.login_url(api_key)`` builds the official Kite login
     URL (SDK-generated — never hand-constructed).
  2. The app opens it in the default browser (async, UI thread free).
  3. The user completes Zerodha's own login + 2FA on Zerodha's site.
     VAYREN never sees or automates credentials/OTP.
  4. Zerodha redirects to the Kite-app's registered redirect URL, which
     points at the local ``RequestTokenCapture`` server
     (``http://127.0.0.1:<port><CALLBACK_PATH>``). The server captures
     ``request_token`` from the redirect — the user copies nothing.
  5. ``exchange()`` swaps request_token → access_token (SDK call; the API
     secret is used once in-memory and never stored/logged).
  6. ``ZerodhaSessionStore`` persists the access_token through the
     platform CredentialStore (Windows Credential Manager on win32, file
     fallback elsewhere) — never in source, never in plain logs.
  7. ``validate()`` proves the token with one read-only ``profile()``
     call — an expired/invalid token is LOGIN_REQUIRED, never treated
     as a network error.

What is deliberately NOT here: password/TOTP automation, session-cookie
reuse, unofficial endpoints, silent token minting. Zerodha's own
authentication step stays with the user, always.
"""

from __future__ import annotations

import threading
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

DEFAULT_CALLBACK_HOST = "127.0.0.1"
DEFAULT_CALLBACK_PORT = 9474
CALLBACK_PATH = "/vayren/callback"
SESSION_SERVICE = "vayren:zerodha:session"


class AuthError(RuntimeError):
    """Normalized authentication failure (reasons carry no secret values)."""

    def __init__(self, message: str, code: str = "AUTH_ERROR") -> None:
        super().__init__(message)
        self.code = code


def redirect_url(port: int = DEFAULT_CALLBACK_PORT) -> str:
    """The URL the Kite Connect app must register as its redirect."""
    return f"http://{DEFAULT_CALLBACK_HOST}:{port}{CALLBACK_PATH}"


class ZerodhaSessionStore:
    """Access-token session persistence via the platform CredentialStore.

    Stored under its own service key so ``disconnect`` can clear the
    session without touching the broker's configured API credentials.
    """

    def __init__(self, store: Any, service: str = SESSION_SERVICE) -> None:
        self._store = store
        self._service = service

    def save_token(self, token: str, user_id: str = "") -> None:
        if not token:
            raise AuthError("refusing to store an empty access token", code="BAD_TOKEN")
        self._store.save(
            self._service,
            {"access_token": str(token), "user_id": str(user_id), "saved_at": _now()},
        )

    def load_token(self) -> dict[str, str] | None:
        values = self._store.load(self._service)
        if not isinstance(values, dict):
            return None
        token = str(values.get("access_token", "") or "")
        if not token:
            return None
        return {
            "access_token": token,
            "user_id": str(values.get("user_id", "") or ""),
            "saved_at": str(values.get("saved_at", "") or ""),
        }

    def clear(self) -> None:
        self._store.delete(self._service)


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()


class RequestTokenCapture:
    """One-shot localhost callback server for the Zerodha redirect.

    Runs ``http.server`` on a background thread; ``wait()`` blocks the
    CALLING (worker) thread only. The captured query dict carries
    ``request_token`` (and ``status`` on failure redirects). Tokens are
    never written to logs — the handler records the path, nothing else.
    """

    def __init__(self, path: str = CALLBACK_PATH) -> None:
        self._path = path
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._result: dict[str, str] | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._error = ""

    @property
    def error(self) -> str:
        return self._error

    def start(self, port: int = DEFAULT_CALLBACK_PORT) -> int:
        """Bind and serve; returns the actual bound port (0 → OS-picked)."""
        capture = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server API)
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != capture._path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = {str(k): str(v[-1]) for k, v in urllib.parse.parse_qs(parsed.query).items()}
                with capture._lock:
                    capture._result = query
                capture._done.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>VAYREN</h2><p>Login received."
                    b" You can close this tab and return to VAYREN.</p></body></html>"
                )

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass  # silent: default logging would echo request lines

        self._server = HTTPServer((DEFAULT_CALLBACK_HOST, port), _Handler)
        bound = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="vayren-callback", daemon=True
        )
        self._thread.start()
        return int(bound)

    def wait(self, timeout_s: float = 300.0) -> dict[str, str] | None:
        """Block until the redirect arrives or the timeout elapses.

        One-shot: the captured query is consumed by the first successful
        wait (a captured token must never be read twice).
        """
        if not self._done.wait(timeout_s):
            self._error = "login timed out — no callback received"
            return None
        with self._lock:
            result, self._result = self._result, None
        if result is None:
            return None
        return result

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self._done.set()


class KiteAuthFlow:
    """Official Kite Connect auth steps with an injectable SDK factory.

    ``kite_factory`` defaults to building a real ``KiteConnect`` client
    lazily; tests inject a scripted double. Every failure path returns a
    normalized reason without echoing the api_secret or tokens.
    """

    def __init__(self, kite_factory: Callable[[str], Any] | None = None) -> None:
        self._kite_factory = kite_factory

    def _client(self, api_key: str) -> Any:
        if self._kite_factory is not None:
            return self._kite_factory(api_key)
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise AuthError(
                "kiteconnect SDK not installed (pip install kiteconnect>=5)",
                code="NOT_CONFIGURED",
            ) from exc
        return KiteConnect(api_key=api_key)

    def login_url(self, api_key: str) -> str:
        """Official login URL from the SDK itself."""
        if not api_key:
            raise AuthError("API key required before login", code="CONFIG")
        return str(self._client(api_key).login_url())

    def exchange(self, api_key: str, request_token: str, api_secret: str) -> dict[str, Any]:
        """request_token → session payload (access_token + user_id)."""
        if not api_key or not request_token or not api_secret:
            raise AuthError("api_key, request_token and api_secret are all required", code="CONFIG")
        try:
            session = self._client(api_key).generate_session(request_token, api_secret=api_secret)
        except Exception as exc:
            raise AuthError(f"token exchange rejected by venue: {exc}", code="EXCHANGE") from None
        if not isinstance(session, dict):
            raise AuthError("venue returned an unreadable session", code="EXCHANGE")
        token = str(session.get("access_token", "") or "")
        if not token:
            raise AuthError("venue returned no access token", code="EXCHANGE")
        return {
            "access_token": token,
            "user_id": str(session.get("user_id", "") or ""),
        }

    def validate(self, api_key: str, access_token: str) -> tuple[bool, str]:
        """Read-only proof of a stored token (one ``profile()`` call)."""
        if not api_key or not access_token:
            return False, "credentials missing"
        try:
            client = self._client(api_key)
            client.set_access_token(access_token)
            profile = client.profile()
        except Exception as exc:
            text = str(exc)
            lowered = text.lower()
            if "token" in lowered and ("invalid" in lowered or "expired" in lowered):
                return False, "session expired or invalid"
            if "network" in lowered or "timeout" in lowered:
                return False, f"venue unreachable: {text}"
            return False, f"session check failed: {text}"
        user = str((profile or {}).get("user_id", "") or "") if isinstance(profile, dict) else ""
        if not user:
            return False, "venue returned no account identity"
        return True, f"authenticated as {user}"


def wait_for_login_token(
    flow: KiteAuthFlow,
    api_key: str,
    api_secret: str,
    store: ZerodhaSessionStore,
    *,
    port: int = DEFAULT_CALLBACK_PORT,
    timeout_s: float = 300.0,
    open_browser: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Complete one interactive login round-trip (blocking; worker threads only).

    Opens the browser (via ``open_browser``; default ``webbrowser.open``),
    captures the redirect, exchanges, stores, validates. Returns
    ``(ok, message)`` — the message is user-safe and secret-free.
    """
    import webbrowser

    capture = RequestTokenCapture()
    try:
        capture.start(port)
    except OSError as exc:
        return False, f"cannot open local callback port {port}: {exc}"
    try:
        url = flow.login_url(api_key)
        opener = open_browser or webbrowser.open
        opener(url)
        query = capture.wait(timeout_s)
    except AuthError as exc:
        return False, str(exc)
    finally:
        capture.stop()
    if query is None:
        return False, capture.error or "login did not complete"
    if str(query.get("status", "success")).lower() != "success" or not query.get("request_token"):
        return False, "venue reported a failed login (status != success)"
    try:
        session = flow.exchange(api_key, str(query["request_token"]), api_secret)
    except AuthError as exc:
        return False, str(exc)
    store.save_token(session["access_token"], session["user_id"])
    ok, reason = flow.validate(api_key, session["access_token"])
    if not ok:
        return False, f"token stored but validation failed: {reason}"
    return True, f"connected: {reason}"


__all__ = [
    "AuthError",
    "CALLBACK_PATH",
    "DEFAULT_CALLBACK_HOST",
    "DEFAULT_CALLBACK_PORT",
    "KiteAuthFlow",
    "RequestTokenCapture",
    "SESSION_SERVICE",
    "ZerodhaSessionStore",
    "redirect_url",
    "wait_for_login_token",
]
