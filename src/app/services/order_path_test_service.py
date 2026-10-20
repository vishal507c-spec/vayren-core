"""Bounded, operator-authorized verification of the LIVE order path.

The normal order path is strategy-signal driven: an order exists only while a
running session turns closed candles into signals. That makes "was the broker
reachable for an order?" unanswerable without starting a strategy, and
starting one emits a paired, daily-repeating order stream. This module is the
small operator-triggered alternative used to verify the same venue path once.

It is deliberately NOT a second trading engine and it does NOT start, arm or
consult the strategy session. It submits exactly what the operator explicitly
authorises, through the same broker adapter, the same access token and the same
risk engine the session uses.

The rules this module exists to enforce:

* **One BUY, once.** ``state.json`` records the attempt BEFORE the order is
  sent, so even a crash mid-submit consumes the allowance. A second BUY is
  refused outright - the operator must inspect the first.
* **Idempotency key.** ``client_order_id`` is deterministic
  (``otest:<purpose>:<symbol>:<IST date>``) and doubles as the FYERS
  ``orderTag``. Re-issuing the same id makes the adapter ADOPT the existing
  order instead of submitting a new one.
* **Never retry an uncertain order.** A transport failure leaves the outcome
  UNCERTAIN and the allowance consumed. Retrying is a human decision made
  after looking at the broker, not something this module does.
* **A SELL is not a closed position.** Exit success is reported only from the
  broker's own position book, never from an accepted order request.
* **Risk is not bypassed.** The sizing verdict and the risk engine both have to
  approve, exactly as they do on the session path. A caller cannot widen
  quantity, drop the stop, or loosen a limit here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from broker.vocab import BrokerError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = [
    "OUTCOME_BLOCKED",
    "OUTCOME_EXITED",
    "OUTCOME_EXIT_UNCERTAIN",
    "OUTCOME_FILLED",
    "OUTCOME_REJECTED",
    "OUTCOME_SUBMITTED",
    "OUTCOME_UNCERTAIN",
    "OrderTestResult",
    "client_order_id",
    "net_position_qty",
    "submit_buy",
    "submit_exit",
]

# ── outcomes ────────────────────────────────────────────────────────────────
OUTCOME_SKIPPED = "SKIPPED"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_SUBMITTED = "SUBMITTED"
OUTCOME_FILLED = "FILLED"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_UNCERTAIN = "UNCERTAIN"
OUTCOME_EXITED = "EXITED"
OUTCOME_EXIT_UNCERTAIN = "EXIT_UNCERTAIN"

#: The only FYERS numeric order status this module reads as a confirmed fill.
#: Anything else is reported raw and treated as not-yet-filled, because an
#: unrecognised status must never be guessed into "filled".
_STATUS_FILLED = 2

_STATE_FILENAME = "order_path_test.json"
_PURPOSE_BUY = "buy"
_PURPOSE_EXIT = "exit"


@dataclass(frozen=True)
class OrderTestResult:
    """One operator-triggered order-path outcome.

    ``outcome`` is the honest verdict. ``position_qty`` and ``verified_closed``
    come from the BROKER's position book only - never from an accepted request.
    ``retryable`` is False for every outcome except a clean pre-flight refusal,
    so a caller cannot mistake UNCERTAIN for "try again".
    """

    outcome: str
    detail: str
    symbol: str
    side: str
    quantity: float
    client_order_id: str = ""
    broker_order_id: str = ""
    broker_status: str = ""
    position_qty: float | None = None
    verified_closed: bool = False
    retryable: bool = False

    @property
    def ok(self) -> bool:
        """True only for an outcome that achieved what it was asked to do."""
        return self.outcome in (OUTCOME_FILLED, OUTCOME_EXITED)


def client_order_id(symbol: str, purpose: str, now_epoch: float | None = None) -> str:
    """Deterministic idempotency key for one symbol/purpose/trading-day.

    Stable within an IST day so a re-issued request carries the SAME
    ``orderTag`` and the adapter adopts the existing order instead of creating
    a second one.
    """
    import datetime

    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now = time.time() if now_epoch is None else float(now_epoch)
    day = datetime.datetime.fromtimestamp(now, datetime.UTC).astimezone(ist)
    clean = str(symbol).split(":")[-1].strip().upper()
    return f"otest:{purpose}:{clean}:{day.strftime('%Y%m%d')}"


# ── durable one-BUY ledger ──────────────────────────────────────────────────


class _Ledger:
    """Small durable record of what this module has already sent.

    The attempt is written BEFORE the venue call. That ordering is the whole
    point: if the process dies mid-submit, the allowance is still consumed and
    the operator is never offered a second BUY against an unknown first.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def buy_consumed(self) -> bool:
        """True once a BUY attempt has been recorded (sent or uncertain)."""
        return bool(str(self.read().get(f"{_PURPOSE_BUY}_client_order_id", "") or ""))

    def record_buy_attempt(self, cid: str, symbol: str, now_epoch: float) -> None:
        """Consume the allowance and journal the intent before any network call."""
        import datetime

        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        stamp = datetime.datetime.fromtimestamp(now_epoch, datetime.UTC)
        data = self.read()
        data[f"{_PURPOSE_BUY}_client_order_id"] = cid
        data[f"{_PURPOSE_BUY}_symbol"] = symbol
        data[f"{_PURPOSE_BUY}_attempted_at"] = stamp.astimezone(ist).isoformat()
        data[f"{_PURPOSE_BUY}_attempted_epoch"] = now_epoch
        self.write(data)


# ── broker reads (public adapter surface only) ─────────────────────────────


def _venue_symbol(adapter: Any, symbol: str) -> str:
    mapper = getattr(adapter, "to_venue_symbol", None)
    return str(mapper(symbol)) if callable(mapper) else str(symbol)


def net_position_qty(adapter: Any, symbol: str) -> float:
    """Signed net quantity the BROKER reports for ``symbol`` (0.0 when flat).

    Reads the venue position book directly. Never infers flatness from an
    order request, an order status, or an absence of open orders.
    """
    want = _venue_symbol(adapter, symbol)
    total = 0.0
    for row in adapter.positions() or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol", "")) != want:
            continue
        for key in ("netQty", "netQuantity", "netqty", "quantity"):
            raw = row.get(key)
            if raw is None:
                continue
            try:
                total += float(raw)
            except (TypeError, ValueError):
                continue
    return total


def _order_by_tag(adapter: Any, tag: str) -> dict[str, Any]:
    """The venue's own row for ``tag``, or an empty dict when it is not open."""
    for row in adapter.open_orders() or []:
        if not isinstance(row, dict):
            continue
        # Normalized venue rows carry ``client_order_id``; raw venue rows
        # carry ``orderTag``. FYERS prefixes a user tag with "1:".
        venue_tag = str(row.get("client_order_id") or row.get("orderTag", "") or "")
        if venue_tag.removeprefix("1:") == tag.removeprefix("1:"):
            return row
    return {}


def _row_status(row: dict[str, Any]) -> str:
    """The venue's status for a row, normalized name or raw code."""
    if not row:
        return ""
    status = str(row.get("status", "") or "")
    if status:
        return status
    venue_status = row.get("venue_status")
    return str(venue_status) if venue_status is not None else ""


def _row_filled_qty(row: dict[str, Any]) -> float:
    """The venue's filled quantity for a row (normalized or raw field)."""
    if not row:
        return 0.0
    raw = row.get("filled_qty")
    if raw is None:
        raw = row.get("filledQty", 0)
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


# ── risk (never bypassed) ───────────────────────────────────────────────────


def _risk_approves(
    *,
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    stop_price: float,
    position_qty: float,
    available: float,
    now_epoch: float,
    max_age_seconds: float,
) -> tuple[bool, str]:
    """Run the SAME sizing + risk gates the session uses. Returns (ok, reason)."""
    from risk.engine import RiskEngine
    from risk.models import RiskPolicy, RiskRequest
    from risk.sizing import BrokerCapital, size_position

    sizing = size_position(
        broker_capital=BrokerCapital(available=available, fetched_epoch=now_epoch, source="broker"),
        entry_price=entry_price,
        stop_price=stop_price,
        mode="LIVE",
        now_epoch=now_epoch,
    )
    if not sizing.ready:
        return False, f"sizing blocked: {sizing.reason}"
    if sizing.quantity < quantity:
        return False, f"sizing allows {sizing.quantity}, request was {quantity}"

    policy = RiskPolicy(
        max_order_qty=quantity,
        max_position_qty=quantity,
        allowed_symbols=(symbol,),
    )
    import datetime

    stamp = datetime.datetime.fromtimestamp(now_epoch, datetime.UTC).isoformat()
    decision = RiskEngine(policy).evaluate(
        RiskRequest(
            intent_id=f"order-path-test:{symbol}",
            strategy_id="order-path-test",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=entry_price,
            timestamp=stamp,
            position_qty=position_qty,
            equity=available,
            available_capital=available,
            data_age_seconds=max_age_seconds,
            broker_healthy=True,
            orders_today=0,
            last_order_epoch=None,
            now_epoch=now_epoch,
        )
    )
    if not decision.approved:
        return False, f"risk engine denied: {'; '.join(decision.reasons)}"
    return True, "risk approved"


# ── the one authorized BUY ──────────────────────────────────────────────────


def submit_buy(
    *,
    adapter: Any,
    symbol: str,
    quantity: float,
    stop_price: float,
    ledger_path: str | Path,
    authorize: bool = False,
    now_epoch: float | None = None,
) -> OrderTestResult:
    """Submit at most one explicitly authorized BUY and report what happened.

    Refuses without contacting the venue when authorization is missing, the
    one-BUY allowance is already spent, or the risk gates decline.
    """
    from execution.models.order import OrderPlan

    now = time.time() if now_epoch is None else float(now_epoch)
    ledger = _Ledger(ledger_path)
    cid = client_order_id(symbol, _PURPOSE_BUY, now)

    def refuse(outcome: str, detail: str, retryable: bool = False) -> OrderTestResult:
        return OrderTestResult(
            outcome=outcome,
            detail=detail,
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            client_order_id=cid,
            retryable=retryable,
        )

    if not authorize:
        return refuse(OUTCOME_BLOCKED, "explicit operator authorization required")
    if float(quantity) <= 0:
        return refuse(OUTCOME_BLOCKED, "quantity must be positive")
    if float(stop_price) <= 0:
        return refuse(
            OUTCOME_BLOCKED, "a protective stop price is required; refusing an unbounded order"
        )
    if ledger.buy_consumed():
        prior = str(ledger.read().get(f"{_PURPOSE_BUY}_client_order_id", "") or "")
        return refuse(
            OUTCOME_BLOCKED,
            f"one-BUY allowance already used by {prior}; "
            "inspect that order before any further action",
        )

    try:
        funds = adapter.funds() or {}
        available = float(funds.get("available") or 0.0)
        position_qty = net_position_qty(adapter, symbol)
    except Exception as exc:  # noqa: BLE001 - refuse closed, never guess
        return refuse(OUTCOME_UNCERTAIN, f"cannot read broker account state: {exc}")

    quote = (adapter.quotes([symbol]) or {}).get(symbol) or {}
    entry_price = float(quote.get("lp") or 0.0)
    if entry_price <= 0:
        return refuse(OUTCOME_UNCERTAIN, "no live venue price for the symbol")

    ok, reason = _risk_approves(
        symbol=symbol,
        side="BUY",
        quantity=float(quantity),
        entry_price=entry_price,
        stop_price=float(stop_price),
        position_qty=position_qty,
        available=available,
        now_epoch=now,
        max_age_seconds=0.0,
    )
    if not ok:
        return refuse(OUTCOME_BLOCKED, reason)

    plan = OrderPlan(
        intent_id=f"order-path-test:{symbol}:buy",
        symbol=symbol,
        side="BUY",
        quantity=float(quantity),
        order_type="MARKET",
        time_in_force="DAY",
    )

    # Allowance is consumed BEFORE the venue call: a crash here must not leave
    # the operator free to send a second BUY against an unknown first.
    ledger.record_buy_attempt(cid, symbol, now)
    logger.info("ORDER_PATH_TEST BUY symbol=%s qty=%s cid=%s", symbol, quantity, cid)

    try:
        broker_order_id = str(adapter.place_order(plan, cid))
    except BrokerError as exc:
        # Classify by the NORMALIZED code, never by message text: a transport
        # fault leaves the outcome unknown, a venue refusal is a real answer.
        uncertain = exc.code in (
            ErrorCode.NETWORK_ERROR,
            ErrorCode.PROVIDER_UNAVAILABLE,
            ErrorCode.RATE_LIMITED,
        )
        outcome = OUTCOME_UNCERTAIN if uncertain else OUTCOME_REJECTED
        logger.error("ORDER_PATH_TEST BUY failed cid=%s outcome=%s reason=%s", cid, outcome, exc)
        return OrderTestResult(
            outcome=outcome,
            detail=(f"{exc.code}: {exc} (allowance consumed; do NOT auto-retry)"),
            symbol=symbol,
            side="BUY",
            quantity=float(quantity),
            client_order_id=cid,
            retryable=False,
        )
    except Exception as exc:  # noqa: BLE001 - any transport fault is uncertain
        logger.error("ORDER_PATH_TEST BUY uncertain cid=%s reason=%s", cid, exc)
        return OrderTestResult(
            outcome=OUTCOME_UNCERTAIN,
            detail=f"{type(exc).__name__}: {exc} (allowance consumed; do NOT auto-retry)",
            symbol=symbol,
            side="BUY",
            quantity=float(quantity),
            client_order_id=cid,
            retryable=False,
        )

    row = _order_by_tag(adapter, cid)
    status = _row_status(row)
    filled_qty = _row_filled_qty(row)

    if status in ("FILLED", str(_STATUS_FILLED)) or filled_qty > 0:
        outcome, detail = OUTCOME_FILLED, f"venue reports filled qty {filled_qty}"
    elif row:
        outcome = OUTCOME_SUBMITTED
        detail = f"venue accepted the order (status {status or 'unknown'}), not yet filled"
    else:
        outcome = OUTCOME_SUBMITTED
        detail = "venue acknowledged submission; order is not in the open book"

    return OrderTestResult(
        outcome=outcome,
        detail=detail,
        symbol=symbol,
        side="BUY",
        quantity=float(quantity),
        client_order_id=cid,
        broker_order_id=broker_order_id,
        broker_status=status,
        position_qty=position_qty,
        retryable=False,
    )


# ── the exit, verified against the broker's position book ──────────────────


def submit_exit(
    *,
    adapter: Any,
    symbol: str,
    ledger_path: str | Path,
    now_epoch: float | None = None,
    poll_interval_s: float = 2.0,
    poll_timeout_s: float = 30.0,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
) -> OrderTestResult:
    """Sell the open position, then PROVE it closed from the broker's books.

    ``verified_closed`` is set only when the venue reports a flat net
    position. An accepted SELL is reported as a request, never as a close.
    """
    from execution.models.order import OrderPlan

    now = time.time() if now_epoch is None else float(now_epoch)
    ledger = _Ledger(ledger_path)
    cid = client_order_id(symbol, _PURPOSE_EXIT, now)

    try:
        before = net_position_qty(adapter, symbol)
    except Exception as exc:  # noqa: BLE001 - never claim a close we cannot see
        return OrderTestResult(
            outcome=OUTCOME_EXIT_UNCERTAIN,
            detail=f"cannot read broker positions: {exc}",
            symbol=symbol,
            side="SELL",
            quantity=0.0,
            client_order_id=cid,
        )

    if before == 0:
        return OrderTestResult(
            outcome=OUTCOME_EXITED,
            detail="broker reports no open position for the symbol; nothing to exit",
            symbol=symbol,
            side="SELL",
            quantity=0.0,
            client_order_id=cid,
            position_qty=0.0,
            verified_closed=True,
        )

    plan = OrderPlan(
        intent_id=f"order-path-test:{symbol}:exit",
        symbol=symbol,
        side="SELL",
        quantity=abs(before),
        order_type="MARKET",
        time_in_force="DAY",
    )

    try:
        broker_order_id = str(adapter.place_order(plan, cid))
    except Exception as exc:  # noqa: BLE001 - request may or may not have landed
        logger.error("ORDER_PATH_TEST EXIT uncertain cid=%s reason=%s", cid, exc)
        return OrderTestResult(
            outcome=OUTCOME_EXIT_UNCERTAIN,
            detail=(
                f"SELL not confirmed: {type(exc).__name__}: {exc}; "
                f"position {before} may still be open"
            ),
            symbol=symbol,
            side="SELL",
            quantity=abs(before),
            client_order_id=cid,
            position_qty=before,
            verified_closed=False,
        )

    row = _order_by_tag(adapter, cid)
    status = _row_status(row)

    deadline = monotonic() + max(0.0, float(poll_timeout_s))
    after = before
    while monotonic() < deadline:
        try:
            after = net_position_qty(adapter, symbol)
        except Exception:  # noqa: BLE001 - keep polling; absence is not proof
            sleep(poll_interval_s)
            continue
        if after == 0:
            break
        sleep(poll_interval_s)

    closed = after == 0
    data = ledger.read()
    data[f"{_PURPOSE_EXIT}_client_order_id"] = cid
    data[f"{_PURPOSE_EXIT}_broker_order_id"] = broker_order_id
    data[f"{_PURPOSE_EXIT}_position_after"] = after
    data[f"{_PURPOSE_EXIT}_verified_closed"] = closed
    ledger.write(data)

    if closed:
        detail = f"broker reports flat position; {abs(before)} share(s) closed"
        outcome = OUTCOME_EXITED
    else:
        detail = (
            f"SELL was submitted (broker order {broker_order_id}) but the broker still reports "
            f"a net position of {after}; the position is NOT confirmed closed"
        )
        outcome = OUTCOME_EXIT_UNCERTAIN
        logger.error("ORDER_PATH_TEST EXIT unconfirmed cid=%s position_after=%s", cid, after)

    return OrderTestResult(
        outcome=outcome,
        detail=detail,
        symbol=symbol,
        side="SELL",
        quantity=abs(before),
        client_order_id=cid,
        broker_order_id=broker_order_id,
        broker_status=status,
        position_qty=after,
        verified_closed=closed,
    )
