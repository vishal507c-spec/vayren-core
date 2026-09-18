"""FyersSessionAdapter — authenticated read-only FYERS session (auth phase).

Owns the FYERS API v3 transport for one verified session: ``connect``,
``health``, ``account``, ``funds``, ``positions``, ``open_orders``. All
calls are read-only GETs with the documented ``app_id:access_token``
authorization header.

Deliberately NOT here (future phases): order placement, modification,
cancellation, historical candles, websockets. This adapter cannot trade —
there is no ``place_order`` method at all, so no execution path can ever
resolve it as a trading venue. The ``fyers-live`` UBL registry venue is
NOT registered in this phase; the manager keeps the instance for
connection/account verification only.
"""

from __future__ import annotations

from typing import Any

from data.provider.fyers.live_auth import (
    API_BASE,
    AuthError,
    FyersAuthFlow,
    HttpTransport,
    _UrllibTransport,
)


class FyersSessionAdapter:
    """Read-only FYERS session bound to one verified access token."""

    def __init__(
        self,
        app_id: str,
        access_token: str,
        transport: HttpTransport | None = None,
    ) -> None:
        self._app_id = str(app_id or "")
        self._token = str(access_token or "")
        self._http = transport if transport is not None else _UrllibTransport()
        self._connected = False

    # ── lifecycle ──────────────────────────────────────────────────────

    def connect(self) -> None:
        """Verify the session with one read-only profile call."""
        flow = FyersAuthFlow(self._http)
        ok, reason = flow.validate(self._app_id, self._token)
        if not ok:
            raise AuthError(f"FYERS session rejected: {reason}", code="AUTH")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> tuple[bool, str]:
        """Connection-only health (never implies LIVE readiness)."""
        if not self._app_id or not self._token:
            return False, "CREDENTIALS not ready"
        try:
            body = self._get("profile")
        except Exception as exc:
            return False, f"venue unreachable: {exc}"
        if not isinstance(body, dict) or body.get("s") != "ok":
            return False, "session check failed"
        return True, "ok"

    # ── read-only account surface (manager health checks) ──────────────

    def account(self) -> dict[str, Any]:
        """Minimal identity dict (``account_id``/``user_id``/``client_id``)."""
        body = self._get("profile")
        data = body.get("data", {}) if isinstance(body, dict) else {}
        if not isinstance(data, dict):
            raise AuthError("venue returned an unreadable profile", code="AUTH")
        identity = (
            str(data.get("fy_id", "") or "")
            or str(data.get("client_id", "") or "")
            or str(data.get("id", "") or "")
        )
        if not identity:
            raise AuthError("venue returned no account identity", code="AUTH")
        return {
            "account_id": identity,
            "user_id": identity,
            "client_id": identity,
            "name": str(data.get("name", "") or ""),
        }

    def funds(self) -> dict[str, float]:
        """Best-effort funds mapping (missing legs become 0.0, never fake)."""
        body = self._get("funds")
        limits = body.get("fund_limit", []) if isinstance(body, dict) else []
        available, used = 0.0, 0.0
        if isinstance(limits, list):
            for row in limits:
                if not isinstance(row, dict):
                    continue
                available += _safe_amount(row.get("equityAmount"))
                used += _safe_amount(row.get("commodityAmount"))
        total = available + used
        return {"available": available, "used": used, "total": total, "equity": total}

    def positions(self) -> list[dict[str, Any]]:
        """Net positions (empty when flat; honest failure otherwise)."""
        body = self._get("positions")
        rows = body.get("netPositions", []) if isinstance(body, dict) else []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def open_orders(self) -> list[dict[str, Any]]:
        """Open order book rows (empty when none)."""
        body = self._get("orders")
        rows = body.get("orderBook", []) if isinstance(body, dict) else []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    # ── internals ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"{self._app_id}:{self._token}"}

    def _get(self, path: str) -> dict[str, Any]:
        body = self._http.get_json(f"{API_BASE}/{path}", headers=self._headers())
        if not isinstance(body, dict):
            raise AuthError(f"venue returned an unreadable {path} response", code="NETWORK")
        return body


def _safe_amount(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return number


__all__ = ["FyersSessionAdapter"]
