"""FyersLiveMarketData — real-time market data WebSocket adapter for FYERS.

Streams live ticks using FYERS API v3 DataSocket and normalizes them to
broker-neutral quote dicts ``{symbol, price, timestamp[, volume, ...]}`` —
the same contract as the Zerodha market-data face, folded exactly once by
the execution ``BrokerFeedProvider`` into its own closed-candle buckets.
No execution-domain objects are built here (provider SDKs never import
execution); tracks transport and heartbeat health, with
auto-reconnection and subscription memory.
"""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.vocab import BrokerError, ErrorCode

logger = logging.getLogger(__name__)

MARKET_DATA_CAPABILITIES: CapabilitySet = capability_set(
    {
        Domain.MARKET_DATA: (
            Caps.MD_CANDLE_STREAM,
            Caps.MD_QUOTES,
            Caps.MD_DEPTH,
            Caps.MD_HEARTBEAT,
        )
    }
)

# FYERS DataSocket connection states
STATE_DISCONNECTED = "DISCONNECTED"
STATE_CONNECTING = "CONNECTING"
STATE_CONNECTED = "CONNECTED"
STATE_RECONNECTING = "RECONNECTING"
STATE_ERROR = "ERROR"


class FyersLiveMarketData:
    """Live FYERS market data adapter implementing MarketDataFace & MarketDataProvider."""

    name = "fyers"

    def __init__(
        self,
        app_id: str,
        access_token: str,
        socket_factory: Callable[..., Any] | None = None,
        max_queue_size: int = 50_000,
    ) -> None:
        self._app_id = str(app_id or "").strip()
        self._token = str(access_token or "").strip()
        self._socket_factory = socket_factory
        self._max_queue_size = max_queue_size
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue_size)
        self._subscriptions: set[str] = set()
        self._timeframe: str = "1m"
        self._state = STATE_DISCONNECTED
        self._last_error_reason: str = ""
        self._ws_client: Any = None
        self._is_closed = False

    @property
    def capabilities(self) -> CapabilitySet:
        return MARKET_DATA_CAPABILITIES

    @property
    def state(self) -> str:
        return self._state

    def connect(self) -> None:
        """Establish WebSocket connection to FYERS data stream (idempotent)."""
        if self._is_closed:
            raise BrokerError("FyersLiveMarketData is closed", code=ErrorCode.NOT_CONNECTED)
        if self._state == STATE_CONNECTED and self._ws_client is not None:
            return

        if not self._app_id or not self._token:
            self._state = STATE_ERROR
            self._last_error_reason = "credentials or session token missing"
            raise BrokerError(self._last_error_reason, code=ErrorCode.CREDENTIALS_NOT_READY)

        self._state = STATE_CONNECTING
        logger.info("MARKET_WS_CONNECTING broker=FYERS")

        # Format token required by FyersDataSocket: appId:token or token
        token_str = (
            f"{self._app_id}:{self._token}"
            if not self._token.startswith(f"{self._app_id}:")
            else self._token
        )

        try:
            if self._socket_factory is not None:
                self._ws_client = self._socket_factory(
                    access_token=token_str,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_connect=self._on_connect,
                    on_close=self._on_close,
                )
            else:
                from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket

                self._ws_client = FyersDataSocket(
                    access_token=token_str,
                    litemode=False,
                    reconnect=True,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_connect=self._on_connect,
                    on_close=self._on_close,
                )

            if hasattr(self._ws_client, "connect"):
                self._ws_client.connect()
        except Exception as exc:
            self._state = STATE_ERROR
            self._last_error_reason = f"websocket connection failure: {exc}"
            logger.error("MARKET_WS_ERROR broker=FYERS reason=%s", exc)
            raise BrokerError(self._last_error_reason, code=ErrorCode.NETWORK_ERROR) from exc

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:
        """Start streaming for symbols (idempotent)."""
        self._timeframe = timeframe or "1m"
        self.connect()
        if symbols:
            self.subscribe(symbols)

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        """Add symbols to active subscriptions and subscribe via socket."""
        to_add = [self._normalize_symbol(s) for s in symbols if s]
        if not to_add:
            return
        self._subscriptions.update(to_add)

        if self._state == STATE_CONNECTED and self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "subscribe"):
                    self._ws_client.subscribe(symbols=to_add, data_type="SymbolUpdate")
                    logger.info("MARKET_WS_SUBSCRIBED broker=FYERS symbols=%s", to_add)
            except Exception as exc:
                logger.error("MARKET_WS_SUBSCRIBE_FAILED broker=FYERS reason=%s", exc)

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        """Remove symbols from active subscriptions."""
        to_remove = [self._normalize_symbol(s) for s in symbols if s]
        if not to_remove:
            return
        self._subscriptions.difference_update(to_remove)

        if self._state == STATE_CONNECTED and self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "unsubscribe"):
                    self._ws_client.unsubscribe(symbols=to_remove)
                    logger.info("MARKET_WS_UNSUBSCRIBED broker=FYERS symbols=%s", to_remove)
            except Exception as exc:
                logger.error("MARKET_WS_UNSUBSCRIBE_FAILED broker=FYERS reason=%s", exc)

    def poll(self) -> tuple[dict[str, Any], ...]:
        """Drain currently available normalized quote dicts (non-blocking)."""
        quotes: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                quotes.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return tuple(quotes)

    def health(self) -> tuple[bool, str]:
        """(healthy, reason) — reports WebSocket transport and connection health."""
        if self._is_closed:
            return False, "closed"
        if not self._app_id or not self._token:
            return False, "CREDENTIALS not ready"
        if self._state == STATE_CONNECTED:
            return True, "connected"
        if self._state == STATE_CONNECTING:
            return False, "connecting"
        if self._state == STATE_RECONNECTING:
            return False, "reconnecting"
        if self._state == STATE_ERROR:
            return False, f"error: {self._last_error_reason}"
        return False, "disconnected"

    def disconnect(self) -> None:
        """Drop transport while keeping subscription memory for reconnect."""
        if self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "close_connection"):
                    self._ws_client.close_connection()
            except Exception as exc:
                logger.warning("MARKET_WS_DISCONNECT_WARN broker=FYERS reason=%s", exc)
        self._state = STATE_DISCONNECTED
        logger.info("MARKET_WS_DISCONNECTED broker=FYERS")

    def reconnect(self) -> None:
        """Drop and re-establish transport, resuming subscriptions."""
        self._state = STATE_RECONNECTING
        logger.info("MARKET_WS_RECONNECTING broker=FYERS")
        self.disconnect()
        self.connect()
        # Resubscribe preserved symbols
        if self._subscriptions and self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "subscribe"):
                    self._ws_client.subscribe(
                        symbols=list(self._subscriptions), data_type="SymbolUpdate"
                    )
            except Exception as exc:
                logger.error("MARKET_WS_RESUBSCRIBE_FAILED broker=FYERS reason=%s", exc)

    def close(self) -> None:
        """Stop streaming and release all resources."""
        self._is_closed = True
        self.disconnect()
        # Drain remaining queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    # ── Socket callbacks ───────────────────────────────────────────────

    def _on_connect(self) -> None:
        self._state = STATE_CONNECTED
        self._last_error_reason = ""
        logger.info("MARKET_WS_CONNECTED broker=FYERS")
        # Resubscribe any registered symbols
        if self._subscriptions and self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "subscribe"):
                    self._ws_client.subscribe(
                        symbols=list(self._subscriptions), data_type="SymbolUpdate"
                    )
                    logger.info(
                        "MARKET_WS_RESUBSCRIBED broker=FYERS symbols=%s", list(self._subscriptions)
                    )
            except Exception as exc:
                logger.error("MARKET_WS_RESUBSCRIBE_ON_CONNECT_FAILED reason=%s", exc)

    def _on_close(self, *args: Any, **_kwargs: Any) -> None:
        if not self._is_closed:
            self._state = STATE_DISCONNECTED
        logger.info("MARKET_WS_CLOSED broker=FYERS args=%s", args)

    def _on_error(self, message: Any) -> None:
        self._state = STATE_ERROR
        self._last_error_reason = str(message)
        logger.error("MARKET_WS_ERROR broker=FYERS error=%s", message)

    def _on_message(self, message: Any) -> None:
        """Ingest one raw tick dict from FYERS DataSocket into a normalized quote."""
        if not isinstance(message, dict):
            return

        normalized_events = self.normalize_tick(message)
        for ev in normalized_events:
            try:
                self._queue.put_nowait(ev)
            except queue.Full:
                logger.warning("MARKET_QUEUE_OVERFLOW broker=FYERS dropping oldest tick")
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(ev)
                except Exception:
                    pass

    # ── Payload normalization ──────────────────────────────────────────

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def normalize_tick(self, msg: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """Convert one FYERS raw tick into a normalized quote dict.

        Exactly ONE dict per tick — ``{symbol, price, timestamp[, volume,
        bid/ask legs, OHLC legs]}`` — so the execution ``BrokerFeedProvider``
        folds each tick once into its own closed-candle buckets (emitting a
        trade dict plus a candle dict for the same tick would double-fold).
        A tick without a positive price yields nothing: there is no honest
        quote to fold.
        """
        symbol = str(msg.get("symbol") or "").strip()
        if not symbol:
            return ()
        ts_str = self._extract_timestamp(msg)
        price = self._safe_float(msg.get("ltp"))
        bid = self._safe_float(msg.get("bid_price") or msg.get("bidPrice1"))
        ask = self._safe_float(msg.get("ask_price") or msg.get("askPrice1"))
        if price <= 0:
            if bid > 0 and ask > 0:
                price = (bid + ask) / 2.0
            elif bid > 0:
                price = bid
            elif ask > 0:
                price = ask
        if price <= 0:
            return ()
        try:
            volume = float(msg.get("vol_traded_today") or msg.get("last_traded_qty") or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        quote: dict[str, Any] = {
            "symbol": symbol,
            "price": price,
            "timestamp": ts_str,
            "volume": volume,
        }
        bid_qty = self._safe_float(msg.get("bid_size") or msg.get("bidQty1"))
        ask_qty = self._safe_float(msg.get("ask_size") or msg.get("askQty1"))
        if bid > 0 or ask > 0:
            quote.update(bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty)
        open_p = self._safe_float(msg.get("open_price"))
        high_p = self._safe_float(msg.get("high_price"))
        low_p = self._safe_float(msg.get("low_price"))
        close_p = self._safe_float(msg.get("prev_close_price") or msg.get("ltp"))
        if open_p > 0 and high_p > 0 and low_p > 0 and close_p > 0:
            quote.update(open=open_p, high=high_p, low=low_p, close=close_p)
        return (quote,)

    def _extract_timestamp(self, msg: dict[str, Any]) -> str:
        """Parse tick timestamp to ISO-8601 UTC string."""
        raw_ts = msg.get("last_traded_time") or msg.get("exch_feed_time") or msg.get("timestamp")
        if raw_ts is not None:
            try:
                # FYERS sometimes sends Unix epoch timestamp in seconds
                epoch = float(raw_ts)
                if epoch > 0:
                    dt = datetime.fromtimestamp(epoch, tz=UTC)
                    return dt.isoformat()
            except (ValueError, TypeError, OverflowError):
                pass

        # Fallback to current UTC time
        return datetime.now(UTC).isoformat()

    def _normalize_symbol(self, symbol: str) -> str:
        s = str(symbol or "").strip()
        if not s:
            return ""
        if ":" not in s:
            return f"NSE:{s}-EQ"
        return s


__all__ = [
    "FyersLiveMarketData",
    "MARKET_DATA_CAPABILITIES",
    "STATE_CONNECTED",
    "STATE_CONNECTING",
    "STATE_DISCONNECTED",
    "STATE_ERROR",
    "STATE_RECONNECTING",
]
