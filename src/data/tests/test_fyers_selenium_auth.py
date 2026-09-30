"""FYERS Selenium-auth proofs — full mechanics with fake drivers.

Covers: stored-session short-circuit, missing-triple exact messages
(zero browser), the complete automated sequence (client-ID → TOTP
boxes → PIN boxes → redirect ``auth_code`` + ``state`` → exchange →
store → validate), secret-free logging, infra failure surfacing for
the API fallback, challenge detection (CAPTCHA → user message, no
retry), and the CONNECT-button wrapper (short-circuit / selenium /
legacy-manual selection). No real browser, no real network.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pytest

from data.provider.fyers.credentials import FyersCredentials
from data.provider.fyers.selenium_auth import (
    FyersSeleniumAuthEngine,
    fyers_selenium_interactive_login,
)
from data.provider.selenium_driver import BrowserUnavailableError

APP_ID = "SPXXXXE7-100"
SECRET = "SHH-SECRET-9"
PIN = "2468"  # FYERS trading PIN is 4 digits (four PIN boxes)
FULL = FyersCredentials(APP_ID, SECRET, client_id="AB1234", totp_secret="JBSWY3DPEHPK3PXP", pin=PIN)
CALLBACK = "http://127.0.0.1:9475/vayren/fyers-callback"


class FakeElement:
    """Minimal WebElement double (visible, records input).

    ``resolver`` lets container elements (the PIN form) delegate scoped
    lookups back to the driver registry, like real DOM scoping.
    """

    def __init__(
        self,
        text: str = "",
        displayed: bool = True,
        resolver: Any | None = None,
    ) -> None:
        self.text = text
        self.entered = ""
        self.clicks = 0
        self.cleared = 0
        self._displayed = displayed
        self._resolver = resolver

    def clear(self) -> None:
        self.cleared += 1
        self.entered = ""

    def send_keys(self, value: str) -> None:
        self.entered += str(value)

    def click(self) -> None:
        self.clicks += 1

    def is_displayed(self) -> bool:
        return self._displayed

    def get_attribute(self, _name: str) -> str:
        return ""

    def find_element(self, by: str, selector: str) -> FakeElement:
        if self._resolver is not None:
            return self._resolver(by, selector)
        raise LookupError(f"no scoped lookup on {self!r}")


class FakeDriver:
    """Scripted Chrome double: staged URLs + element registry + page html."""

    def __init__(self) -> None:
        self.elements: dict[tuple[str, str], FakeElement] = {}
        self.page_source = "<html><body>fyers login</body></html>"
        self.visited: list[str] = []
        self.quit_called = False
        self._urls: list[str] = ["https://login.fyers.in/start"]

    def stage_urls(self, urls: list[str]) -> None:
        self._urls = list(urls)

    @property
    def current_url(self) -> str:
        if len(self._urls) > 1:
            return self._urls.pop(0)
        return self._urls[0]

    def get(self, url: str) -> None:
        self.visited.append(url)

    def find_element(self, by: str, selector: str) -> FakeElement:
        try:
            return self.elements[(by, selector)]
        except KeyError:
            raise LookupError(f"no such element {(by, selector)}") from None

    def find_elements(self, by: str, selector: str) -> list[FakeElement]:
        return [element for (b, s), element in self.elements.items() if b == by and s == selector]

    def execute_cdp_cmd(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def quit(self) -> None:
        self.quit_called = True


def _standard_driver(auth_code: str = "AUTH-1", state: str = "STATE-1") -> FakeDriver:
    """Driver staged for the happy path (login → redirect with code)."""
    driver = FakeDriver()
    driver.elements[("id", "fy_client_id")] = FakeElement()
    driver.elements[("id", "confirmOtpSubmit")] = FakeElement()
    for box in ("first", "second", "third", "fourth", "fifth", "sixth"):
        driver.elements[("id", box)] = FakeElement()
    driver.elements[("id", "verifyPinForm")] = FakeElement(resolver=driver.find_element)
    driver.elements[("id", "verifyPinSubmit")] = FakeElement()
    query = urlencode({"auth_code": auth_code, "state": state})
    driver.stage_urls(["https://login.fyers.in/start", f"{CALLBACK}?{query}"])
    return driver


class FakeFlow:
    """Exchange/validate seam (records calls, never touches network)."""

    def __init__(self, state: str = "STATE-1") -> None:
        self.state = state
        self.exchanged: list[str] = []
        self.validated: list[str] = []
        self.login_urls: list[str] = []

    def login_url(self, app_id: str, redirect_uri: str, state: str) -> str:
        self.login_urls.append(f"{app_id}|{redirect_uri}|{state}")
        self.state = state
        return f"https://login.fyers.in/start?state={state}"

    def exchange(self, app_id: str, auth_code: str, secret: str) -> dict[str, str]:
        self.exchanged.append(f"{app_id}|{auth_code}|{secret[:2]}")
        assert auth_code and secret
        return {"access_token": "TOK-1", "refresh_token": "REF-1"}

    def validate(self, app_id: str, token: str) -> tuple[bool, str]:
        self.validated.append(f"{app_id}|{token[:4]}")
        if token == "TOK-1":
            return True, "authenticated as AB1234"
        return False, "session expired or invalid"


class FakeStore:
    """Session store double."""

    def __init__(self, token: str = "") -> None:
        self.saved: dict[str, str] = {}
        if token:
            self.saved = {"access_token": token}

    def save_token(self, access_token: str, refresh_token: str = "", app_id: str = "") -> None:
        self.saved = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "app_id": app_id,
        }

    def load_token(self) -> dict[str, str] | None:
        return dict(self.saved) if self.saved.get("access_token") else None


def _engine(
    driver: FakeDriver,
    flow: FakeFlow,
    credentials: FyersCredentials = FULL,
    tmp_path: Any = None,
    *,
    stage_redirect: bool = True,
) -> FyersSeleniumAuthEngine:
    holders: dict[str, Any] = {"driver": driver}

    def factory(*_args: Any, **_kwargs: Any) -> FakeDriver:
        holders["builds"] = holders.get("builds", 0) + 1
        return holders["driver"]

    if stage_redirect:
        # Stage the redirect with the REAL per-attempt state (CSRF match),
        # exactly like the venue echoes it back.
        real_login_url = flow.login_url

        def login_url(app_id: str, redirect_uri: str, state: str) -> str:
            url = real_login_url(app_id, redirect_uri, state)
            query = urlencode({"auth_code": "AUTH-1", "state": state})
            driver.stage_urls([url, f"{CALLBACK}?{query}"])
            return url

        flow.login_url = login_url  # type: ignore[method-assign]

    engine = FyersSeleniumAuthEngine(
        credentials,
        driver_factory=factory,
        flow_factory=lambda: flow,
        logs_dir=str(tmp_path) if tmp_path is not None else None,
    )
    engine._builds = holders  # type: ignore[attr-defined]
    return engine


def test_stored_session_short_circuits_without_browser(tmp_path) -> None:
    flow = FakeFlow()
    engine = _engine(FakeDriver(), flow, tmp_path=tmp_path)
    ok, message = engine.ensure_session(FakeStore("TOK-1"))
    assert ok and "session live" in message
    assert engine._builds.get("builds", 0) == 0  # type: ignore[attr-defined]
    assert flow.exchanged == []


def test_missing_triple_reports_exact_piece_without_browser() -> None:
    for kwargs, needle in [
        ({"client_id": "", "totp_secret": "S", "pin": "1"}, "Client ID"),
        ({"client_id": "A", "totp_secret": "", "pin": "1"}, "TOTP Secret"),
        ({"client_id": "A", "totp_secret": "S", "pin": ""}, "PIN"),
    ]:
        creds = FyersCredentials(APP_ID, SECRET, **kwargs)  # type: ignore[arg-type]
        engine = _engine(FakeDriver(), FakeFlow(), credentials=creds)
        ok, message = engine.ensure_session(FakeStore())
        assert not ok and needle in message
        assert engine._builds.get("builds", 0) == 0  # type: ignore[attr-defined]


def test_unconfigured_credentials_refuse() -> None:
    engine = _engine(FakeDriver(), FakeFlow(), credentials=FyersCredentials("", ""))
    ok, message = engine.ensure_session(FakeStore())
    assert not ok and "App ID" in message


def test_full_browser_sequence_reaches_connected(tmp_path) -> None:
    driver = _standard_driver()
    flow = FakeFlow()
    engine = _engine(driver, flow, tmp_path=tmp_path)
    ok, message = engine.ensure_session(FakeStore())
    assert ok, message
    assert "connected" in message
    # Client ID went to the venue's field (RETURN submits the form, so the
    # key itself is a prefix of what the field recorded).
    assert driver.elements[("id", "fy_client_id")].entered.startswith("AB1234")
    # Six TOTP digits across six boxes (valid current code shape).
    otp = "".join(driver.elements[("id", box)].entered for box in ("first", "second", "third"))
    otp += "".join(driver.elements[("id", box)].entered for box in ("fourth", "fifth", "sixth"))
    assert len(otp) == 6 and otp.isdigit()
    # PIN digits across the four PIN boxes.
    pin = "".join(driver.elements[("id", box)].entered for box in ("first", "second", "third"))
    assert pin + driver.elements[("id", "fourth")].entered == PIN
    # Redirect code exchanged (state echoed back by the staged URL).
    assert flow.exchanged and flow.exchanged[0].split("|")[1] == "AUTH-1"
    assert driver.quit_called  # clean shutdown even on success


def test_state_mismatch_rejected() -> None:
    driver = _standard_driver(auth_code="AUTH-1", state="ATTACKER")
    engine = _engine(driver, FakeFlow(), tmp_path=None, stage_redirect=False)
    ok, message = engine.ensure_session(FakeStore())
    assert not ok and "match this session" in message


def test_challenge_aborts_with_user_message() -> None:
    driver = _standard_driver()
    driver.page_source = "<html><body><iframe src='https://recaptcha/x'></iframe></body></html>"
    engine = _engine(driver, FakeFlow())
    ok, message = engine.ensure_session(FakeStore())
    assert not ok and "security check" in message
    assert driver.quit_called


def test_sms_mode_aborts_with_totp_guidance() -> None:
    driver = FakeDriver()
    driver.elements[("id", "fy_client_id")] = FakeElement()
    driver.elements[("id", "confirmOtpSubmit")] = FakeElement()
    driver.page_source = "<html><body>otp sent to your mobile. resend otp</body></html>"
    query = urlencode({"auth_code": "X", "state": "Y"})
    driver.stage_urls(["https://login.fyers.in/start", f"{CALLBACK}?{query}"])
    engine = _engine(driver, FakeFlow())
    ok, message = engine.ensure_session(FakeStore())
    assert not ok and "TOTP" in message


def test_infra_failure_propagates_for_api_fallback() -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("no chrome on this host")

    engine = FyersSeleniumAuthEngine(FULL, driver_factory=broken, flow_factory=FakeFlow)
    with pytest.raises(BrowserUnavailableError):
        engine.ensure_session(FakeStore())


def test_log_file_carries_no_secrets(tmp_path) -> None:
    import logging

    # Fresh handler for this directory (the logger caches by name).
    named = logging.getLogger("FyersSeleniumAuth")
    for handler in named.handlers[:]:
        named.removeHandler(handler)
        handler.close()
    driver = _standard_driver()
    engine = _engine(driver, FakeFlow(), tmp_path=tmp_path)
    ok, _ = engine.ensure_session(FakeStore())
    assert ok
    logs = list((tmp_path / "logs").glob("*_auth.log"))
    assert logs, "auth log must exist"
    blob = logs[0].read_text(encoding="utf-8")
    for secret in (PIN, "JBSWY3DPEHPK3PXP", "AUTH-1", "TOK-1", SECRET):
        assert secret not in blob
    assert "SELENIUM AUTH START" in blob


def test_wrapper_short_circuits_valid_session() -> None:
    builds = {"count": 0}

    def factory(*_args: Any, **_kwargs: Any) -> Any:
        builds["count"] += 1
        raise AssertionError("browser must not start")

    ok, message = fyers_selenium_interactive_login(
        FakeFlow(), APP_ID, SECRET, FakeStore("TOK-1"), config=None
    )
    assert ok and "already connected" in message
    assert builds["count"] == 0


def test_wrapper_runs_selenium_when_triple_present() -> None:
    driver = _standard_driver()
    flow = FakeFlow()

    # Stage the redirect with the real per-attempt state (CSRF match).
    real_login_url = flow.login_url

    def login_url(app_id: str, redirect_uri: str, state: str) -> str:
        url = real_login_url(app_id, redirect_uri, state)
        driver.stage_urls([url, f"{CALLBACK}?{urlencode({'auth_code': 'AUTH-1', 'state': state})}"])
        return url

    flow.login_url = login_url  # type: ignore[method-assign]
    holders: dict[str, Any] = {}

    import data.provider.fyers.selenium_auth as selenium_auth

    real = selenium_auth.FyersSeleniumAuthEngine
    try:

        class SpyEngine(real):  # type: ignore[misc]
            def __init__(self, credentials: Any) -> None:
                holders["credentials"] = credentials
                super().__init__(
                    credentials, driver_factory=lambda **_k: driver, flow_factory=lambda: flow
                )

        selenium_auth.FyersSeleniumAuthEngine = SpyEngine  # type: ignore[assignment]
        ok, message = fyers_selenium_interactive_login(
            flow,
            APP_ID,
            SECRET,
            FakeStore(),
            config={
                "app_id": APP_ID,
                "secret": SECRET,
                "client_id": "AB1234",
                "totp_secret": "JBSWY3DPEHPK3PXP",
                "pin": PIN,
            },
        )
    finally:
        selenium_auth.FyersSeleniumAuthEngine = real  # type: ignore[assignment]
    assert ok and "connected" in message
    assert holders["credentials"].client_id == "AB1234"


def test_wrapper_falls_back_to_manual_without_triple() -> None:
    from data.provider.fyers.live_auth import FyersAuthFlow

    opened: list[str] = []

    def fake_browser(url: str) -> None:
        opened.append(url)

    ok, _message = fyers_selenium_interactive_login(
        FyersAuthFlow(),
        APP_ID,
        SECRET,
        FakeStore(),
        port=0,
        timeout_s=0.01,
        open_browser=fake_browser,
        config={"app_id": APP_ID, "secret": SECRET},
    )
    assert not ok  # loopback wait expires with no callback in the test
    assert opened and "generate-authcode" in opened[0]
