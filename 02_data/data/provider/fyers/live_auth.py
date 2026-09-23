"""FYERS interactive authentication — official FYERS API v3 flow, automated.

Implements exactly the FYERS v3 authorization-code flow with ZERO manual
token copy/paste:

  1. ``FyersAuthFlow.login_url(app_id, redirect_uri, state)`` builds the
     official ``generate-authcode`` URL (never hand-waved, always the
     documented ``https://api-t1.fyers.in/api/v3`` endpoints).
  2. The app opens it in the default browser (async, UI thread free).
  3. The user completes FYERS' own login + OTP on FYERS' site. VAYREN
     never sees, automates, or bypasses credentials/OTP/CAPTCHA.
  4. FYERS redirects to the app's registered redirect URI, which points at
     the local ``AuthCodeCapture`` server
     (``http://127.0.0.1:<port>/vayren/fyers-callback``). The server
     captures ``auth_code`` (+ verifies ``state``) — the user copies nothing.
  5. ``exchange()`` swaps auth_code → access_token via the documented
     ``validate-authcode`` call (``appIdHash = sha256(app_id:secret)`` used
     once in-memory, never stored/logged).
  6. ``FyersSessionStore`` persists the session through the platform
     CredentialStore (Windows Credential Manager on win32, file fallback
     elsewhere) — never in source, never in plain logs.
  7. ``validate()`` proves the token with one read-only ``profile`` call —
     an expired/invalid token (FYERS codes -8/-15/-16/-17 or HTTP 401) is
     LOGIN_REQUIRED, never treated as a network error.

Stdlib only (``urllib``) — no FYERS SDK dependency, portable across
Windows/Linux/EC2/VPS. The HTTP layer is injectable for tests.

One-time operator setup (outside VAYREN): create the FYERS app at
https://myapi.fyers.in/dashboard/ and register the redirect URI shown in
SYSTEM → BROKERS (INTEGRATION DETAILS) as the app's redirect URL.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol, runtime_checkable

API_BASE = "https://api-t1.fyers.in/api/v3"
VAGATOR_BASE = "https://api-t2.fyers.in/vagator/v2"
DEFAULT_CALLBACK_HOST = "127.0.0.1"
DEFAULT_CALLBACK_PORT = 9475
CALLBACK_PATH = "/vayren/fyers-callback"
SESSION_SERVICE = "vayren:fyers:session"
CONFIG_SERVICE = "vayren:fyers"
REQUEST_TIMEOUT_S = 15.0

# FYERS' edge answers the stdlib ``Python-urllib/x.y`` User-Agent with a
# bare 403 (no useful body) — before credentials are even checked. Every
# request below carries a browser-like UA so a transport fingerprint is
# never misdiagnosed as a bad app_id/secret/redirect_uri.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# FYERS token invalid/expired signals: documented error codes or HTTP 401.
# Anything else is a venue/network problem, never a silent re-login.
_EXPIRED_CODES = {-8, -15, -16, -17}


class AuthError(RuntimeError):
    """Normalized authentication failure (reasons carry no secret values)."""

    def __init__(self, message: str, code: str = "AUTH_ERROR") -> None:
        super().__init__(message)
        self.code = code


def default_redirect_url(port: int = DEFAULT_CALLBACK_PORT) -> str:
    """The localhost URL the FYERS app must register as its redirect."""
    return f"http://{DEFAULT_CALLBACK_HOST}:{port}{CALLBACK_PATH}"


DEFAULT_REDIRECT_URL = default_redirect_url()


def app_id_hash(app_id: str, secret: str) -> str:
    """Documented ``sha256(app_id:secret)`` hex digest for token calls."""
    digest = hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()
    return digest


def new_state() -> str:
    """Unpredictable OAuth ``state`` for one login round-trip (CSRF check)."""
    return "vayren-" + secrets.token_urlsafe(16)


def login_url(app_id: str, redirect_uri: str, state: str) -> str:
    """Official ``generate-authcode`` URL for the browser step."""
    query = urllib.parse.urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{API_BASE}/generate-authcode?{query}"


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP seam: stdlib ``urllib`` in production, fakes in tests."""

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]: ...

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]: ...


class _UrllibTransport:
    """Default stdlib HTTP transport (injectable seam for tests)."""

    def __init__(self, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self._timeout = timeout_s

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                **(headers or {}),
            },
            method="POST",
        )
        return self._read_json(request)

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})},
            method="GET",
        )
        return self._read_json(request)

    def _read_json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return {"s": "error", "code": -8, "message": "unauthorized"}
            # FYERS POST /api/v3/token returns HTTP 308 with the auth_code
            # inside the response body's ``Url`` field — not a real redirect.
            # Read the body and hand it back so ``fetch_auth_code`` can extract
            # the auth_code from the URL string.  All other 3xx/4xx/5xx codes
            # remain errors.
            if exc.code == 308:
                try:
                    raw = exc.read().decode("utf-8", "replace")
                except Exception:
                    raw = ""
                if raw:
                    try:
                        body = json.loads(raw)
                        if isinstance(body, dict) and ("Url" in body or "url" in body):
                            return body
                    except ValueError:
                        pass
            err_msg = ""
            try:
                err_raw = exc.read().decode("utf-8", "replace")
                if err_raw:
                    err_json = json.loads(err_raw)
                    if isinstance(err_json, dict):
                        err_msg = str(err_json.get("message") or err_json.get("error") or "").strip()
            except Exception:
                pass
            if err_msg:
                raise AuthError(f"{err_msg} (HTTP {exc.code})", code="VENUE") from None
            raise AuthError(f"venue request failed (HTTP {exc.code})", code="NETWORK") from None
        except OSError as exc:
            raise AuthError(f"venue unreachable: {exc}", code="NETWORK") from None
        try:
            body = json.loads(raw)
        except ValueError:
            raise AuthError("venue returned an unreadable response", code="NETWORK") from None
        if not isinstance(body, dict):
            raise AuthError("venue returned an unreadable response", code="NETWORK") from None
        return body


class FyersSessionStore:
    """Access-token session persistence via the platform CredentialStore.

    Stored under its own service key so ``disconnect`` can clear the
    session without touching the broker's configured App ID/secret.
    """

    def __init__(self, store: Any, service: str = SESSION_SERVICE) -> None:
        self._store = store
        self._service = service

    def save_token(self, access_token: str, refresh_token: str = "", app_id: str = "") -> None:
        if not access_token:
            raise AuthError("refusing to store an empty access token", code="BAD_TOKEN")
        self._store.save(
            self._service,
            {
                "access_token": str(access_token),
                "refresh_token": str(refresh_token or ""),
                "app_id": str(app_id or ""),
                "saved_at": _now(),
            },
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
            "refresh_token": str(values.get("refresh_token", "") or ""),
            "app_id": str(values.get("app_id", "") or ""),
            "saved_at": str(values.get("saved_at", "") or ""),
        }

    def clear(self) -> None:
        self._store.delete(self._service)


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()


class AuthCodeCapture:
    """One-shot localhost callback server for the FYERS redirect.

    Runs ``http.server`` on a background thread; ``wait()`` blocks the
    CALLING (worker) thread only. The captured query carries ``auth_code``
    (and ``state`` for the CSRF check). Tokens/codes are never written to
    logs — the handler records the path, nothing else.
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
            target=self._server.serve_forever, name="vayren-fyers-callback", daemon=True
        )
        self._thread.start()
        return int(bound)

    def wait(self, timeout_s: float = 300.0) -> dict[str, str] | None:
        """Block until the redirect arrives or the timeout elapses.

        One-shot: the captured query is consumed by the first successful
        wait (a captured code must never be read twice).
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


class FyersAuthFlow:
    """Official FYERS API v3 auth steps with an injectable HTTP transport.

    ``transport`` defaults to the stdlib ``urllib`` implementation; tests
    inject a scripted double. Every failure path returns a normalized
    reason without echoing the secret, tokens, or auth codes.
    """

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._http = transport if transport is not None else _UrllibTransport()

    def login_url(self, app_id: str, redirect_uri: str = "", state: str = "") -> str:
        """Official login URL for the browser step."""
        if not app_id:
            raise AuthError("App ID required before login", code="CONFIG")
        return login_url(app_id, redirect_uri or DEFAULT_REDIRECT_URL, state or new_state())

    def exchange(self, app_id: str, auth_code: str, secret: str) -> dict[str, Any]:
        """auth_code → session payload (access_token + refresh_token)."""
        if not app_id or not auth_code or not secret:
            raise AuthError("app_id, auth_code and secret are all required", code="CONFIG")
        response = self._http.post_json(
            f"{API_BASE}/validate-authcode",
            {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash(app_id, secret),
                "code": auth_code,
            },
        )
        if not isinstance(response, dict) or response.get("s") != "ok":
            raise AuthError(
                f"token exchange rejected by venue: {response.get('message', 'unknown')}",
                code="EXCHANGE",
            )
        token = str(response.get("access_token", "") or "")
        if not token:
            raise AuthError("venue returned no access token", code="EXCHANGE")
        return {
            "access_token": token,
            "refresh_token": str(response.get("refresh_token", "") or ""),
        }

    def validate(self, app_id: str, access_token: str) -> tuple[bool, str]:
        """Read-only proof of a stored token (one ``profile`` call)."""
        if not app_id or not access_token:
            return False, "credentials missing"
        try:
            response = self._http.get_json(
                f"{API_BASE}/profile",
                headers={"Authorization": f"{app_id}:{access_token}"},
            )
        except AuthError as exc:
            return False, str(exc)
        if not isinstance(response, dict):
            return False, "venue returned an unreadable profile"
        if response.get("s") == "ok":
            data = response.get("data", {})
            identity = ""
            if isinstance(data, dict):
                identity = str(data.get("fy_id", "") or data.get("client_id", "") or "")
            if not identity:
                return False, "venue returned no account identity"
            return True, f"authenticated as {identity}"
        if _is_expired(response):
            return False, "session expired or invalid"
        return False, f"session check failed: {response.get('message', 'unknown')}"


def _is_expired(response: dict[str, Any]) -> bool:
    """True when the venue says the token is invalid/expired."""
    try:
        code = int(response.get("code", 0))
    except (TypeError, ValueError):
        code = 0
    if code in _EXPIRED_CODES:
        return True
    message = str(response.get("message", "") or "").lower()
    return "token" in message and ("invalid" in message or "expired" in message)


def fyers_interactive_login(
    flow: FyersAuthFlow,
    app_id: str,
    secret: str,
    store: FyersSessionStore,
    *,
    port: int = DEFAULT_CALLBACK_PORT,
    redirect_uri: str = "",
    timeout_s: float = 300.0,
    open_browser: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Complete one interactive login round-trip (blocking; worker threads only).

    Opens the browser (via ``open_browser``; default ``webbrowser.open``),
    captures the redirect, verifies ``state``, exchanges, stores,
    validates. Returns ``(ok, message)`` — the message is user-safe and
    secret-free.
    """
    import webbrowser

    if not app_id or not secret:
        return False, "broker not configured — save the App ID and Secret first"
    target_redirect = redirect_uri or DEFAULT_REDIRECT_URL
    state = new_state()
    capture = AuthCodeCapture()
    try:
        capture.start(port)
    except OSError as exc:
        return False, f"cannot open local callback port {port}: {exc}"
    try:
        url = flow.login_url(app_id, target_redirect, state)
        opener = open_browser or webbrowser.open
        opener(url)
        query = capture.wait(timeout_s)
    except AuthError as exc:
        return False, str(exc)
    finally:
        capture.stop()
    if query is None:
        return False, capture.error or "login did not complete"
    if str(query.get("state", "")) != state:
        return False, "login response did not match this session — try again"
    auth_code = str(query.get("auth_code", "") or "")
    if not auth_code:
        return False, "venue reported a failed login (no auth_code received)"
    try:
        session = flow.exchange(app_id, auth_code, secret)
    except AuthError as exc:
        return False, str(exc)
    store.save_token(session["access_token"], session.get("refresh_token", ""), app_id)
    ok, reason = flow.validate(app_id, session["access_token"])
    if not ok:
        return False, f"token stored but validation failed: {reason}"
    return True, f"connected: {reason}"


__all__ = [
    "API_BASE",
    "CALLBACK_PATH",
    "CONFIG_SERVICE",
    "DEFAULT_CALLBACK_HOST",
    "DEFAULT_CALLBACK_PORT",
    "DEFAULT_REDIRECT_URL",
    "REQUEST_TIMEOUT_S",
    "SESSION_SERVICE",
    "USER_AGENT",
    "VAGATOR_BASE",
    "AuthCodeCapture",
    "AuthError",
    "FyersAuthFlow",
    "FyersSessionStore",
    "HttpTransport",
    "_UrllibTransport",
    "app_id_hash",
    "default_redirect_url",
    "fyers_interactive_login",
    "login_url",
    "new_state",
]
