"""RiskEngine — mandatory fail-closed gate before ANY order.

Every check is recorded by name so a denial is always explainable. Any
unexpected error inside evaluation denies the request: when uncertain,
NO ORDER.
"""

from __future__ import annotations

from risk.kill_switch import KillSwitch
from risk.models import RiskCheck, RiskDecision, RiskPolicy, RiskRequest
from risk.session import SessionRules, clock_sane, within_session


class RiskEngine:
    """Stateless policy evaluation + duplicate-order memory.

    The engine itself holds no market state; the caller supplies a
    RiskRequest snapshot. Only approved intent IDs are remembered (for
    duplicate protection) and day counters the caller reports.
    """

    def __init__(self, policy: RiskPolicy, kill_switch: KillSwitch | None = None) -> None:
        self._policy = policy
        self._kill_switch = kill_switch if kill_switch is not None else KillSwitch()
        self._seen_intent_ids: set[str] = set()

    @property
    def policy(self) -> RiskPolicy:
        return self._policy

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    def evaluate(self, request: RiskRequest) -> RiskDecision:
        """Approve only when every applicable check passes. Never raises."""
        try:
            return self._evaluate(request)
        except Exception as exc:
            return RiskDecision(
                approved=False,
                intent_id=request.intent_id,
                reasons=(f"risk engine error — fail closed: {exc}",),
                checks=(RiskCheck(name="engine", passed=False, detail=str(exc)),),
            )

    def _check(self, checks: list[RiskCheck], name: str, passed: bool, detail: str = "") -> bool:
        checks.append(RiskCheck(name=name, passed=passed, detail=detail))
        return passed

    def _evaluate(self, request: RiskRequest) -> RiskDecision:
        policy = self._policy
        checks: list[RiskCheck] = []
        ok = True

        ok &= self._check(
            checks,
            "kill_switch",
            not self._kill_switch.is_halted(),
            "kill switch engaged" if self._kill_switch.is_halted() else "",
        )
        ok &= self._check(
            checks,
            "broker_health",
            bool(request.broker_healthy),
            "" if request.broker_healthy else "broker unhealthy",
        )
        ok &= self._check(
            checks,
            "session",
            within_session(
                request.timestamp,
                SessionRules(start=policy.session_start, end=policy.session_end),
            ),
            ""
            if within_session(
                request.timestamp, SessionRules(policy.session_start, policy.session_end)
            )
            else "outside trading session",
        )
        ok &= self._check(
            checks,
            "clock",
            clock_sane(request.timestamp, request.now_epoch),
            "" if clock_sane(request.timestamp, request.now_epoch) else "clock anomaly",
        )
        if policy.allowed_symbols:
            ok &= self._check(
                checks,
                "instrument",
                request.symbol in policy.allowed_symbols,
                ""
                if request.symbol in policy.allowed_symbols
                else f"symbol not allowed: {request.symbol}",
            )
        else:
            self._check(checks, "instrument", True, "universe unrestricted")
        if request.intent_id in self._seen_intent_ids:
            ok &= self._check(
                checks, "duplicate", False, f"intent already decided: {request.intent_id}"
            )
        else:
            self._check(checks, "duplicate", True)
        if policy.require_fresh_data_seconds is not None:
            fresh = (
                request.data_age_seconds is not None
                and request.data_age_seconds <= policy.require_fresh_data_seconds
            )
            ok &= self._check(
                checks,
                "fresh_data",
                fresh,
                "" if fresh else f"stale market data: age={request.data_age_seconds}",
            )
        else:
            self._check(checks, "fresh_data", True, "staleness gate disabled")
        if policy.spread_limit_pct is not None:
            tight = request.spread_pct is not None and request.spread_pct <= policy.spread_limit_pct
            ok &= self._check(
                checks, "spread", tight, "" if tight else f"spread too wide: {request.spread_pct}"
            )
        else:
            self._check(checks, "spread", True, "spread gate disabled")
        if policy.cooldown_seconds > 0 and request.last_order_epoch is not None:
            cooled = (request.now_epoch - request.last_order_epoch) >= policy.cooldown_seconds
            ok &= self._check(checks, "cooldown", cooled, "" if cooled else "in cooldown")
        else:
            self._check(checks, "cooldown", True)
        if policy.max_orders_per_day is not None:
            under = request.orders_today < policy.max_orders_per_day
            ok &= self._check(
                checks, "order_rate", under, "" if under else "max orders per day reached"
            )
        else:
            self._check(checks, "order_rate", True)
        if request.quantity <= 0 or request.price <= 0:
            ok &= self._check(checks, "sanity", False, "non-positive quantity or price")
        else:
            self._check(checks, "sanity", True)
        ok &= self._check(
            checks,
            "order_qty",
            request.quantity <= policy.max_order_qty,
            "" if request.quantity <= policy.max_order_qty else "order quantity exceeds max",
        )
        notional = request.quantity * request.price
        if policy.max_notional is not None:
            ok &= self._check(
                checks,
                "notional",
                notional <= policy.max_notional,
                "" if notional <= policy.max_notional else "notional exceeds max",
            )
        else:
            self._check(checks, "notional", True)
        direction = 1.0 if request.side == "BUY" else -1.0
        new_position = request.position_qty + direction * request.quantity
        ok &= self._check(
            checks,
            "position",
            abs(new_position) <= policy.max_position_qty,
            "" if abs(new_position) <= policy.max_position_qty else "position limit exceeded",
        )
        if policy.max_exposure_pct is not None and request.equity > 0:
            exposure = abs(new_position) * request.price / request.equity * 100.0
            ok &= self._check(
                checks,
                "exposure",
                exposure <= policy.max_exposure_pct,
                "" if exposure <= policy.max_exposure_pct else "exposure limit exceeded",
            )
        else:
            self._check(checks, "exposure", True)
        if policy.daily_loss_limit is not None:
            ok &= self._check(
                checks,
                "daily_loss",
                request.day_pnl >= -abs(policy.daily_loss_limit),
                ""
                if request.day_pnl >= -abs(policy.daily_loss_limit)
                else "daily loss limit breached",
            )
        else:
            self._check(checks, "daily_loss", True)
        if policy.strategy_loss_limit is not None:
            ok &= self._check(
                checks,
                "strategy_loss",
                request.strategy_day_pnl >= -abs(policy.strategy_loss_limit),
                ""
                if request.strategy_day_pnl >= -abs(policy.strategy_loss_limit)
                else "strategy loss limit breached",
            )
        else:
            self._check(checks, "strategy_loss", True)
        if request.available_capital <= 0 and request.side == "BUY":
            ok &= self._check(checks, "capital", False, "no available capital")
        else:
            self._check(checks, "capital", True)

        reasons = tuple(check.detail for check in checks if not check.passed and check.detail)
        if ok:
            self._seen_intent_ids.add(request.intent_id)
        return RiskDecision(
            approved=bool(ok), intent_id=request.intent_id, reasons=reasons, checks=tuple(checks)
        )
