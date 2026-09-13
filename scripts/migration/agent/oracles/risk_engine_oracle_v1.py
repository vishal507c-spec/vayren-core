"""Frozen oracle for risk.engine.evaluate (AUTO-GENERATED, immutable).

Snapshot sha256: 80ead462a9ab40931c5c6611b55374c8d8b6a04d41758be11f6682b99fe0fe7a.
Verification-only: imported by parity tests/harnesses, never by production.
"""

from __future__ import annotations

from risk.models import RiskCheck, RiskDecision
from risk.session import SessionRules, clock_sane, within_session


def _ocheck(checks: list, name: str, passed: bool, detail: str = "") -> bool:
    checks.append(RiskCheck(name=name, passed=passed, detail=detail))
    return passed


def oracle_evaluate(policy, request, halted, seen) -> RiskDecision:
    policy = policy
    checks: list[RiskCheck] = []
    ok = True
    ok &= _ocheck(checks, "kill_switch", not halted, "kill switch engaged" if halted else "")
    ok &= _ocheck(
        checks,
        "broker_health",
        bool(request.broker_healthy),
        "" if request.broker_healthy else "broker unhealthy",
    )
    ok &= _ocheck(
        checks,
        "session",
        within_session(
            request.timestamp, SessionRules(start=policy.session_start, end=policy.session_end)
        ),
        ""
        if within_session(request.timestamp, SessionRules(policy.session_start, policy.session_end))
        else "outside trading session",
    )
    ok &= _ocheck(
        checks,
        "clock",
        clock_sane(request.timestamp, request.now_epoch),
        "" if clock_sane(request.timestamp, request.now_epoch) else "clock anomaly",
    )
    if policy.allowed_symbols:
        ok &= _ocheck(
            checks,
            "instrument",
            request.symbol in policy.allowed_symbols,
            ""
            if request.symbol in policy.allowed_symbols
            else f"symbol not allowed: {request.symbol}",
        )
    else:
        _ocheck(checks, "instrument", True, "universe unrestricted")
    if request.intent_id in seen:
        ok &= _ocheck(checks, "duplicate", False, f"intent already decided: {request.intent_id}")
    else:
        _ocheck(checks, "duplicate", True)
    if policy.require_fresh_data_seconds is not None:
        fresh = (
            request.data_age_seconds is not None
            and request.data_age_seconds <= policy.require_fresh_data_seconds
        )
        ok &= _ocheck(
            checks,
            "fresh_data",
            fresh,
            "" if fresh else f"stale market data: age={request.data_age_seconds}",
        )
    else:
        _ocheck(checks, "fresh_data", True, "staleness gate disabled")
    if policy.spread_limit_pct is not None:
        tight = request.spread_pct is not None and request.spread_pct <= policy.spread_limit_pct
        ok &= _ocheck(
            checks, "spread", tight, "" if tight else f"spread too wide: {request.spread_pct}"
        )
    else:
        _ocheck(checks, "spread", True, "spread gate disabled")
    if policy.cooldown_seconds > 0 and request.last_order_epoch is not None:
        cooled = request.now_epoch - request.last_order_epoch >= policy.cooldown_seconds
        ok &= _ocheck(checks, "cooldown", cooled, "" if cooled else "in cooldown")
    else:
        _ocheck(checks, "cooldown", True)
    if policy.max_orders_per_day is not None:
        under = request.orders_today < policy.max_orders_per_day
        ok &= _ocheck(checks, "order_rate", under, "" if under else "max orders per day reached")
    else:
        _ocheck(checks, "order_rate", True)
    if request.quantity <= 0 or request.price <= 0:
        ok &= _ocheck(checks, "sanity", False, "non-positive quantity or price")
    else:
        _ocheck(checks, "sanity", True)
    ok &= _ocheck(
        checks,
        "order_qty",
        request.quantity <= policy.max_order_qty,
        "" if request.quantity <= policy.max_order_qty else "order quantity exceeds max",
    )
    notional = request.quantity * request.price
    if policy.max_notional is not None:
        ok &= _ocheck(
            checks,
            "notional",
            notional <= policy.max_notional,
            "" if notional <= policy.max_notional else "notional exceeds max",
        )
    else:
        _ocheck(checks, "notional", True)
    direction = 1.0 if request.side == "BUY" else -1.0
    new_position = request.position_qty + direction * request.quantity
    ok &= _ocheck(
        checks,
        "position",
        abs(new_position) <= policy.max_position_qty,
        "" if abs(new_position) <= policy.max_position_qty else "position limit exceeded",
    )
    if policy.max_exposure_pct is not None and request.equity > 0:
        exposure = abs(new_position) * request.price / request.equity * 100.0
        ok &= _ocheck(
            checks,
            "exposure",
            exposure <= policy.max_exposure_pct,
            "" if exposure <= policy.max_exposure_pct else "exposure limit exceeded",
        )
    else:
        _ocheck(checks, "exposure", True)
    if policy.daily_loss_limit is not None:
        ok &= _ocheck(
            checks,
            "daily_loss",
            request.day_pnl >= -abs(policy.daily_loss_limit),
            "" if request.day_pnl >= -abs(policy.daily_loss_limit) else "daily loss limit breached",
        )
    else:
        _ocheck(checks, "daily_loss", True)
    if policy.strategy_loss_limit is not None:
        ok &= _ocheck(
            checks,
            "strategy_loss",
            request.strategy_day_pnl >= -abs(policy.strategy_loss_limit),
            ""
            if request.strategy_day_pnl >= -abs(policy.strategy_loss_limit)
            else "strategy loss limit breached",
        )
    else:
        _ocheck(checks, "strategy_loss", True)
    if request.available_capital <= 0 and request.side == "BUY":
        ok &= _ocheck(checks, "capital", False, "no available capital")
    else:
        _ocheck(checks, "capital", True)
    reasons = tuple((check.detail for check in checks if not check.passed and check.detail))
    if ok:
        seen.add(request.intent_id)
    return RiskDecision(
        approved=bool(ok), intent_id=request.intent_id, reasons=reasons, checks=tuple(checks)
    )
