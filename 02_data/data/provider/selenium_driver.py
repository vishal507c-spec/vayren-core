"""Shared Selenium/Chrome infrastructure for broker auto-login.

Broker-agnostic primitives reused by every venue that automates its OWN
official browser login (today: FYERS; Zerodha keeps its proven inline
implementation in ``data.provider.zerodha.auth`` — NOTHING here changes
it, and nothing here imports it).

What lives here (mechanics only, zero venue knowledge):

- lazy Selenium import (``require_selenium`` — import errors become a
  typed ``SeleniumAuthError`` with an install hint, never a bare
  ``ImportError`` on worker threads);
- ChromeDriver resolution (Selenium Manager default → explicit
  ``chromedriver`` chain fallback for locked-down hosts);
- headless Chrome construction with the same anti-automation posture as
  the proven Zerodha driver (``--headless=new``, automation flags off,
  desktop UA, ``navigator.webdriver`` masked);
- explicit-wait element lookup with venue-meaningful timeout errors;
- redirect query-parameter capture (``auth_code``/``request_token`` style);
- secret-free file logging (stages only — values never logged);
- a typed error taxonomy so venues can tell INFRA failures (fall back
  to another path) apart from AUTH failures (report honestly, never
  mask with a fallback).

Stdlib only at import time; ``selenium``/``pyotp`` load lazily inside
the helpers so importing this module never requires a browser.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Reused driver posture (proven by the Zerodha auto-login): modern
# headless, no sandbox friction, desktop UA, automation hints off.
_HEADLESS_ARGS = (
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
)
_DEFAULT_WINDOW = "1280,900"
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_WEBDRIVER_MASK = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"

# Browsers need longer than APIs: page load + OTP/PIN round-trips.
DEFAULT_WAIT_S = 20.0
DEFAULT_REDIRECT_TIMEOUT_S = 90.0
ATTEMPT_PAUSE_S = 5.0
MAX_ATTEMPTS = 2


class SeleniumAuthError(RuntimeError):
    """Typed browser-auth failure (messages are user-safe, secret-free).

    Codes: ``BROWSER_UNAVAILABLE`` (infra — a fallback path may apply),
    ``PAGE_CHANGED`` (selectors stale — needs a selector refresh),
    ``CHALLENGE`` (CAPTCHA/anti-bot/SMS-OTP — needs the user),
    ``LOGIN_FAILED`` (venue rejected the credentials/OTP/PIN),
    ``TIMEOUT`` (redirect never arrived), ``NOT_CONFIGURED``.
    """

    def __init__(self, message: str, code: str = "LOGIN_FAILED") -> None:
        super().__init__(message)
        self.code = code


class BrowserUnavailableError(SeleniumAuthError):
    """Chrome/driver/Selenium itself is missing — a non-browser fallback
    (e.g. the venue's official API flow) may apply. Never raised for
    credential or page failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="BROWSER_UNAVAILABLE")


def require_selenium() -> Any:
    """Import the Selenium surface lazily (typed error when absent)."""
    needed = (
        "selenium.webdriver",
        "selenium.common.exceptions",
        "selenium.webdriver.chrome.options",
        "selenium.webdriver.chrome.service",
        "selenium.webdriver.common.by",
        "selenium.webdriver.support",
        "selenium.webdriver.support.ui",
    )
    try:
        import importlib

        module = importlib.import_module(needed[0])
        for name in needed[1:]:
            importlib.import_module(name)
        return module
    except ImportError:
        raise BrowserUnavailableError(
            "browser automation needs Selenium — run: pip install selenium"
        ) from None


def resolve_chromedriver() -> str | None:
    """Explicit chromedriver path, or None to let Selenium Manager decide.

    Chain (first hit wins): chromedriver_autoinstaller →
    webdriver_manager → PATH → well-known locations. None (not an
    error) means "no explicit driver found — Selenium Manager will
    fetch one on first launch if the host has network".
    """
    with contextlib.suppress(Exception):
        import chromedriver_autoinstaller

        path = chromedriver_autoinstaller.install()
        if path and Path(path).exists():
            return path
    with contextlib.suppress(Exception):
        from webdriver_manager.chrome import ChromeDriverManager  # type: ignore[import]

        path = ChromeDriverManager().install()
        if path and Path(path).exists():
            return path
    for candidate in ("chromedriver", "chromedriver.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    for fallback in (
        "/usr/bin/chromedriver",
        "/usr/local/bin/chromedriver",
        r"C:\Program Files\Google\Chrome\Application\chromedriver.exe",
        r"C:\chromedriver\chromedriver.exe",
    ):
        if Path(fallback).exists():
            return fallback
    return None


def build_chrome(
    *,
    headless: bool = True,
    window: str = _DEFAULT_WINDOW,
    driver_factory: Callable[..., Any] | None = None,
    service_factory: Callable[..., Any] | None = None,
    options_factory: Callable[[], Any] | None = None,
) -> Any:
    """Launch Chrome for broker login (typed error when impossible).

    ``VAYREN_SELENIUM_HEADED=1`` forces a visible window (bot-friction
    debugging only — headless stays the default). Factories exist for
    tests; production passes none.
    """
    wd = require_selenium()
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService

    headed = os.environ.get("VAYREN_SELENIUM_HEADED", "").strip() == "1"
    options = options_factory() if options_factory is not None else ChromeOptions()
    if headless and not headed:
        for arg in _HEADLESS_ARGS:
            options.add_argument(arg)
    options.add_argument(f"--window-size={window}")
    options.add_argument(f"--user-agent={_DESKTOP_UA}")
    with contextlib.suppress(Exception):
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
    with contextlib.suppress(Exception):
        options.add_experimental_option("useAutomationExtension", False)
    explicit = resolve_chromedriver()
    if service_factory is not None:
        service = service_factory(explicit)
    elif explicit:
        service = ChromeService(executable_path=explicit)
    else:
        service = ChromeService()  # Selenium Manager resolves/downloads
    factory = driver_factory or wd.Chrome
    try:
        driver = factory(service=service, options=options)
    except Exception as exc:
        raise BrowserUnavailableError(f"Chrome did not start: {type(exc).__name__}") from None
    with contextlib.suppress(Exception):
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _WEBDRIVER_MASK})
    return driver


class ChromeSession:
    """One driver lifetime (build on enter, ``quit()`` on exit).

    ``driver_factory`` injects a fake driver in tests; production passes
    none and gets a real headless Chrome.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        driver_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._headless = headless
        self._factory = driver_factory
        self._driver: Any | None = None

    def __enter__(self) -> Any:
        self._driver = build_chrome(headless=self._headless, driver_factory=self._factory)
        return self._driver

    def __exit__(self, *exc: object) -> None:
        if self._driver is not None:
            with contextlib.suppress(Exception):
                self._driver.quit()
            self._driver = None


def wait_visible(driver: Any, by: str, selector: str, timeout_s: float = DEFAULT_WAIT_S) -> Any:
    """Wait for a visible element (``PAGE_CHANGED`` when the page moved on)."""
    require_selenium()
    from selenium.webdriver.support import expected_conditions as ec
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        return WebDriverWait(driver, timeout_s).until(
            ec.visibility_of_element_located((by, selector))
        )
    except Exception as exc:
        name = type(exc).__name__
        if name in ("TimeoutException", "NoSuchElementException") or "timeout" in name.lower():
            raise SeleniumAuthError(
                f"login page changed ( timed out waiting for {selector!r} ) — "
                "the broker updated its login page; retry after a VAYREN update",
                code="PAGE_CHANGED",
            ) from None
        raise


def wait_url_param(
    driver: Any,
    param: str,
    timeout_s: float = DEFAULT_REDIRECT_TIMEOUT_S,
    poll_s: float = 0.5,
) -> str:
    """Poll ``driver.current_url`` until ``param`` appears in its query."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            current = str(getattr(driver, "current_url", "") or "")
        except Exception:
            current = ""
        if current:
            values = parse_qs(urlparse(current).query).get(param, [])
            if values and str(values[0]).strip():
                return str(values[0])
        time.sleep(poll_s)
    try:
        landed = str(getattr(driver, "current_url", "") or "")
    except Exception:
        landed = ""
    raise SeleniumAuthError(
        f"login did not finish (no {param!r} in the redirect"
        + (f"; stopped at {landed[:80]!r}" if landed else "")
        + ")",
        code="TIMEOUT",
    )


def auth_logger(name: str, logs_dir: str | Path) -> logging.Logger:
    """Secret-free file logger for browser auth (stages only, never values)."""
    alog = logging.getLogger(name)
    if not alog.handlers:
        os.makedirs(logs_dir, exist_ok=True)
        alog.setLevel(logging.DEBUG)
        handler = logging.FileHandler(
            os.path.join(logs_dir, datetime.now().strftime("%Y-%m-%d") + "_auth.log"),
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        alog.addHandler(handler)
    return alog


def default_logs_dir(data_dir: str | Path | None) -> Path:
    """Log directory for browser auth (data dir, else the temp folder)."""
    if data_dir:
        return Path(data_dir) / "logs"
    import tempfile

    return Path(tempfile.gettempdir()) / "vayren" / "logs"


__all__ = [
    "ATTEMPT_PAUSE_S",
    "DEFAULT_REDIRECT_TIMEOUT_S",
    "DEFAULT_WAIT_S",
    "MAX_ATTEMPTS",
    "BrowserUnavailableError",
    "ChromeSession",
    "SeleniumAuthError",
    "auth_logger",
    "build_chrome",
    "default_logs_dir",
    "require_selenium",
    "resolve_chromedriver",
    "wait_url_param",
    "wait_visible",
]
