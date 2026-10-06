"""FyersSessionAdapter — authenticated FYERS session and live order execution.

Owns the FYERS API v3 transport for one verified session: ``connect``,
``health``, ``account``, ``funds``, ``positions``, ``open_orders``, and live
order execution: ``place_order``, ``cancel_order``, ``modify_order``,
``stream_events``.
"""

from __future__ import annotations

import logging
import queue
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.providers.fyers.live_auth import (
    API_BASE,
    AuthError,
    FyersAuthFlow,
    HttpTransport,
    _UrllibTransport,
)
from broker.vocab import BrokerError, Environment, ErrorCode

logger = logging.getLogger(__name__)

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

_TERMINAL_STATUSES = frozenset({2, 5, 6})  # 2: Filled, 5: Cancelled, 6: Rejected
# FYERS order statuses:
# 1: Cancelled, 2: Traded/Filled, 4: Transit, 5: Rejected, 6: Pending
# We handle both numeric statuses and string representations gracefully.


STATE_DISCONNECTED = "DISCONNECTED"
STATE_CONNECTING = "CONNECTING"
STATE_CONNECTED = "CONNECTED"
STATE_RECONNECTING = "RECONNECTING"
STATE_ERROR = "ERROR"


class FyersSessionAdapter:
    """FYERS trading adapter bound to one verified access token."""

    name = "fyers"
    environment = Environment.LIVE

    def __init__(
        self,
        app_id: str,
        access_token: str,
        transport: HttpTransport | None = None,
        poll_interval_s: float = 1.0,
        order_socket_factory: Callable[..., Any] | None = None,
        enable_order_ws: bool = True,
    ) -> None:
        self._app_id = str(app_id or "")
        self._token = str(access_token or "")
        self._http = transport if transport is not None else _UrllibTransport()
        self._connected = False
        self._poll_interval_s = max(0.0, float(poll_interval_s))
        self._last_poll_epoch = 0.0
        self._last_orders: dict[str, dict[str, Any]] = {}
        self._seen_client_ids: dict[str, str] = {}
        self._cached_snapshot: tuple[dict[str, Any], ...] = ()

        # Order WebSocket and stream state
        self._order_socket_factory = order_socket_factory
        self._enable_order_ws = enable_order_ws
        self._order_ws: Any = None
        self._order_ws_state = STATE_DISCONNECTED
        self._order_ws_error = ""
        self._order_event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10_000)
        self._seen_fills: set[str] = set()  # Dedup key: f"{order_id}:{trade_no or fill_qty}"
        self._order_cum_filled: dict[
            str, float
        ] = {}  # Track cum filled per order_id for delta calc

    # ── capabilities & environment ─────────────────────────────────────

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

    @property
    def order_ws_state(self) -> str:
        return self._order_ws_state

    # ── lifecycle ──────────────────────────────────────────────────────

    def connect(self) -> None:
        """Verify the session with one read-only profile call and start order WS if enabled."""
        flow = FyersAuthFlow(self._http)
        ok, reason = flow.validate(self._app_id, self._token)
        if not ok:
            raise AuthError(f"FYERS session rejected: {reason}", code="AUTH")
        self._connected = True

        if self._enable_order_ws:
            self._start_order_ws()

    def disconnect(self) -> None:
        self._stop_order_ws()
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

        # If order WS is enabled, report warning if order WS is in error
        if self._enable_order_ws and self._order_ws_state == STATE_ERROR:
            return False, f"order socket error: {self._order_ws_error}"
        return True, "ok"

    def order_stream_health(self) -> tuple[bool, str]:
        """Dedicated health check for the order WebSocket stream."""
        if not self._enable_order_ws:
            return True, "order socket disabled (rest polling fallback active)"
        if self._order_ws_state == STATE_CONNECTED:
            return True, "connected"
        if self._order_ws_state == STATE_CONNECTING:
            return False, "connecting"
        if self._order_ws_state == STATE_RECONNECTING:
            return False, "reconnecting"
        if self._order_ws_state == STATE_ERROR:
            return False, f"error: {self._order_ws_error}"
        return False, "disconnected"

    def _start_order_ws(self) -> None:
        """Initialize and connect FyersOrderSocket."""
        if self._order_ws_state == STATE_CONNECTED and self._order_ws is not None:
            return

        self._order_ws_state = STATE_CONNECTING
        logger.info("ORDER_WS_CONNECTING broker=FYERS")

        token_str = (
            f"{self._app_id}:{self._token}"
            if not self._token.startswith(f"{self._app_id}:")
            else self._token
        )

        try:
            if self._order_socket_factory is not None:
                self._order_ws = self._order_socket_factory(
                    access_token=token_str,
                    on_orders=self._on_ws_orders,
                    on_trades=self._on_ws_trades,
                    on_error=self._on_ws_error,
                    on_connect=self._on_ws_connect,
                    on_close=self._on_ws_close,
                )
            else:
                from fyers_apiv3.FyersWebsocket.order_ws import FyersOrderSocket

                self._order_ws = FyersOrderSocket(
                    access_token=token_str,
                    on_orders=self._on_ws_orders,
                    on_trades=self._on_ws_trades,
                    on_error=self._on_ws_error,
                    on_connect=self._on_ws_connect,
                    on_close=self._on_ws_close,
                )

            if hasattr(self._order_ws, "connect"):
                self._order_ws.connect()
            if hasattr(self._order_ws, "subscribe"):
                self._order_ws.subscribe(data_type="orders,trades")
        except Exception as exc:
            self._order_ws_state = STATE_ERROR
            self._order_ws_error = str(exc)
            logger.error("ORDER_WS_INIT_FAILED broker=FYERS reason=%s", exc)

    def _stop_order_ws(self) -> None:
        """Disconnect and cleanup FyersOrderSocket."""
        if self._order_ws is not None:
            try:
                if hasattr(self._order_ws, "close_connection"):
                    self._order_ws.close_connection()
            except Exception as exc:
                logger.warning("ORDER_WS_CLOSE_WARN broker=FYERS reason=%s", exc)
        self._order_ws = None
        self._order_ws_state = STATE_DISCONNECTED
        logger.info("ORDER_WS_DISCONNECTED broker=FYERS")

    def _on_ws_connect(self) -> None:
        self._order_ws_state = STATE_CONNECTED
        self._order_ws_error = ""
        logger.info("ORDER_WS_CONNECTED broker=FYERS")
        if self._order_ws is not None and hasattr(self._order_ws, "subscribe"):
            try:
                self._order_ws.subscribe(data_type="orders,trades")
            except Exception as exc:
                logger.error("ORDER_WS_SUBSCRIBE_FAILED broker=FYERS reason=%s", exc)

    def _on_ws_close(self, *args: Any, **_kwargs: Any) -> None:
        if self._connected:
            self._order_ws_state = STATE_DISCONNECTED
        logger.info("ORDER_WS_CLOSED broker=FYERS args=%s", args)

    def _on_ws_error(self, message: Any) -> None:
        self._order_ws_state = STATE_ERROR
        self._order_ws_error = str(message)
        logger.error("ORDER_WS_ERROR broker=FYERS error=%s", message)

    def _on_ws_orders(self, msg: Any) -> None:
        """Handle normalized order update from FyersOrderSocket."""
        if not isinstance(msg, dict):
            return
        order_dict = msg.get("orders") if "orders" in msg else msg
        if not isinstance(order_dict, dict):
            return

        events = self._parse_ws_order_event(order_dict)
        for ev in events:
            try:
                self._order_event_queue.put_nowait(ev)
            except queue.Full:
                logger.warning("ORDER_EVENT_QUEUE_FULL dropping event")

    def _on_ws_trades(self, msg: Any) -> None:
        """Handle normalized trade update from FyersOrderSocket."""
        if not isinstance(msg, dict):
            return
        trade_dict = msg.get("trades") if "trades" in msg else msg
        if not isinstance(trade_dict, dict):
            return

        ev = self._parse_ws_trade_event(trade_dict)
        if ev is not None:
            try:
                self._order_event_queue.put_nowait(ev)
            except queue.Full:
                logger.warning("ORDER_EVENT_QUEUE_FULL dropping trade event")

    def _parse_ws_order_event(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse FYERS WebSocket order dict into VAYREN events."""
        events: list[dict[str, Any]] = []
        tag = str(order.get("orderTag") or order.get("ordertag") or "")
        order_id = str(order.get("id") or order.get("id_fyers") or "")
        if not tag and order_id in self._seen_client_ids.values():
            # Invert lookup
            for k, v in self._seen_client_ids.items():
                if v == order_id:
                    tag = k
                    break

        if not tag:
            return []

        status = order.get("status")
        # 1: Cancelled, 2: Traded/Filled, 4: Transit, 5: Rejected, 6: Pending/Open
        # Check ACK/Open
        if status in (6, "PENDING", "OPEN", 4, "TRANSIT"):
            events.append(
                {
                    "type": "ack",
                    "client_order_id": tag,
                    "broker_order_id": order_id,
                }
            )

        # Check fills from order update if trades wasn't received
        cum_filled = 0.0
        try:
            cum_filled = float(order.get("filledQty") or order.get("qty_filled") or 0.0)
        except (TypeError, ValueError):
            cum_filled = 0.0

        prev_cum = self._order_cum_filled.get(order_id, 0.0)
        if cum_filled > prev_cum:
            delta_qty = cum_filled - prev_cum
            self._order_cum_filled[order_id] = cum_filled
            fill_dedup_key = f"{order_id}:cum_{cum_filled}"
            if fill_dedup_key not in self._seen_fills:
                self._seen_fills.add(fill_dedup_key)
                try:
                    price = float(order.get("tradedPrice") or order.get("price_traded") or 0.0)
                except (TypeError, ValueError):
                    price = 0.0
                side_val = order.get("side") or order.get("tran_side")
                side = "BUY" if side_val in (1, "BUY") else "SELL"
                partial = status not in _TERMINAL_STATUSES
                events.append(
                    {
                        "type": "fill",
                        "client_order_id": tag,
                        "broker_order_id": order_id,
                        "fill": SimpleNamespace(
                            client_order_id=tag,
                            broker_order_id=order_id,
                            symbol=str(order.get("symbol") or ""),
                            side=side,
                            fill_qty=delta_qty,
                            fill_price=price,
                            commission=0.0,
                            timestamp=str(
                                order.get("orderDateTime") or order.get("time_oms") or ""
                            ),
                            partial=partial,
                        ),
                    }
                )

        if status in (5, "REJECTED"):
            events.append(
                {
                    "type": "reject",
                    "client_order_id": tag,
                    "broker_order_id": order_id,
                    "reason": str(order.get("message") or order.get("oms_msg") or "order rejected"),
                }
            )
        elif status in (1, "CANCELLED"):
            events.append(
                {
                    "type": "cancel",
                    "client_order_id": tag,
                    "broker_order_id": order_id,
                }
            )

        return events

    def _parse_ws_trade_event(self, trade: dict[str, Any]) -> dict[str, Any] | None:
        """Parse FYERS WebSocket trade/execution print into a VAYREN fill event."""
        order_id = str(trade.get("orderNumber") or trade.get("id") or "")
        trade_id = str(trade.get("tradeNumber") or trade.get("id_fill") or "")
        tag = str(trade.get("orderTag") or trade.get("ordertag") or "")

        if not tag and order_id in self._seen_client_ids.values():
            for k, v in self._seen_client_ids.items():
                if v == order_id:
                    tag = k
                    break

        if not tag:
            return None

        # Deduplicate trade execution by tradeNumber
        if trade_id:
            dedup_key = f"{order_id}:trade_{trade_id}"
        else:
            dedup_key = f"{order_id}:trade_{time.time()}"
        if trade_id and dedup_key in self._seen_fills:
            return None
        self._seen_fills.add(dedup_key)

        try:
            qty = float(trade.get("tradedQty") or trade.get("qty_traded") or 0.0)
            price = float(trade.get("tradePrice") or trade.get("price_traded") or 0.0)
        except (TypeError, ValueError):
            return None

        if qty <= 0:
            return None

        # Update cum filled
        prev_cum = self._order_cum_filled.get(order_id, 0.0)
        self._order_cum_filled[order_id] = prev_cum + qty

        side_val = trade.get("side") or trade.get("tran_side")
        side = "BUY" if side_val in (1, "BUY") else "SELL"

        return {
            "type": "fill",
            "client_order_id": tag,
            "broker_order_id": order_id,
            "fill": SimpleNamespace(
                client_order_id=tag,
                broker_order_id=order_id,
                symbol=str(trade.get("symbol") or ""),
                side=side,
                fill_qty=qty,
                fill_price=price,
                commission=0.0,
                timestamp=str(trade.get("orderDateTime") or trade.get("fill_time") or ""),
                # Conservative: LiveSession tracks remaining quantity via order state.
                partial=True,
            ),
        }

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

    # ── trading surface ────────────────────────────────────────────────

    def place_order(
        self, plan: Any, client_order_id: str, idempotency_key: str | None = None
    ) -> str:
        """Submit one order to FYERS API v3. Returns the real broker order id.

        Duplicate ``client_order_id`` adopts existing broker order.
        Timeouts reconcile by tag before raising.
        Structured logging records requests and responses without secrets.
        """
        _ = idempotency_key
        self._require_usable()

        if not client_order_id:
            raise BrokerError("client order id required", code=ErrorCode.INVALID_REQUEST)

        # Idempotency check 1: in-memory cache
        if client_order_id in self._seen_client_ids:
            return self._seen_client_ids[client_order_id]

        # Idempotency check 2: active open orders tag scan
        adopted = self._find_by_tag(client_order_id)
        if adopted is not None:
            self._seen_client_ids[client_order_id] = adopted
            return adopted

        # Format and validate parameters
        symbol = self._venue_symbol(plan)
        side_val, side_str = self._venue_side(plan)
        qty = self._venue_quantity(plan)
        order_type_val, order_type_str, limit_price, stop_price = self._venue_price_and_type(plan)
        product_type = self._venue_product(plan)
        validity = str(getattr(plan, "time_in_force", "DAY") or "DAY").upper()
        if validity not in ("DAY", "IOC"):
            validity = "DAY"

        # Log ORDER_REQUEST safely (never credentials)
        logger.info(
            "ORDER_REQUEST broker=FYERS mode=LIVE symbol=%s side=%s qty=%d order_type=%s",
            symbol,
            side_str,
            qty,
            order_type_str,
        )

        payload: dict[str, Any] = {
            "symbol": symbol,
            "qty": qty,
            "type": order_type_val,
            "side": side_val,
            "productType": product_type,
            "limitPrice": limit_price,
            "stopPrice": stop_price,
            "validity": validity,
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag": str(client_order_id),
        }

        try:
            resp = self._post("orders/sync", payload)
        except Exception as exc:
            # Check if order landed despite network timeout
            resolved = self._resolve_unknown_tag(client_order_id, exc)
            if resolved is not None:
                return resolved

            logger.error("ORDER_REJECTED broker=FYERS reason=%s", str(exc))
            if isinstance(exc, BrokerError):
                raise
            raise self._mapped(exc, "order submission failed") from None

        # Verify FYERS response structure
        # Success: {"s": "ok", "code": 1101, "message": "Order submitted successfully", "id": "..."}
        # Failure: {"s": "error", "code": -..., "message": "..."}
        if not isinstance(resp, dict):
            logger.error("ORDER_REJECTED broker=FYERS reason=malformed broker response")
            raise BrokerError("malformed broker response", code=ErrorCode.NETWORK_ERROR)

        status_flag = resp.get("s")
        if status_flag != "ok":
            msg = str(resp.get("message") or "order rejected by venue")
            code_num = resp.get("code")
            logger.error("ORDER_REJECTED broker=FYERS reason=%s (code=%s)", msg, code_num)
            raise self._error_from_fyers_response(resp)

        broker_id = str(resp.get("id") or "").strip()
        if not broker_id:
            logger.error("ORDER_REJECTED broker=FYERS reason=no broker order id in response")
            raise BrokerError(
                "FYERS response missing broker order id", code=ErrorCode.NETWORK_ERROR
            )

        logger.info(
            "ORDER_RESPONSE broker=FYERS broker_order_id=%s status=ACCEPTED",
            broker_id,
        )

        self._seen_client_ids[client_order_id] = broker_id
        return broker_id

    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order on FYERS API v3."""
        self._require_usable()
        if not broker_order_id:
            raise BrokerError("broker order id required", code=ErrorCode.INVALID_REQUEST)

        payload = {"id": str(broker_order_id)}
        try:
            resp = self._delete("orders/sync", payload)
        except Exception as exc:
            if self._is_terminal_miss(exc):
                return False
            raise self._mapped(exc, "cancel failed") from None

        if isinstance(resp, dict) and resp.get("s") == "ok":
            return True
        if isinstance(resp, dict) and self._is_terminal_miss_msg(str(resp.get("message", ""))):
            return False
        raise self._error_from_fyers_response(resp)

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool:
        """Modify an active order on FYERS API v3."""
        self._require_usable()
        if not broker_order_id:
            raise BrokerError("broker order id required", code=ErrorCode.INVALID_REQUEST)

        payload: dict[str, Any] = {"id": str(broker_order_id)}
        if quantity is not None:
            if quantity <= 0 or float(quantity) != int(float(quantity)):
                raise BrokerError(
                    "modify quantity must be a positive whole unit",
                    code=ErrorCode.INVALID_REQUEST,
                )
            payload["qty"] = int(float(quantity))

        if price is not None:
            if price <= 0:
                raise BrokerError("modify price must be positive", code=ErrorCode.INVALID_REQUEST)
            payload["limitPrice"] = float(price)
            payload["type"] = 1  # Limit

        try:
            resp = self._put("orders/sync", payload)
        except Exception as exc:
            if self._is_terminal_miss(exc):
                return False
            raise self._mapped(exc, "modify failed") from None

        if isinstance(resp, dict) and resp.get("s") == "ok":
            return True
        if isinstance(resp, dict) and self._is_terminal_miss_msg(str(resp.get("message", ""))):
            return False
        raise self._error_from_fyers_response(resp)

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        """Drain real-time WebSocket order/trade events first; fallback to REST poll."""
        self._require_usable()

        # 1. Drain WebSocket order/trade events
        ws_events: list[dict[str, Any]] = []
        while not self._order_event_queue.empty():
            try:
                ws_events.append(self._order_event_queue.get_nowait())
            except queue.Empty:
                break

        if ws_events:
            return tuple(ws_events)

        # 2. Polling fallback if order WS not connected or no WS events arrived
        now = time.time()
        if now - self._last_poll_epoch < self._poll_interval_s and self._cached_snapshot:
            return ()
        self._last_poll_epoch = now

        try:
            raw_orders = self._order_snapshot()
        except Exception:
            return ()

        snapshot = {order["order_id"]: order for order in raw_orders}
        events: list[dict[str, Any]] = []
        for order_id, order in snapshot.items():
            previous = self._last_orders.get(order_id)
            events.extend(self._diff_order(previous, order))

        self._last_orders = snapshot
        self._cached_snapshot = tuple(snapshot.values())
        return tuple(events)

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        """Live venues ignore reference-price settlement."""

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        return None

    # ── mapping helpers ────────────────────────────────────────────────

    def _venue_symbol(self, plan: Any) -> str:
        sym = str(getattr(plan, "symbol", "") or "").strip()
        if not sym:
            raise BrokerError("order symbol required", code=ErrorCode.INVALID_SYMBOL)
        if ":" not in sym:
            return f"NSE:{sym}-EQ"
        return sym

    def _venue_side(self, plan: Any) -> tuple[int, str]:
        raw_side = str(getattr(plan, "side", "") or "").strip().upper()
        if raw_side in ("1", "BUY"):
            return 1, "BUY"
        if raw_side in ("-1", "SELL"):
            return -1, "SELL"
        raise BrokerError(f"invalid order side: {raw_side}", code=ErrorCode.INVALID_REQUEST)

    def _venue_quantity(self, plan: Any) -> int:
        try:
            quantity = float(plan.quantity)
        except (TypeError, ValueError):
            raise BrokerError("order quantity unreadable", code=ErrorCode.INVALID_REQUEST) from None
        if quantity <= 0:
            raise BrokerError("order quantity must be positive", code=ErrorCode.INVALID_REQUEST)
        if float(quantity) != int(float(quantity)):
            raise BrokerError(
                f"venue needs whole units, got {quantity}", code=ErrorCode.INVALID_REQUEST
            )
        return int(float(quantity))

    def _venue_price_and_type(self, plan: Any) -> tuple[int, str, float, float]:
        order_type = str(getattr(plan, "order_type", "MARKET") or "MARKET").upper()
        # Look for trigger/stop price on plan
        trigger = getattr(plan, "trigger_price", None)
        if trigger is None:
            trigger = getattr(plan, "stop_price", None)
        # Check bracket for stop price if present
        if trigger is None and hasattr(plan, "bracket"):
            bracket = getattr(plan, "bracket", ())
            if isinstance(bracket, (list, tuple)):
                for leg in bracket:
                    if isinstance(leg, dict) and "stop_price" in leg:
                        trigger = leg.get("stop_price")
                        break

        stop_price = 0.0
        if trigger is not None:
            try:
                stop_price = float(trigger)
            except (TypeError, ValueError):
                stop_price = 0.0

        if order_type in ("1", "LIMIT"):
            try:
                price = float(getattr(plan, "limit_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                raise BrokerError(
                    "limit price unreadable", code=ErrorCode.INVALID_REQUEST
                ) from None
            if price <= 0:
                raise BrokerError("limit price must be positive", code=ErrorCode.INVALID_REQUEST)
            if stop_price > 0:
                return 4, "STOP_LIMIT", round(price, 2), round(stop_price, 2)
            return 1, "LIMIT", round(price, 2), 0.0

        if order_type in ("2", "MARKET"):
            if stop_price > 0:
                return 3, "STOP_MARKET", 0.0, round(stop_price, 2)
            return 2, "MARKET", 0.0, 0.0

        if order_type in ("3", "SLM", "STOP_MARKET"):
            if stop_price <= 0:
                raise BrokerError("stop price required for SL-M", code=ErrorCode.INVALID_REQUEST)
            return 3, "STOP_MARKET", 0.0, round(stop_price, 2)

        if order_type in ("4", "SLL", "STOP_LIMIT"):
            try:
                price = float(getattr(plan, "limit_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                raise BrokerError(
                    "limit price unreadable", code=ErrorCode.INVALID_REQUEST
                ) from None
            if price <= 0 or stop_price <= 0:
                raise BrokerError(
                    "limit price and stop price required for SL-L", code=ErrorCode.INVALID_REQUEST
                )
            return 4, "STOP_LIMIT", round(price, 2), round(stop_price, 2)

        raise BrokerError(
            f"unsupported order type: {order_type}", code=ErrorCode.CAPABILITY_UNSUPPORTED
        )

    def _venue_product(self, plan: Any) -> str:
        product = str(getattr(plan, "product", "") or getattr(plan, "product_type", "")).upper()
        if product in ("CNC", "DELIVERY"):
            return "CNC"
        if product in ("MARGIN", "NRML"):
            return "MARGIN"
        return "INTRADAY"

    def _order_snapshot(self) -> list[dict[str, Any]]:
        body = self._get("orders")
        rows = body.get("orderBook", []) if isinstance(body, dict) else []
        if not isinstance(rows, list):
            return []
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "order_id": str(row.get("id", "")),
                    "tag": str(row.get("orderTag", "") or ""),
                    "status": row.get("status", 0),
                    "symbol": str(row.get("symbol", "")),
                    "side": row.get("side", 1),
                    "qty": row.get("qty", 0),
                    "filled_qty": row.get("filledQty", 0),
                    "remaining_qty": row.get("remainingQuantity", 0),
                    "traded_price": row.get("tradedPrice", 0.0),
                    "order_date_time": str(row.get("orderDateTime", "")),
                    "message": str(row.get("message", "") or ""),
                }
            )
        return normalized

    def _find_by_tag(self, tag: str) -> str | None:
        try:
            for order in self._order_snapshot():
                if order["tag"] == tag and order["status"] not in _TERMINAL_STATUSES:
                    return order["order_id"]
        except Exception:
            return None
        return None

    def _resolve_unknown_tag(self, tag: str, cause: Exception) -> str | None:
        name = type(cause).__name__
        msg = str(cause).lower()
        if "timeout" not in msg and "network" not in name.lower() and "oserror" not in name.lower():
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

        prev_filled = float(previous.get("filled_qty", 0) or 0) if previous else 0.0
        try:
            filled = float(order.get("filled_qty", 0) or 0)
        except (TypeError, ValueError):
            filled = 0.0

        if filled > prev_filled:
            try:
                price = float(order.get("traded_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            side = "BUY" if order.get("side") == 1 else "SELL"
            partial = order.get("status") not in _TERMINAL_STATUSES
            events.append(
                {
                    "type": "fill",
                    "client_order_id": tag,
                    "broker_order_id": order["order_id"],
                    "fill": SimpleNamespace(
                        client_order_id=tag,
                        broker_order_id=order["order_id"],
                        symbol=order["symbol"],
                        side=side,
                        fill_qty=filled - prev_filled,
                        fill_price=price,
                        commission=0.0,
                        timestamp=order.get("order_date_time", ""),
                        partial=partial,
                    ),
                }
            )

        status = order.get("status")
        # 5: Rejected, 6: Pending/Cancelled depending on venue
        if status in (5, "REJECTED") and (
            previous is None or previous.get("status") not in (5, "REJECTED")
        ):
            events.append(
                {
                    "type": "reject",
                    "client_order_id": tag,
                    "broker_order_id": order["order_id"],
                    "reason": order.get("message", "venue rejected order"),
                }
            )
        if status in (1, 6, "CANCELLED") and (
            previous is None or previous.get("status") not in (1, 6, "CANCELLED")
        ):
            events.append(
                {
                    "type": "cancel",
                    "client_order_id": tag,
                    "broker_order_id": order["order_id"],
                }
            )
        return events

    def _require_usable(self) -> None:
        if not self._connected:
            raise BrokerError("FYERS venue not connected", code=ErrorCode.NOT_CONNECTED)
        if not self._app_id or not self._token:
            raise BrokerError(
                "FYERS credentials/session missing", code=ErrorCode.CREDENTIALS_NOT_READY
            )

    def _error_from_fyers_response(self, resp: Any) -> BrokerError:
        if not isinstance(resp, dict):
            return BrokerError("malformed broker response", code=ErrorCode.NETWORK_ERROR)
        msg = str(resp.get("message") or "venue error").strip()
        code = resp.get("code")
        lower_msg = msg.lower()
        if code in (-8, -15, -16, -17) or "unauthorized" in lower_msg or "expired" in lower_msg:
            return BrokerError(f"auth failure: {msg}", code=ErrorCode.AUTHENTICATION_FAILED)
        if "insufficient" in lower_msg or "fund" in lower_msg or "margin" in lower_msg:
            return BrokerError(f"insufficient funds: {msg}", code=ErrorCode.INVALID_REQUEST)
        if "symbol" in lower_msg or ("token" in lower_msg and "invalid token" in lower_msg):
            return BrokerError(f"invalid symbol/token: {msg}", code=ErrorCode.INVALID_SYMBOL)
        if "closed" in lower_msg or "market" in lower_msg:
            return BrokerError(f"market closed: {msg}", code=ErrorCode.INVALID_REQUEST)
        return BrokerError(f"venue error: {msg}", code=ErrorCode.INVALID_REQUEST)

    def _mapped(self, exc: Exception, fallback: str) -> BrokerError:
        if isinstance(exc, BrokerError):
            return exc
        text = str(exc)
        lower_text = text.lower()
        exc_type = type(exc).__name__.lower()
        if "auth" in lower_text or "unauthorized" in lower_text or "401" in text:
            return BrokerError(
                f"authentication failure: {text}", code=ErrorCode.AUTHENTICATION_FAILED
            )
        if (
            "timeout" in lower_text
            or "timeout" in exc_type
            or "network" in lower_text
            or "unreachable" in lower_text
            or "connection" in lower_text
        ):
            return BrokerError(f"network failure: {text}", code=ErrorCode.NETWORK_ERROR)
        return BrokerError(f"{fallback}: {text}", code=ErrorCode.INVALID_REQUEST)

    def _is_terminal_miss(self, exc: Exception) -> bool:
        return self._is_terminal_miss_msg(str(exc))

    def _is_terminal_miss_msg(self, text: str) -> bool:
        lower = text.lower()
        return any(
            token in lower
            for token in ("complete", "cancelled", "rejected", "invalid order", "not found")
        )

    # ── internals ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"{self._app_id}:{self._token}"}

    def _get(self, path: str) -> dict[str, Any]:
        body = self._http.get_json(f"{API_BASE}/{path}", headers=self._headers())
        if not isinstance(body, dict):
            raise AuthError(f"venue returned an unreadable {path} response", code="NETWORK")
        return body

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._http.post_json(f"{API_BASE}/{path}", payload=payload, headers=self._headers())
        if not isinstance(body, dict):
            raise BrokerError(
                f"venue returned an unreadable {path} response", code=ErrorCode.NETWORK_ERROR
            )
        return body

    def _put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._http.put_json(f"{API_BASE}/{path}", payload=payload, headers=self._headers())
        if not isinstance(body, dict):
            raise BrokerError(
                f"venue returned an unreadable {path} response", code=ErrorCode.NETWORK_ERROR
            )
        return body

    def _delete(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._http.delete_json(
            f"{API_BASE}/{path}", payload=payload, headers=self._headers()
        )
        if not isinstance(body, dict):
            raise BrokerError(
                f"venue returned an unreadable {path} response", code=ErrorCode.NETWORK_ERROR
            )
        return body


def _safe_amount(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return number


__all__ = ["FyersSessionAdapter", "TRADING_CAPABILITIES"]
