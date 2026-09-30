"""FYERS Selenium browser proof — REAL headless Chrome, local mock pages.

Drives the REAL ``FyersSeleniumAuthEngine`` (real driver build, real
explicit waits, real field entry, real redirect capture) against
loopback pages that replicate FYERS' live login model (client-ID →
six OTP boxes → four PIN boxes → redirect with ``auth_code``). The
exchange/validate seam stays scripted; everything browser-shaped is
genuine Chrome. Skips (never fails) where no Chrome exists.
"""

from __future__ import annotations

import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

selenium_missing = False
try:
    import selenium  # noqa: F401
except ImportError:
    selenium_missing = True


def _chrome_present() -> bool:
    if selenium_missing:
        return False
    if shutil.which("chrome") or shutil.which("chrome.exe"):
        return True
    from pathlib import Path

    return Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists()


pytestmark = pytest.mark.skipif(not _chrome_present(), reason="no Chrome for browser proof")


class MockFyersPages:
    """Loopback FYERS login model (records every submitted value)."""

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
                if parsed.path == "/login":
                    body = (
                        "<html><body><h1>FYERS mock</h1>"
                        '<form action="/otp" method="get">'
                        f'<input id="fy_client_id" name="fy_id" value="">'
                        f'<input type="hidden" name="state" value="{query.get("state", "")}">'
                        f'<input type="hidden" name="ru" value="{query.get("ru", "")}">'
                        '<button type="submit">Continue</button>'
                        "</form></body></html>"
                    )
                elif parsed.path == "/otp":
                    outer.seen["fy_id"] = query.get("fy_id", "")
                    boxes = "".join(
                        f'<input id="{b}" name="{b}" value="">'
                        for b in ("first", "second", "third", "fourth", "fifth", "sixth")
                    )
                    body = (
                        "<html><body><h1>OTP mock</h1>"
                        '<form action="/pin" method="get">'
                        f"{boxes}"
                        f'<input type="hidden" name="state" value="{query.get("state", "")}">'
                        f'<input type="hidden" name="ru" value="{query.get("ru", "")}">'
                        '<button id="confirmOtpSubmit" type="submit">Verify</button>'
                        "</form></body></html>"
                    )
                elif parsed.path == "/pin":
                    outer.seen["otp"] = "".join(
                        query.get(b, "")
                        for b in ("first", "second", "third", "fourth", "fifth", "sixth")
                    )
                    boxes = "".join(
                        f'<input id="{b}" name="{b}" value="">'
                        for b in ("first", "second", "third", "fourth")
                    )
                    body = (
                        "<html><body><h1>PIN mock</h1>"
                        '<form id="verifyPinForm" action="/done" method="get">'
                        f"{boxes}"
                        f'<input type="hidden" name="state" value="{query.get("state", "")}">'
                        f'<input type="hidden" name="ru" value="{query.get("ru", "")}">'
                        '<button id="verifyPinSubmit" type="submit">Submit</button>'
                        "</form></body></html>"
                    )
                elif parsed.path == "/done":
                    outer.seen["pin"] = "".join(
                        query.get(b, "") for b in ("first", "second", "third", "fourth")
                    )
                    target = query.get("ru", "") or "http://127.0.0.1:9/dead"
                    params = {"auth_code": "MOCK-AUTH", "state": query.get("state", "")}
                    location = f"{target}?{urlencode(params)}"
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.end_headers()
                    return
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                raw = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


@pytest.fixture()
def mock_pages() -> Any:
    pages = MockFyersPages()
    pages.start()
    yield pages
    pages.stop()


class MockFlow:
    """Login-URL points at the mock pages; exchange/validate scripted."""

    def __init__(self, pages: MockFyersPages) -> None:
        self._pages = pages
        self.exchanged: list[str] = []

    def login_url(self, app_id: str, redirect_uri: str, state: str) -> str:
        assert app_id
        return f"{self._pages.base}/login?{urlencode({'ru': redirect_uri, 'state': state})}"

    def exchange(self, app_id: str, auth_code: str, secret: str) -> dict[str, str]:
        self.exchanged.append(auth_code)
        assert auth_code == "MOCK-AUTH" and app_id and secret
        return {"access_token": "BROWSER-TOK", "refresh_token": ""}

    def validate(self, app_id: str, token: str) -> tuple[bool, str]:
        assert app_id
        if token == "BROWSER-TOK":
            return True, "authenticated as BROWSER-USER"
        return False, "session expired or invalid"


class MockStore:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def save_token(self, access_token: str, refresh_token: str = "", app_id: str = "") -> None:
        self.saved = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "app_id": app_id,
        }

    def load_token(self) -> dict[str, str] | None:
        return dict(self.saved) if self.saved.get("access_token") else None


def test_real_chrome_completes_mock_fyers_login(mock_pages: Any, tmp_path: Any) -> None:
    """REAL headless Chrome fills mock FYERS pages and finishes CONNECTED."""
    from data.provider.fyers.credentials import FyersCredentials
    from data.provider.fyers.selenium_auth import FyersSeleniumAuthEngine

    flow = MockFlow(mock_pages)
    credentials = FyersCredentials(
        "SPXXXXE7-100",
        "SHH-SECRET-9",
        redirect_uri="http://127.0.0.1:9/dead",
        client_id="BROWSER-USER",
        totp_secret="JBSWY3DPEHPK3PXP",
        pin="1357",
    )
    engine = FyersSeleniumAuthEngine(credentials, flow_factory=lambda: flow, logs_dir=str(tmp_path))
    ok, message = engine.ensure_session(MockStore())
    assert ok, message
    assert "connected" in message
    # The mock server saw genuine form submissions from real Chrome.
    assert mock_pages.seen.get("fy_id") == "BROWSER-USER"
    otp = mock_pages.seen.get("otp", "")
    assert len(otp) == 6 and otp.isdigit()
    assert mock_pages.seen.get("pin") == "1357"
    assert flow.exchanged == ["MOCK-AUTH"]
