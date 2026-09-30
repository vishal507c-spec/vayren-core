"""FYERS Selenium authentication — Chrome-driven official browser login.

Zerodha-experience parity through the SAME automation concept as the
proven Zerodha headless login (``data.provider.zerodha.auth``), adapted
to FYERS' own ``generate-authcode`` pages. Generic mechanics (driver,
waits, redirect capture, secret-free logging) come from the shared
:mod:`data.provider.selenium_driver` layer — Zerodha's implementation
is NOT imported and NOT changed.

Automated browser steps (verified against FYERS' live login page and
community-confirmed selectors):

1. open the official ``generate-authcode`` URL (``FyersAuthFlow``);
2. choose client-ID login when the option exists (older page variant);
3. enter the FYERS Client ID (``#fy_client_id``) — the page has no
   password field, so the existing credential schema is enough;
4. enter the 6-digit TOTP into the OTP boxes (``#first``…``#sixth``)
   and submit (``#confirmOtpSubmit``);
5. enter the trading PIN into ``#verifyPinForm #first``…``#fourth``
   and submit (``#verifyPinSubmit``);
6. approve the app when FYERS asks (first-use only — clicked when
   present, reported when it blocks);
7. capture ``auth_code`` (+ ``state`` CSRF check) from the redirect URL;
8. ``validate-authcode`` → store → ``profile`` validate (reused
   ``FyersAuthFlow``/``FyersSessionStore`` — the SAME exchange the
   interactive flow uses, so no token copy/paste anywhere).

NOT automated (pause and report instead):

- CAPTCHA / reCAPTCHA / anti-bot challenges (detected by marker);
- SMS/e-mail OTP mode (the automation only fills the TOTP the user
  enabled via External 2FA — anything else needs the user);
- first-time app approval when it cannot be confirmed;
- wrong TOTP/PIN (the venue says so — reported exactly, never
  retried blindly).

Secrets (Client ID excluded — it is an identifier, not a secret;
password/TOTP/PIN/auth_code/tokens) never reach logs, messages,
screenshots or events. Entry signature: ``ensure_session(store)``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

from data.provider.selenium_driver import (
    ATTEMPT_PAUSE_S,
    MAX_ATTEMPTS,
    BrowserUnavailableError,
    ChromeSession,
    SeleniumAuthError,
    auth_logger,
    default_logs_dir,
    require_selenium,
    wait_url_param,
    wait_visible,
)

log = logging.getLogger("FyersSeleniumAuth")

# ── live-page model (probed 2026-09-19 + community-confirmed) ──────────
# Client-ID step.
_LOGIN_CLIENT_ID_OPTION = "login_client_id"
_FY_CLIENT_ID = "fy_client_id"
# OTP step: six single boxes + confirm. Fallbacks cover page variants.
_OTP_BOX_IDS = ("first", "second", "third", "fourth", "fifth", "sixth")
_OTP_SUBMIT_ID = "confirmOtpSubmit"
# PIN step: four boxes scoped to the PIN form + verify.
_PIN_FORM_ID = "verifyPinForm"
_PIN_BOX_IDS = ("first", "second", "third", "fourth")
_PIN_SUBMIT_ID = "verifyPinSubmit"
# First-use app approval (clicked only when it appears).
_APPROVE_MARKERS = ("approve", "allow", "authorize", "authorise", "accept")
# Challenge markers: automation stops and asks the user instead.
_CHALLENGE_MARKERS = ("recaptcha", "g-recaptcha", "captcha", "cf-challenge", "challenge")
# The OTP page in SMS/e-mail mode (TOTP automation cannot proceed).
_SMS_MODE_MARKERS = ("resend otp", "resend sms", "otp sent to your mobile", "otp sent on")

_TIMEOUT_S = 20.0
_REDIRECT_TIMEOUT_S = 90.0


def _load_pyotp():  # type: ignore[no-untyped-def]
    try:
        import pyotp

        return pyotp
    except ImportError:
        raise SeleniumAuthError(
            "TOTP automation needs pyotp — run: pip install pyotp", code="NOT_CONFIGURED"
        ) from None


def _totp_now(secret: str) -> str:
    """Current 6-digit TOTP (waits out an expiring window edge)."""
    pyotp = _load_pyotp()
    import time as _time

    try:
        remaining = pyotp.TOTP(secret).interval - (int(_time.time()) % pyotp.TOTP(secret).interval)
    except Exception:
        remaining = 30
    if remaining < 5:
        _time.sleep(remaining + 1)
    return pyotp.TOTP(secret).now()


def _page_text(driver: Any) -> str:
    try:
        return str(getattr(driver, "page_source", "") or "").lower()
    except Exception:
        return ""


def _page_has(driver: Any, *needles: str) -> str:
    """First challenge/mode marker present in the page, else ``""``."""
    html = _page_text(driver)
    if not html:
        return ""
    for needle in needles:
        if needle in html:
            return needle
    return ""


def _element_present(driver: Any, by: str, selector: str) -> bool:
    try:
        driver.find_element(by, selector)
        return True
    except Exception:
        return False


def _selectors():  # type: ignore[no-untyped-def]
    """(ID-strategy, button-XPath, RETURN-key) — selenium loads lazily."""
    require_selenium()
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    return By.ID, "//button", Keys.RETURN


def _submit(element: Any) -> None:
    """Submit via RETURN, falling back to clicking the element itself."""
    _, _, press_return = _selectors()
    try:
        element.send_keys(press_return)
    except Exception:
        with _suppress():
            element.click()
    time.sleep(1.0)


def _suppress():  # type: ignore[no-untyped-def]
    import contextlib

    return contextlib.suppress(Exception)


class FyersSeleniumAuthEngine:
    """Chrome-driven FYERS login (Selenium-first path).

    ``credentials`` is the venue's ``FyersCredentials`` (existing
    schema: ``app_id``/``secret``/``redirect_uri`` + auto-login triple
    ``client_id``/``totp_secret``/``pin`` — no password exists in the
    FYERS browser flow, so none was invented). ``driver_factory``
    injects a fake driver in tests; ``flow_factory`` injects the
    exchange/validate seam.
    """

    def __init__(
        self,
        credentials: Any,
        *,
        driver_factory: Callable[..., Any] | None = None,
        flow_factory: Callable[[], Any] | None = None,
        logs_dir: Any | None = None,
    ) -> None:
        self._credentials = credentials
        self._driver_factory = driver_factory
        self._flow_factory = flow_factory
        self._logs_dir = logs_dir

    # ── entry point ──────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        """(ready, reason) — App ID + Secret configured?"""
        if not getattr(self._credentials, "configured", False):
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
        if not getattr(self._credentials, "auto_login_configured", False):
            return (
                False,
                "FYERS browser login needs Client ID, TOTP Secret and PIN "
                "(SYSTEM → BROKERS → FYERS fields, or VAYREN_FYERS_CLIENT_ID / "
                "VAYREN_FYERS_TOTP_SECRET / VAYREN_FYERS_PIN)",
            )
        return True, "ready"

    def ensure_session(self, session_store: Any) -> tuple[bool, str]:
        """Return a live session, driving Chrome automatically if needed."""
        from data.provider.fyers.live_auth import FyersAuthFlow

        creds = self._credentials
        ready, reason = self.available()
        if not ready:
            return False, reason
        flow = self._flow_factory() if self._flow_factory is not None else FyersAuthFlow()
        cached = _stored_access_token(session_store)
        if cached:
            ok, detail = flow.validate(creds.app_id, cached)
            if ok:
                log.info("[FyersSelenium] Saved session valid.")
                return True, f"session live ({detail})"
            if "unreachable" in detail:
                return False, detail
            log.warning("[FyersSelenium] Stored session expired — browser login …")
        possible, why = self.auto_login_possible()
        if not possible:
            return False, why
        alog = auth_logger("FyersSeleniumAuth", default_logs_dir(self._logs_dir))
        alog.info("=== SELENIUM AUTH START ===")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            alog.info(f"Browser login attempt {attempt}/{MAX_ATTEMPTS} …")
            try:
                auth_code, state = self._browser_auth_code(flow, alog)
                session = flow.exchange(creds.app_id, auth_code, creds.secret)
                token = str(session.get("access_token", "") or "")
                if not token:
                    raise SeleniumAuthError("venue returned no access token", code="LOGIN_FAILED")
                session_store.save_token(
                    token, str(session.get("refresh_token", "") or ""), creds.app_id
                )
            except BrowserUnavailableError:
                raise
            except SeleniumAuthError as exc:
                alog.warning(f"Attempt {attempt} failed: {exc} [{exc.code}]")
                if exc.code == "CHALLENGE":
                    return False, str(exc)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(ATTEMPT_PAUSE_S)
                    continue
                return False, str(exc)
            token = _stored_access_token(session_store)
            if not token:
                return False, "browser login reported success but stored no session"
            ok, detail = flow.validate(creds.app_id, token)
            if not ok:
                return False, f"browser login validation failed: {detail}"
            alog.info("Browser login complete.")
            log.info("[FyersSelenium] Browser login complete.")
            return True, f"connected: {detail}"
        return False, "FYERS browser login failed — check the log"

    # ── browser run (one attempt; driver always quits) ──────────

    def _browser_auth_code(self, flow: Any, alog: Any) -> tuple[str, str]:
        creds = self._credentials
        by_id, _, _ = _selectors()
        by = by_id
        from data.provider.fyers.live_auth import new_state

        state = new_state()
        url = flow.login_url(creds.app_id, creds.redirect_uri, state)
        with ChromeSession(driver_factory=self._driver_factory) as driver:
            alog.info("login page opened")
            driver.get(url)
            time.sleep(1.5)
            self._refuse_challenge(driver, "login page")
            self._step_client_id(driver, by, alog)
            self._refuse_challenge(driver, "client-id step")
            self._step_otp(driver, by, alog)
            self._refuse_challenge(driver, "OTP step")
            self._step_pin(driver, by, alog)
            self._refuse_challenge(driver, "PIN step")
            self._step_approve(driver, urlparse(url).netloc, alog)
            alog.info("waiting for broker redirect")
            auth_code = wait_url_param(driver, "auth_code", _REDIRECT_TIMEOUT_S)
            query = parse_qs(urlparse(str(driver.current_url)).query)
            landed = str((query.get("state", [""]) or [""])[0] or "")
            if landed != state:
                raise SeleniumAuthError(
                    "login response did not match this session — try again",
                    code="LOGIN_FAILED",
                )
            alog.info("auth code captured from redirect")
            return auth_code, state

    # ── page steps ───────────────────────────────────────────────

    def _step_client_id(self, driver: Any, by: str, alog: Any) -> None:
        creds = self._credentials
        if _element_present(driver, by, _LOGIN_CLIENT_ID_OPTION):
            with _suppress():
                driver.find_element(by, _LOGIN_CLIENT_ID_OPTION).click()
            time.sleep(1.0)
        field = wait_visible(driver, by, _FY_CLIENT_ID, _TIMEOUT_S)
        field.clear()
        field.send_keys(creds.client_id)
        alog.info("client ID entered")
        time.sleep(0.5)
        _submit(field)
        try:
            wait_visible(driver, by, _OTP_SUBMIT_ID, 8.0)
        except SeleniumAuthError:
            # Older page variant needs an explicit continue click: the
            # visible submit button, never a blind first-button click.
            if not _click_visible_submit(driver):
                raise
            wait_visible(driver, by, _OTP_SUBMIT_ID, _TIMEOUT_S)
        self._fail_on_venue_error(driver, "client ID was not accepted")

    def _step_otp(self, driver: Any, by: str, alog: Any) -> None:
        creds = self._credentials
        if _page_has(driver, *_SMS_MODE_MARKERS):
            raise SeleniumAuthError(
                "FYERS asked for a mobile/e-mail OTP — open the browser login "
                "once and switch to the authenticator (TOTP) option, then retry",
                code="CHALLENGE",
            )
        wait_visible(driver, by, _OTP_SUBMIT_ID, _TIMEOUT_S)
        boxes = [b for b in (_find(driver, by, box) for box in _OTP_BOX_IDS) if b is not None]
        if len(boxes) < 6:
            single = _find_otp_single(driver, by)
            if single is None:
                raise SeleniumAuthError(
                    "login page changed (OTP field not found) — "
                    "the broker updated its login page; retry after a VAYREN update",
                    code="PAGE_CHANGED",
                )
            single.clear()
            single.send_keys(_totp_now(creds.totp_secret))
        else:
            code = _totp_now(creds.totp_secret)
            for box, digit in zip(boxes, code, strict=False):
                box.clear()
                box.send_keys(digit)
        alog.info("one-time code entered")
        time.sleep(0.5)
        _submit(driver.find_element(by, _OTP_SUBMIT_ID))
        self._fail_on_venue_error(
            driver,
            "one-time code was not accepted — enable External TOTP 2FA "
            "in the FYERS portal and check the TOTP secret",
        )

    def _step_pin(self, driver: Any, by: str, alog: Any) -> None:
        creds = self._credentials
        wait_visible(driver, by, _PIN_SUBMIT_ID, _TIMEOUT_S)
        scope = None
        if _element_present(driver, by, _PIN_FORM_ID):
            with _suppress():
                scope = driver.find_element(by, _PIN_FORM_ID)
        boxes = []
        for box in _PIN_BOX_IDS:
            found = _find_in(driver, scope, by, box)
            if found is not None:
                boxes.append(found)
        if len(boxes) < 4:
            raise SeleniumAuthError(
                "login page changed (PIN fields not found) — "
                "the broker updated its login page; retry after a VAYREN update",
                code="PAGE_CHANGED",
            )
        for box, digit in zip(boxes, str(creds.pin), strict=False):
            box.clear()
            box.send_keys(digit)
        alog.info("PIN entered")
        time.sleep(0.5)
        _submit(driver.find_element(by, _PIN_SUBMIT_ID))
        self._fail_on_venue_error(driver, "PIN was not accepted — check the trading PIN")

    def _step_approve(self, driver: Any, login_host: str, alog: Any) -> None:
        """Click first-use app approval when it appears (else continue).

        Scoped to the venue's login host: once navigation leaves it
        (redirect/error page) approval is not applicable, so stray text
        on browser error pages can never false-positive into a block.
        """

        def on_login_host() -> bool:
            try:
                current = str(getattr(driver, "current_url", "") or "")
            except Exception:
                return True
            if "auth_code=" in current:
                return False
            return urlparse(current).netloc == login_host

        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                current = str(getattr(driver, "current_url", "") or "")
            except Exception:
                current = ""
            if "auth_code=" in current or (current and urlparse(current).netloc != login_host):
                return
            if self._click_approve(driver):
                time.sleep(1.5)
                alog.info("app approval confirmed")
                return
            time.sleep(0.5)
        if on_login_host() and _page_has(driver, *_APPROVE_MARKERS, "grant access"):
            raise SeleniumAuthError(
                "FYERS asks for first-time app approval — approve the app "
                "once in the browser, then retry (later logins are automatic)",
                code="CHALLENGE",
            )

    # ── guards ───────────────────────────────────────────────────

    def _refuse_challenge(self, driver: Any, stage: str) -> None:
        marker = _page_has(driver, *_CHALLENGE_MARKERS)
        if marker:
            raise SeleniumAuthError(
                f"FYERS shows a security check ({marker}) at the {stage} — "
                "complete it once in a normal browser window, then retry",
                code="CHALLENGE",
            )

    def _fail_on_venue_error(self, driver: Any, message: str) -> None:
        # Phrasal markers only (single words like "expired" appear in
        # benign footer/help text and would false-positive).
        time.sleep(1.0)
        html = _page_text(driver)
        if "auth_code=" in html:
            return
        for marker in (
            "invalid otp",
            "invalid pin",
            "invalid totp",
            "incorrect otp",
            "incorrect pin",
            "wrong otp",
            "wrong pin",
            "authentication failed",
            "auth failed",
            "otp mismatch",
            "not accepted",
        ):
            if marker in html:
                raise SeleniumAuthError(f"{message}", code="LOGIN_FAILED")

    def _click_approve(self, driver: Any) -> bool:
        buttons = _visible_buttons(driver)
        for button in buttons:
            try:
                label = f"{button.text or ''} {button.get_attribute('value') or ''}".lower()
            except Exception:
                continue
            if any(marker in label for marker in _APPROVE_MARKERS):
                with _suppress():
                    button.click()
                    return True
        return False


def _visible_buttons(driver: Any) -> list[Any]:
    """Visible submit/button elements (never a blind first-button click)."""
    _, button_xpath, _ = _selectors()
    try:
        candidates = driver.find_elements("xpath", button_xpath + "[@type='submit']")
    except Exception:
        candidates = []
    if not candidates:
        try:
            candidates = driver.find_elements("xpath", button_xpath)
        except Exception:
            return []
    visible = []
    for candidate in candidates:
        try:
            if candidate.is_displayed():
                visible.append(candidate)
        except Exception:
            continue
    return visible


def _click_visible_submit(driver: Any) -> bool:
    for candidate in _visible_buttons(driver):
        with _suppress():
            candidate.click()
            return True
    return False


def _find(driver: Any, by: str, selector: str) -> Any | None:
    try:
        return driver.find_element(by, selector)
    except Exception:
        return None


def _find_in(driver: Any, scope: Any | None, by: str, selector: str) -> Any | None:
    try:
        if scope is not None:
            return scope.find_element(by, selector)
        return driver.find_element(by, selector)
    except Exception:
        return None


def _find_otp_single(driver: Any, by: str) -> Any | None:
    """Single-box OTP variant (page revisions that use one TOTP field)."""
    for selector in (
        "otp",
        "totp",
        "otpInput",
        "totp-code",
    ):
        found = _find(driver, by, selector)
        if found is not None:
            return found
    return None


def _stored_access_token(session_store: Any) -> str:
    """Stored access token, or "" when no session exists (never raises)."""
    if session_store is None:
        return ""
    try:
        stored = session_store.load_token()
    except Exception:
        return ""
    if isinstance(stored, dict):
        return str(stored.get("access_token", "") or "")
    return ""


def fyers_selenium_interactive_login(
    flow: Any,
    app_id: str,
    secret: str,
    store: Any,
    *,
    port: int = 9475,
    redirect_uri: str = "",
    timeout_s: float = 300.0,
    open_browser: Callable[[str], None] | None = None,
    config: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """CONNECT-button login: Selenium-first, manual browser as fallback.

    Same positional signature as ``fyers_interactive_login`` (the
    manager calls it identically) plus an optional ``config`` mapping
    the manager passes when the callable accepts it. Behavior:

    - a still-valid stored session short-circuits (no browser at all —
      the session-check job usually connected already);
    - complete auto-login triple → Chrome automation;
    - anything else → the legacy manual browser flow, unchanged.
    """
    from data.provider.fyers.live_auth import fyers_interactive_login

    if app_id:
        try:
            ok, _detail = flow.validate(app_id, _stored_access_token(store))
        except Exception:
            ok = False
        if ok:
            return True, "already connected — session still valid"
    if config:
        from data.provider.fyers.credentials import FyersCredentials

        credentials = FyersCredentials(
            app_id=config.get("app_id", app_id),
            secret=config.get("secret", secret),
            redirect_uri=config.get("redirect_uri", redirect_uri),
            pin=config.get("pin", ""),
            client_id=config.get("client_id", ""),
            totp_secret=config.get("totp_secret", ""),
        )
        engine = FyersSeleniumAuthEngine(credentials)
        possible, _why = engine.auto_login_possible()
        if possible:
            try:
                return engine.ensure_session(store)
            except BrowserUnavailableError:
                pass
    return fyers_interactive_login(
        flow,
        app_id,
        secret,
        store,
        port=port,
        redirect_uri=redirect_uri,
        timeout_s=timeout_s,
        open_browser=open_browser,
    )


__all__ = [
    "FyersSeleniumAuthEngine",
    "fyers_selenium_interactive_login",
]
