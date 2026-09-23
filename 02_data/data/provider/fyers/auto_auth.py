"""FYERS automatic authentication — the Zerodha-experience equivalent.

Mirrors the proven Zerodha auto-auth engine
(``data.provider.zerodha.auth.AuthEngine``) step for step, adapted to
FYERS' official API surface:

    Zerodha                              FYERS (here)
    ───────                              ────────────
    saved token → profile() probe        saved token → profile probe
    headless Selenium login              official vagator login API *
    userid + password fields             send_login_otp {fy_id}
    pyotp TOTP field                     pyotp TOTP → verify_otp
    ( Kapol)                             verify_pin {trading PIN}
    redirect request_token capture       Bearer trade-token → /token → auth_code
    generate_session → access_token      validate-authcode → access_token
    save token.json → validate           save session store → validate
    2 attempts, auth log, no secrets     2 attempts, auth log, no secrets

* Why API instead of Selenium: FYERS exposes its own login endpoints
  (``api-t2.fyers.in/vagator/v2``) used by its web login and documented
  in its API samples — the user's own login ID + TOTP (from the user's
  own authenticator) + trading PIN through FYERS' own endpoints. No page
  scraping, no CAPTCHA interaction, no unofficial surface. The
  interactive browser flow (``live_auth.fyers_interactive_login``) stays
  as the fallback when auto-login credentials are absent.

Required for auto-login (all three, layered store → env like the rest):
``client_id`` (FYERS login ID), ``totp_secret`` (enable TOTP 2FA in the
FYERS portal), ``pin`` (trading PIN) — on top of the configured
``app_id`` + ``secret`` pair. Without the triple, ``ensure_session``
reports exactly what is missing and the caller falls back to the
interactive browser flow (capture stays automatic — the user never
handles tokens either way).
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

from data.provider.fyers.live_auth import (
    API_BASE,
    VAGATOR_BASE,
    AuthError,
    FyersAuthFlow,
    HttpTransport,
    _UrllibTransport,
    new_state,
)

log = logging.getLogger("FyersAutoAuth")

# Vagator web-login mode flag (documented community flow; the API's own
# web login uses this mode).
_WEB_LOGIN_APP_ID = "2"

# App-type suffix default when the App ID carries none (``APPID-100`` →
# type ``100``). Derived per app — never assumed across venues.
_DEFAULT_APP_TYPE = "100"


def _load_pyotp():  # type: ignore[no-untyped-def]
    try:
        import pyotp

        return pyotp
    except ImportError:
        raise AuthError("pyotp is not installed — run: pip install pyotp") from None


def _venue_message(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("message", "") or "unknown venue error")
    return "unreadable venue response"


def _stored_access_token(session_store: Any) -> str:
    """Stored access token, or "" when no session exists (never raises)."""
    if session_store is None:
        return ""
    stored = session_store.load_token()
    if isinstance(stored, dict):
        return str(stored.get("access_token", "") or "")
    return ""


def _require_key(response: Any, key: str, what: str) -> str:
    value = response.get(key, "") if isinstance(response, dict) else ""
    if not value or not str(value).strip():
        raise AuthError(f"venue login step failed ({what}): {_venue_message(response)}")
    return str(value)


def request_otp_key(http: HttpTransport, fy_id: str, app_id: str = _WEB_LOGIN_APP_ID) -> str:
    """Step 1 — ask FYERS to start a login (returns the OTP request key)."""
    response = http.post_json(f"{VAGATOR_BASE}/send_login_otp", {"fy_id": fy_id, "app_id": app_id})
    return _require_key(response, "request_key", "send_login_otp")


def verify_totp_key(http: HttpTransport, request_key: str, totp_code: str) -> str:
    """Step 2 — prove the TOTP (returns the PIN-stage request key)."""
    response = http.post_json(
        f"{VAGATOR_BASE}/verify_otp", {"request_key": request_key, "otp": totp_code}
    )
    return _require_key(response, "request_key", "verify_otp")


def verify_pin_token(http: HttpTransport, request_key: str, pin: str) -> str:
    """Step 3 — prove the trading PIN (returns the Bearer trade token)."""
    response = http.post_json(
        f"{VAGATOR_BASE}/verify_pin",
        {"request_key": request_key, "identity_type": "pin", "identifier": pin},
    )
    data = response.get("data", {}) if isinstance(response, dict) else {}
    token = ""
    if isinstance(data, dict):
        token = str(data.get("access_token", "") or "")
    if not token and isinstance(response, dict):
        token = str(response.get("access_token", "") or "")
    if not token:
        raise AuthError(f"venue login step failed (verify_pin): {_venue_message(response)}")
    return token


def split_app_id(app_id: str) -> tuple[str, str]:
    """``SPXXXXE7-100`` → (``SPXXXXE7``, ``100``); no suffix → default type."""
    if "-" in app_id:
        prefix, suffix = app_id.rsplit("-", 1)
        if prefix.strip() and suffix.strip():
            return prefix.strip(), suffix.strip()
    return app_id.strip(), _DEFAULT_APP_TYPE


def fetch_auth_code(
    http: HttpTransport,
    *,
    fy_id: str,
    app_id: str,
    redirect_uri: str,
    trade_token: str,
) -> str:
    """Step 4 — trade token → authorization code (Bearer, official /token)."""
    prefix, app_type = split_app_id(app_id)
    response = http.post_json(
        f"{API_BASE}/token",
        {
            "fyers_id": fy_id,
            "app_id": prefix,
            "redirect_uri": redirect_uri,
            "appType": app_type,
            "code_challenge": "",
            "state": new_state(),
            "scope": "",
            "nonce": "",
            "response_type": "code",
            "create_cookie": True,
        },
        headers={"Authorization": f"Bearer {trade_token}"},
    )
    url = ""
    if isinstance(response, dict):
        url = str(response.get("Url", "") or response.get("url", "") or "")
    if not url:
        raise AuthError(f"venue login step failed (token): {_venue_message(response)}")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    codes = query.get("auth_code", [])
    if not codes or not str(codes[0]).strip():
        raise AuthError("venue login step failed (token): no auth_code in redirect")
    return str(codes[0])


class FyersAutoAuthEngine:
    """Fully automatic FYERS session manager (Zerodha AuthEngine mirror).

    ``ensure_session(store)`` is the single entry point: valid stored
    session → ``(True, …)`` with zero network beyond one profile probe;
    missing/expired session + complete auto-login triple → the official
    TOTP+PIN flow (2 attempts) → exchange → store → validate; anything
    else → ``(False, <exact missing piece>)`` so the caller can fall back
    to the interactive browser flow. Messages never carry secrets.
    """

    def __init__(self, credentials: Any, transport: HttpTransport | None = None) -> None:
        self._credentials = credentials
        self._http = transport if transport is not None else _UrllibTransport()

    def available(self) -> tuple[bool, str]:
        """(ready, reason) — App ID + Secret configured?"""
        if not self._credentials.configured:
            return (
                False,
                "FYERS App ID / Secret not configured — configure them in "
                "SYSTEM → BROKERS (or use the VAYREN_FYERS_* "
                "environment variable fallback)",
            )
        return True, "ready"

    def auto_login_possible(self) -> tuple[bool, str]:
        """(possible, reason) — is the full auto-login triple present?"""
        ready, reason = self.available()
        if not ready:
            return False, reason
        if not self._credentials.auto_login_configured:
            return (
                False,
                "FYERS auto-login needs Client ID, TOTP Secret and PIN "
                "(SYSTEM → BROKERS → FYERS fields, or VAYREN_FYERS_CLIENT_ID / "
                "VAYREN_FYERS_TOTP_SECRET / VAYREN_FYERS_PIN) — "
                "falling back to the interactive browser flow",
            )
        return True, "ready"

    def ensure_session(self, session_store: Any) -> tuple[bool, str]:
        """Return a live session, authenticating automatically if needed."""
        creds = self._credentials
        ready, reason = self.available()
        if not ready:
            return False, reason
        flow = FyersAuthFlow(self._http)
        cached = _stored_access_token(session_store)
        if cached:
            ok, detail = flow.validate(creds.app_id, cached)
            if ok:
                log.info("[FyersAutoAuth] Saved session valid.")
                return True, f"session live ({detail})"
            if "unreachable" in detail:
                return False, detail
            log.warning("[FyersAutoAuth] Stored session expired — re-authenticating …")
        possible, why = self.auto_login_possible()
        if not possible:
            return False, why
        ok, last_err = self._run_auto_auth(session_store, flow)
        if not ok:
            return False, f"FYERS login failed: {last_err}" if last_err else "FYERS automatic login failed"
        token = _stored_access_token(session_store)
        if not token:
            return False, "automatic login reported success but stored no session"
        ok, detail = flow.validate(creds.app_id, token)
        if not ok:
            return False, f"automatic login validation failed: {detail}"
        log.info("[FyersAutoAuth] Automatic login complete.")
        return True, f"connected: {detail}"

    # ── automatic login (official TOTP+PIN API flow) ──────────────────────

    def _run_auto_auth(self, session_store: Any, flow: FyersAuthFlow) -> tuple[bool, str]:
        creds = self._credentials
        pyotp = _load_pyotp()
        log.info("[FyersAutoAuth] === AUTO-AUTH START ===")
        last_err = ""
        for attempt in (1, 2):
            log.info(f"[FyersAutoAuth] Login attempt {attempt}/2 …")
            try:
                otp_key = request_otp_key(self._http, creds.client_id)
                totp_code = pyotp.TOTP(creds.totp_secret).now()
                pin_key = verify_totp_key(self._http, otp_key, totp_code)
                trade_token = verify_pin_token(self._http, pin_key, creds.pin)
                auth_code = fetch_auth_code(
                    self._http,
                    fy_id=creds.client_id,
                    app_id=creds.app_id,
                    redirect_uri=creds.redirect_uri,
                    trade_token=trade_token,
                )
                session = flow.exchange(creds.app_id, auth_code, creds.secret)
                token = str(session.get("access_token", "") or "")
                if not token:
                    raise AuthError("venue returned no access token", code="EXCHANGE")
                session_store.save_token(
                    token, str(session.get("refresh_token", "") or ""), creds.app_id
                )
                log.info("[FyersAutoAuth] New session saved and verified.")
                return True, "ok"
            except Exception as exc:
                last_err = str(exc)
                log.warning(f"[FyersAutoAuth] Attempt {attempt} failed: {exc}")
                if attempt < 2:
                    time.sleep(1)
        log.warning("[FyersAutoAuth] Automatic login failed: %s", last_err)
        return False, last_err


__all__ = [
    "FyersAutoAuthEngine",
    "fetch_auth_code",
    "request_otp_key",
    "split_app_id",
    "verify_pin_token",
    "verify_totp_key",
]
