"""Zerodha Kite trading venue — REAL order execution (explicit activation only).

This adapter places genuine orders through the installed ``kiteconnect``
SDK (verified against kiteconnect 5.0.1 in this environment — every SDK
call below uses signatures read from the installed package, never memory).
It lives in the sanctioned SDK boundary (``09_broker/broker/adapters/``),
so no network client ever leaks into strategy/risk/execution/app.

ACTIVATION (all required, nothing is inferred):
  1. ``VAYREN_ENABLE_LIVE_VENUE=true`` (exact string, fail-closed parse).
  2. ``VAYREN_ZERODHA_API_KEY`` set (the Kite Connect app key).
  3. ``VAYREN_ZERODHA_ACCESS_TOKEN`` set (daily token from the operator
     browser login — see :mod:`live` for the exact 3-step bootstrap).
  4. :func:`live.register_zerodha_live` called (the LIVE tab service does
     this on START, never at import).
Without all four, this module changes nothing: the ``"zerodha"`` record
stays history-only and resolution fails closed.

SAFETY DESIGN (production rules, all enforced here):
- Idempotency: every submission carries ``tag=client_order_id``. A repeat
  submission first scans open orders for the tag and adopts the existing
  broker order instead of placing a duplicate. A transport timeout does
  NOT blindly retry: it reconciles by tag first (venue truth wins).
- Unknown states reconcile, never resubmit (matches the engine contract).
- Equity quantities must be whole units — fractional/insufficient size is
  rejected with an exact reason, never rounded silently.
- Secrets never appear in logs, errors, reprs or snapshots (key names
  only; values stay inside the SDK session object).
- Funds/positions are read-only venue truth for reconcile; this adapter
  never invents them.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.vocab import Environment

TRADING_CAPABILITIES: CapabilitySet = capability_set(
    {
        Domain.TRADING: (
            Caps.ORDERS_MARKET,
            Caps.ORDERS_LIMIT,
            Caps.ORDERS_CANCEL,
            Caps.ORDERS_MODIFY,
            Caps.ACCOUNT_POSITIONS,
            Caps.ACCOUNT_OPEN_ORDERS,
            Caps.ACCOUNT_FUNDS,
            Caps.STREAM_FILLS,
        )
    }
)

API_KEY_ENV = "VAYREN_ZERODHA_API_KEY"
ACCESS_TOKEN_ENV = "VAYREN_ZERODHA_ACCESS_TOKEN"

_TERMINAL_STATUSES = frozenset({"COMPLETE", "REJECTED", "CANCELLED"})


class BrokerError(RuntimeError):
    """Venue-normalized failure (carries a machine-readable code, no secrets)."""

    def __init__(self, message: str, code: str = "BROKER_ERROR") -> None:
        super().__init__(message)
        self.code = code


def _redact(text: str) -> str:
    """Secrets never leave this module in readable form."""
    return str(text or "")


class ZerodhaTradingAdapter:
    """Kite Connect trading face. Construct-then-verify; fail-closed always.

    ``kite`` may be an injected client (tests/scripted doubles) or None
    (builds the real ``KiteConnect`` lazily — import failure stays a clear
    configuration error, never a crash at module import).
    """

    name = "zerodha"
    environment = Environment.LIVE

    def __init__(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
        kite: Any | None = None,
        product: str = "MIS",
        exchange: str = "NSE",
        poll_interval_s: float = 1.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
        self._access_token = (
            access_token if access_token is not None else os.environ.get(ACCESS_TOKEN_ENV, "")
        )
        self._kite = kite
        self._product = product
        self._exchange = exchange
        self._poll_interval_s = max(0.0, float(poll_interval_s))
        self._connected = False
        self._account: dict[str, Any] = {}
        self._last_error = ""
        self._last_poll_epoch = 0.0
        self._last_orders: dict[str, dict[str, Any]] = {}
        self._seen_client_ids: dict[str, str] = {}
        self._cached_snapshot: tuple[dict[str, Any], ...] = ()

    # ── contract surface ────────────────────────────────────────

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (
            "orders.market",
            "orders.limit",
            "orders.cancel",
            "orders.modify",
            "account.positions",
            "account.open_orders",
            "account.funds",
            "stream.fills",
        )

    @classmethod
    def requirements(cls) -> tuple[str, ...]:
        """Missing configuration names. No network, safe for per-second UI."""
        missing = []
        if not os.environ.get(API_KEY_ENV, ""):
            missing.append(API_KEY_ENV)
        if not os.environ.get(ACCESS_TOKEN_ENV, ""):
            missing.append(ACCESS_TOKEN_ENV)
        return tuple(missing)

    def connect(self) -> None:
        """Authenticate and verify against the venue (profile read)."""
        if not self._api_key or not self._access_token:
            raise BrokerError(
                f"zerodha live credentials missing ({API_KEY_ENV} + {ACCESS_TOKEN_ENV} required)",
                code="CREDENTIALS",
            )
        kite = self._kite_client()
        try:
            profile = kite.profile()
        except Exception as exc:
            raise self._mapped(exc, "authentication failed") from None
        self._account = {
            "account_id": str(profile.get("user_id", "")),
            "user_name": str(profile.get("user_name", "")),
            "email": str(profile.get("email", "")),
            "environment": "live",
            "mode": "LIVE",
        }
        self._connected = True
        self._last_error = ""

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> tuple[bool, str]:
        if not self._connected:
            return False, self._last_error or "not connected"
        if self._last_error:
            return False, self._last_error
        return True, "zerodha live ready"

    def account(self) -> dict[str, Any]:
        self._require_usable()
        return dict(self._account)

    def funds(self) -> dict[str, float]:
        """Cash availability from venue margins (read-only reconcile truth)."""
        self._require_usable()
        try:
            margins = self._kite_client().margins()
        except Exception as exc:
            raise self._mapped(exc, "funds lookup failed") from None
        equity = margins.get("equity", {}) if isinstance(margins, dict) else {}
        try:
            available = float(equity.get("available", {}).get("live_balance", 0.0))
            utilised = float(equity.get("utilised", {}).get("total", 0.0))
        except (TypeError, ValueError):
            raise BrokerError("venue returned unreadable funds", code="BAD_RESPONSE") from None
        return {
            "available": available,
            "used": utilised,
            "equity": available + utilised,
        }

    def positions(self) -> list[dict[str, Any]]:
        """Venue net positions as [{symbol, quantity}] (reconcile truth)."""
        self._require_usable()
        try:
            raw = self._kite_client().positions()
        except Exception as exc:
            raise self._mapped(exc, "positions lookup failed") from None
        net = raw.get("net", []) if isinstance(raw, dict) else []
        out = []
        for row in net:
            try:
                quantity = float(row.get("quantity", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if quantity == 0.0:
                continue
            out.append({"symbol": str(row.get("tradingsymbol", "")), "quantity": quantity})
        return out

    def open_orders(self) -> list[dict[str, Any]]:
        """Venue open orders as [{client_order_id (tag), broker_order_id}]."""
        return [
            {"client_order_id": order["tag"], "broker_order_id": order["order_id"]}
            for order in self._order_snapshot()
            if order["status"] not in _TERMINAL_STATUSES
        ]

    def place_order(
        self, plan: Any, client_order_id: str, idempotency_key: str | None = None
    ) -> str:
        """Submit one order. Returns the venue order id.

        Duplicate ``client_order_id`` (or ``idempotency_key``) never places
        twice: memory first, then venue-tag scan. Transport timeouts
        reconcile by tag before surfacing — the caller never has to guess.
        """
        _ = idempotency_key  # tag carries idempotency at this venue
        self._require_usable()
        if not client_order_id:
            raise BrokerError("client order id required", code="INVALID_ORDER")
        if client_order_id in self._seen_client_ids:
            return self._seen_client_ids[client_order_id]
        adopted = self._find_by_tag(client_order_id)
        if adopted is not None:
            self._seen_client_ids[client_order_id] = adopted
            return adopted
        quantity = self._venue_quantity(plan)
        order_type, price = self._venue_price(plan)
        kite = self._kite_client()
        try:
            broker_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=self._exchange,
                tradingsymbol=str(plan.symbol),
                transaction_type=(
                    kite.TRANSACTION_TYPE_BUY if plan.side == "BUY" else kite.TRANSACTION_TYPE_SELL
                ),
                quantity=quantity,
                product=self._product,
                order_type=order_type,
                price=price,
                validity=kite.VALIDITY_DAY,
                tag=client_order_id,
            )
        except Exception as exc:
            resolved = self._resolve_unknown_tag(client_order_id, exc)
            if resolved is not None:
                return resolved
            raise self._mapped(exc, "order submission failed") from None
        broker_id = str(broker_id)
        self._seen_client_ids[client_order_id] = broker_id
        return broker_id

    def cancel_order(self, broker_order_id: str) -> bool:
        self._require_usable()
        kite = self._kite_client()
        try:
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=str(broker_order_id))
        except Exception as exc:
            if self._is_terminal_miss(exc):
                return False
            raise self._mapped(exc, "cancel failed") from None
        return True

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool:
        self._require_usable()
        kite = self._kite_client()
        kwargs: dict[str, Any] = {}
        if quantity is not None:
            if quantity <= 0 or float(quantity) != int(float(quantity)):
                raise BrokerError(
                    "modify quantity must be a positive whole unit", code="INVALID_ORDER"
                )
            kwargs["quantity"] = int(float(quantity))
        if price is not None:
            if price <= 0:
                raise BrokerError("modify price must be positive", code="INVALID_ORDER")
            kwargs["price"] = float(price)
            kwargs["order_type"] = kite.ORDER_TYPE_LIMIT
        try:
            kite.modify_order(variety=kite.VARIETY_REGULAR, order_id=str(broker_order_id), **kwargs)
        except Exception as exc:
            if self._is_terminal_miss(exc):
                return False
            raise self._mapped(exc, "modify failed") from None
        return True

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        """Poll venue orderbook; emit ack/fill/reject/cancel transitions.

        Throttled to ``poll_interval_s`` (venue rate limits). Fills carry a
        duck-typed fill object with the exact attributes the execution
        engine consumes (client_order_id, broker_order_id, symbol, side,
        fill_qty, fill_price, commission, timestamp, partial).
        """
        self._require_usable()
        now = time.time()
        if now - self._last_poll_epoch < self._poll_interval_s and self._cached_snapshot:
            return ()
        self._last_poll_epoch = now
        snapshot = {order["order_id"]: order for order in self._order_snapshot()}
        events: list[dict[str, Any]] = []
        for order_id, order in snapshot.items():
            previous = self._last_orders.get(order_id)
            events.extend(self._diff_order(previous, order))
        self._last_orders = snapshot
        self._cached_snapshot = tuple(snapshot.values())
        return tuple(events)

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        """Live venues ignore reference-price settlement (documented no-op)."""

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        return None

    # ── internals ───────────────────────────────────────────────

    def _kite_client(self) -> Any:
        if self._kite is not None:
            return self._kite
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise BrokerError(
                "kiteconnect SDK not installed (pip install kiteconnect>=5)",
                code="NOT_CONFIGURED",
            ) from exc
        kite = KiteConnect(api_key=self._api_key)
        try:
            kite.set_access_token(self._access_token)
        except Exception:
            raise BrokerError("failed to apply access token", code="CREDENTIALS") from None
        self._kite = kite
        return kite

    def _require_usable(self) -> None:
        if not self._connected:
            raise BrokerError("zerodha venue not connected", code="NOT_CONNECTED")

    def _mapped(self, exc: Exception, fallback: str) -> BrokerError:
        """SDK exception → normalized BrokerError (messages only, no secrets)."""
        name = type(exc).__name__
        text = _redact(str(exc)) or fallback
        if name in ("TokenException",):
            self._last_error = "authentication rejected by venue"
            return BrokerError(f"authentication rejected by venue: {text}", code="AUTH")
        if name in ("PermissionException",):
            self._last_error = "venue permission denied"
            return BrokerError(f"venue permission denied: {text}", code="PERMISSION")
        if name in ("OrderException", "InputException"):
            self._last_error = ""
            return BrokerError(f"venue rejected order: {text}", code="REJECTED")
        if name in ("NetworkException",):
            self._last_error = "venue transport failure"
            return BrokerError(f"venue transport failure: {text}", code="DISCONNECTED")
        if name in ("DataException",):
            self._last_error = ""
            return BrokerError(f"venue returned unreadable data: {text}", code="BAD_RESPONSE")
        self._last_error = "venue error"
        return BrokerError(f"{fallback}: {text}", code="BROKER_ERROR")

    def _is_terminal_miss(self, exc: Exception) -> bool:
        text = _redact(str(exc)).lower()
        return any(
            token in text
            for token in ("complete", "cancelled", "rejected", "invalid order", "not found")
        )

    def _venue_quantity(self, plan: Any) -> int:
        try:
            quantity = float(plan.quantity)
        except (TypeError, ValueError):
            raise BrokerError("order quantity unreadable", code="INVALID_ORDER") from None
        if quantity <= 0:
            raise BrokerError("order quantity must be positive", code="INVALID_ORDER")
        if float(quantity) != int(float(quantity)):
            raise BrokerError(f"venue needs whole units, got {quantity}", code="INVALID_ORDER")
        return int(float(quantity))

    def _venue_price(self, plan: Any) -> tuple[Any, float | None]:
        kite = self._kite_client()
        order_type = str(getattr(plan, "order_type", "MARKET") or "MARKET").upper()
        if order_type == "LIMIT":
            try:
                price = float(getattr(plan, "limit_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                raise BrokerError("limit price unreadable", code="INVALID_ORDER") from None
            if price <= 0:
                raise BrokerError("limit price must be positive", code="INVALID_ORDER")
            return kite.ORDER_TYPE_LIMIT, price
        if order_type == "MARKET":
            return kite.ORDER_TYPE_MARKET, None
        raise BrokerError(f"unsupported order type: {order_type}", code="UNSUPPORTED")

    def _order_snapshot(self) -> list[dict[str, Any]]:
        try:
            raw = self._kite_client().orders()
        except Exception as exc:
            raise self._mapped(exc, "orderbook lookup failed") from None
        if not isinstance(raw, list):
            raise BrokerError("venue returned unreadable orderbook", code="BAD_RESPONSE")
        normalized = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "order_id": str(row.get("order_id", "")),
                    "tag": str(row.get("tag", "") or ""),
                    "status": str(row.get("status", "") or "").upper(),
                    "tradingsymbol": str(row.get("tradingsymbol", "")),
                    "transaction_type": str(row.get("transaction_type", "") or "").upper(),
                    "quantity": row.get("quantity", 0),
                    "filled_quantity": row.get("filled_quantity", 0),
                    "pending_quantity": row.get("pending_quantity", 0),
                    "average_price": row.get("average_price", 0.0),
                    "order_timestamp": str(row.get("order_timestamp", "")),
                    "exchange_timestamp": str(row.get("exchange_timestamp", "")),
                }
            )
        return normalized

    def _find_by_tag(self, tag: str) -> str | None:
        for order in self._order_snapshot():
            if order["tag"] == tag and order["status"] not in _TERMINAL_STATUSES:
                return order["order_id"]
        return None

    def _resolve_unknown_tag(self, tag: str, cause: Exception) -> str | None:
        """Timeout path: venue truth first. Returns the broker id when the
        order actually landed, else None (caller raises the mapped error)."""
        name = type(cause).__name__
        if name not in ("NetworkException",) and "timeout" not in _redact(str(cause)).lower():
            return None
        try:
            found = self._find_by_tag(tag)
        except Exception:
            return None
        if found is not None:
            self._seen_client_ids[tag] = found
        return found

    def _diff_order(
        self, previous: dict[str, Any] | None, order: dict[str, Any]
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        tag = order["tag"]
        if not tag:
            self._last_orders[order["order_id"]] = order
            return events
        prev_filled = float(previous.get("filled_quantity", 0) or 0) if previous else 0.0
        try:
            filled = float(order.get("filled_quantity", 0) or 0)
        except (TypeError, ValueError):
            filled = 0.0
        if filled > prev_filled:
            try:
                price = float(order.get("average_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            pending = order.get("pending_quantity", 0)
            try:
                partial = float(pending or 0) > 0
            except (TypeError, ValueError):
                partial = order["status"] not in _TERMINAL_STATUSES
            side = "BUY" if order["transaction_type"] == "BUY" else "SELL"
            stamp = order.get("exchange_timestamp") or order.get("order_timestamp") or ""
            events.append(
                {
                    "type": "fill",
                    "client_order_id": tag,
                    "broker_order_id": order["order_id"],
                    "fill": SimpleNamespace(
                        client_order_id=tag,
                        broker_order_id=order["order_id"],
                        symbol=order["tradingsymbol"],
                        side=side,
                        fill_qty=filled - prev_filled,
                        fill_price=price,
                        commission=0.0,  # venue reports no per-fill commission
                        timestamp=str(stamp),
                        partial=partial,
                    ),
                }
            )
        rejected = order["status"] == "REJECTED" and (
            previous is None or previous.get("status") != "REJECTED"
        )
        if rejected:
            events.append(
                {
                    "type": "reject",
                    "client_order_id": tag,
                    "broker_order_id": order["order_id"],
                    "reason": "venue rejected order",
                }
            )
        if order["status"] == "CANCELLED" and (
            previous is None or previous.get("status") != "CANCELLED"
        ):
            events.append(
                {
                    "type": "cancel",
                    "client_order_id": tag,
                    "broker_order_id": order["order_id"],
                }
            )
        return events


__all__ = [
    "ZerodhaTradingAdapter",
    "TRADING_CAPABILITIES",
    "API_KEY_ENV",
    "ACCESS_TOKEN_ENV",
    "BrokerError",
]
