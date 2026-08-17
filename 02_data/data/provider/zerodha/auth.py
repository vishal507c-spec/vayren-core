"""AuthEngine — Zerodha Kite Connect session management (adapted).

The original auto-login flow (token.json load → profile probe → headless
Selenium TOTP login → generate_session) is preserved. Integration changes
only:

- credentials come from :class:`data.provider.zerodha.credentials.ZerodhaCredentials`
  (read from the environment at construction) instead of hard-coded source
  constants;
- the token file lives in the data directory (settings) instead of the
  Desktop;
- missing SDKs or missing credentials raise :class:`AuthError` instead of
  exiting the process;
- credential values are never logged or included in messages.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from data.throttle import Throttle

log = logging.getLogger("HistDownloadEngine")

AUTH_LOGGER_NAME = "HistDLAuthEngine"


class AuthError(RuntimeError):
    """Raised when the provider cannot establish a session."""


class _SDKUnavailableError(AuthError):
    """Raised when a required SDK (kiteconnect/pyotp/selenium) is missing."""


def _load_kiteconnect():
    try:
        from kiteconnect import KiteConnect

        return KiteConnect
    except ImportError:
        raise _SDKUnavailableError(
            "kiteconnect is not installed — run: pip install kiteconnect"
        ) from None


def _load_pyotp():
    try:
        import pyotp

        return pyotp
    except ImportError:
        raise _SDKUnavailableError("pyotp is not installed — run: pip install pyotp") from None


def _load_selenium():
    try:
        from urllib.parse import parse_qs, urlparse

        from selenium import webdriver
        from selenium.common.exceptions import NoSuchElementException, TimeoutException
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.chrome.service import Service as ChromeService
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as ec
        from selenium.webdriver.support.ui import WebDriverWait

        return (
            webdriver,
            NoSuchElementException,
            TimeoutException,
            ChromeOptions,
            ChromeService,
            By,
            ec,
            WebDriverWait,
            parse_qs,
            urlparse,
        )
    except ImportError:
        raise _SDKUnavailableError(
            "selenium is not installed — run: pip install selenium (auto-login only)"
        ) from None


def _get_auth_logger(logs_dir: str | Path) -> logging.Logger:
    alog = logging.getLogger(AUTH_LOGGER_NAME)
    if not alog.handlers:
        os.makedirs(logs_dir, exist_ok=True)
        alog.setLevel(logging.DEBUG)
        fh = logging.FileHandler(
            os.path.join(logs_dir, datetime.now().strftime("%Y-%m-%d") + "_auth.log"),
            encoding="utf-8",
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        alog.addHandler(fh)
    return alog


class AuthEngine:
    """Lazily builds a validated KiteConnect session, renewing on expiry."""

    def __init__(self, settings, credentials) -> None:
        self._settings = settings
        self._credentials = credentials
        self._kite: object | None = None
        self._throttle = Throttle(settings.min_inter_call_seconds)

    def get_kite(self) -> object:
        if self._kite:
            return self._kite
        return self._fresh_kite()

    def available(self) -> tuple[bool, str]:
        """(ready, reason) — SDKs present and credentials configured?"""
        try:
            _load_kiteconnect()
        except AuthError as exc:
            return False, str(exc)
        if not self._credentials.configured:
            return (
                False,
                "Zerodha API credentials not configured — configure them in "
                "the Historical Download panel (or use the VAYREN_ZERODHA_* "
                "environment variable fallback)",
            )
        return True, "ready"

    def renew(self) -> object:
        log.warning("[Auth] Token expired — renewing …")
        self._kite = None
        return self._fresh_kite()

    def _fresh_kite(self) -> object:
        kiteconnect = _load_kiteconnect()
        if not self._credentials.configured:
            raise AuthError(
                "Zerodha API credentials are not configured — configure them "
                "in the Historical Download panel"
            )
        token = self._load_token()
        if token:
            kite = kiteconnect(api_key=self._credentials.api_key)
            kite.set_access_token(token)
            try:
                self._throttle.wait()
                kite.profile()
                log.info("[Auth] Saved token valid.")
                self._kite = kite
                return kite
            except Exception:
                log.warning("[Auth] Token expired — re-authenticating …")
        if not self._run_auto_auth():
            raise AuthError("Zerodha auto-login failed — check the auth log")
        fresh = self._load_token()
        if not fresh:
            raise AuthError("Auth succeeded but token file empty.")
        kite = kiteconnect(api_key=self._credentials.api_key)
        kite.set_access_token(fresh)
        self._throttle.wait()
        kite.profile()
        log.info("[Auth] AutoAuth complete.")
        self._kite = kite
        return kite

    # ── token file (disposable, lives in the data directory) ────────────────

    def _load_token(self) -> str | None:
        if os.path.isfile(self._settings.token_file):
            try:
                with open(self._settings.token_file) as f:
                    return json.load(f)["access_token"]
            except Exception:
                return None
        return None

    def _save_token(self, token: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._settings.token_file), exist_ok=True)
            with open(self._settings.token_file, "w") as f:
                json.dump({"access_token": token, "saved_at": datetime.now().isoformat()}, f)
            log.info(f"Token saved → {self._settings.token_file}")
        except Exception as e:
            log.error(f"Failed to save token: {e}")

    # ── auto-login (preserved, headless Selenium) ────────────────────────────

    def _run_auto_auth(self) -> bool:
        creds = self._credentials
        if not (creds.user_id and creds.password and creds.totp_secret):
            log.error(
                "[Auth] Auto-login needs USER_ID, PASSWORD and TOTP_SECRET "
                "credentials (in-app config or VAYREN_ZERODHA_* env fallback)."
            )
            return False
        kiteconnect = _load_kiteconnect()
        pyotp = _load_pyotp()
        alog = _get_auth_logger(self._settings.logs_dir or self._settings.data_dir)
        alog.info("=== AUTO-AUTH START ===")
        existing = self._load_token()
        if existing and self._is_token_valid(existing):
            alog.info("Existing token valid.")
            return True
        driver = None
        for attempt in range(1, 3):
            alog.info(f"Login attempt {attempt}/2 …")
            try:
                driver = self._build_driver()
                req_token = self._browser_login(driver, kiteconnect, pyotp, alog)
                kite = kiteconnect(api_key=creds.api_key)
                self._throttle.wait()
                session = kite.generate_session(req_token, api_secret=creds.api_secret)
                token = session["access_token"]  # type: ignore[index]
                self._save_token(token)
                if self._is_token_valid(token):
                    alog.info("New token saved and verified.")
                    return True
                raise AuthError("Token invalid after generation.")
            except Exception as exc:
                alog.error(f"Attempt {attempt} failed: {exc}")
                if attempt < 2:
                    time.sleep(5)
            finally:
                if driver is not None:
                    with contextlib.suppress(Exception):
                        driver.quit()
                    driver = None
        alog.error("Auth failed.")
        return False

    def _is_token_valid(self, token: str) -> bool:
        try:
            kiteconnect = _load_kiteconnect()
            kite = kiteconnect(api_key=self._credentials.api_key)
            kite.set_access_token(token)
            self._throttle.wait()
            kite.profile()
            return True
        except Exception:
            return False

    def _resolve_chromedriver(self) -> str:
        try:
            import chromedriver_autoinstaller

            p = chromedriver_autoinstaller.install()
            if p and Path(p).exists():
                return p
        except Exception:
            pass
        try:
            from webdriver_manager.chrome import (  # pyright: ignore[reportMissingImports]
                ChromeDriverManager,
            )

            p = ChromeDriverManager().install()
            if p and Path(p).exists():
                return p
        except Exception:
            pass
        for c in ("chromedriver", "chromedriver.exe"):
            found = shutil.which(c)
            if found:
                return found
        for fb in [
            "/usr/bin/chromedriver",
            "/usr/local/bin/chromedriver",
            r"C:\Program Files\Google\Chrome\Application\chromedriver.exe",
        ]:
            if Path(fb).exists():
                return fb
        raise AuthError("ChromeDriver not found.\nInstall Chrome or chromedriver.")

    def _build_driver(self):
        webdriver, _ns, _te, chrome_options, chrome_service, _by, _ec, _wd, _pq, _up = (
            _load_selenium()
        )
        opts = chrome_options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        svc = chrome_service(executable_path=self._resolve_chromedriver())
        driver = webdriver.Chrome(service=svc, options=opts)
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
        return driver

    def _browser_login(self, driver, kiteconnect, pyotp, _alog) -> str:
        (
            webdriver,
            no_such_element,
            timeout_exception,
            _co,
            _cs,
            by,
            ec,
            web_driver_wait,
            parse_qs,
            urlparse,
        ) = _load_selenium()
        _ = webdriver

        class _LoginError(Exception):
            pass

        def wait(by_, sel, t=15):
            try:
                return web_driver_wait(driver, t).until(
                    ec.visibility_of_element_located((by_, sel))
                )
            except timeout_exception:
                raise _LoginError(f"Timeout [{by_}] {sel!r}") from None

        driver.get(kiteconnect(api_key=self._credentials.api_key).login_url())
        time.sleep(1.2)
        wait(by.ID, "userid").send_keys(self._credentials.user_id)
        time.sleep(0.3)
        wait(by.ID, "password").send_keys(self._credentials.password)
        time.sleep(0.3)
        wait(by.XPATH, "//button[@type='submit' and contains(@class,'button-orange')]").click()
        time.sleep(3.0)
        totp = wait(
            by.XPATH,
            "//input[@label='External TOTP' or @type='number' "
            "or contains(@placeholder,'TOTP') or contains(@placeholder,'PIN')]",
        )
        totp.send_keys(pyotp.TOTP(self._credentials.totp_secret).now())
        time.sleep(1.2)
        with contextlib.suppress(no_such_element):
            driver.find_element(
                by.XPATH, "//button[@type='submit' and contains(@class,'button-orange')]"
            ).click()
        deadline = time.time() + 15
        while time.time() < deadline:
            cur = driver.current_url
            if "request_token" in cur:
                p = parse_qs(urlparse(cur).query)
                if "request_token" in p:
                    return p["request_token"][0]
            time.sleep(0.5)
        raise _LoginError(f"request_token not found. URL: {driver.current_url!r}")
