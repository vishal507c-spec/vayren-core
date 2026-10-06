"""LiveSession — the composed execution pipeline (one instance, one mode).

Pipeline per market event (synchronous, deterministic):

    provider.poll → normalizer → attention → driver.on_candle
      → signal → intent → risk → planner → engine → broker
      → fills → portfolio → journal (+ optional bus events)

Startup order is enforced: RECOVER → RECONCILE → VALIDATE → WARMUP → READY.
Orders are impossible before READY. Multi-strategy: one StrategyContext per
registration, isolated state, shared deterministic clock sequence.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from math import floor
from pathlib import Path
from typing import Any

from execution.adaptive.attention import AttentionConfig, AttentionFilter
from execution.adaptive.confidence import ConfidenceInput, ConfidencePolicy
from execution.adaptive.memory import Incident, LongTermMemory, WorkingMemory
from execution.broker.adapter import BrokerAdapter
from execution.broker.factory import resolve_broker
from execution.engine import ExecutionEngine
from execution.events import (
    CandleEvent,
    HeartbeatEvent,
    MarketEvent,
    OrderAcknowledged,
    OrderFill,
    OrderPlanned,
    OrderRejected,
    OrderSubmitted,
    PositionUpdated,
    RiskApproved,
    RiskDenied,
    SignalGenerated,
)
from execution.journal import ExecutionJournal, LatencyTracker
from execution.market_data.normalizer import NormalizerConfig, StreamNormalizer
from execution.market_data.provider import MarketDataProvider
from execution.models.contract import StrategyRuntimeContract
from execution.models.intent import ExecutionIntent, StrategySignal, make_intent_id
from execution.models.order import BrokerOrder, Fill, OrderPlan, OrderState
from execution.modes import ExecutionMode, LiveArm, ModeGates, arm_transition
from execution.native_execution import (
    native_check_live_readiness_basic,
    native_default_quantity,
    native_narrow_multiplier,
)
from execution.native_order_state import TERMINAL_STATES
from execution.planner import ExecutionPreferences, OrderPlanner
from execution.portfolio.ledger import PositionLedger
from execution.portfolio.reconcile import ReconciliationState, reconcile_orders, reconcile_positions
from execution.regime import StatisticalRegimeDetector
from execution.replay import LiveEventRecorder
from execution.runtime.inspector import inspect_strategy
from execution.runtime.lifecycle import LifecycleError, LifecycleState, StrategyLifecycle
from execution.runtime.strategy_runtime import LiveStrategyDriver, StrategyContext
from market import Bar
from risk import KillSwitch, RiskEngine, RiskPolicy, RiskRequest
from risk.sizing import (
    BrokerCapital,
    SizingVerdict,
    size_position,
    validate_planned_quantity,
)
from strategy import StrategyDefinition, StrategyLogic, StrategyParameters, StrategyState


@dataclass
class StrategyRegistration:
    definition_id: str
    version: str
    logic_factory: Callable[[], StrategyLogic]
    params: StrategyParameters
    contract: StrategyRuntimeContract


@dataclass
class SessionConfig:
    mode: ExecutionMode = ExecutionMode.PAPER
    gates: ModeGates = field(default_factory=ModeGates)
    paper_capital: float = 1_000_000.0
    slippage_pct: float = 0.02
    commission_pct: float = 0.03
    journal_path: str | None = None
    memory_path: str | None = None
    kill_switch_path: str | Path | None = None
    session_start: str | None = None
    session_end: str | None = None
    adapter_name: str = ""  # sandbox/live venue key for resolve_broker; "" = none


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    reasons: tuple[str, ...] = ()


def check_live_readiness(
    contract: StrategyRuntimeContract,
    *,
    data_capabilities: tuple[str, ...],
    broker_capabilities: tuple[str, ...],
    warmup_bars_available: int,
    risk_policy_ok: bool,
    account_ok: bool,
    clock_ok: bool,
    reconcile_ok: bool,
    persistence_ok: bool,
    kill_ok: bool,
    observability_ok: bool,
    for_live: bool = True,
) -> ReadinessReport:
    """The 11 pre-live gates. Any failure → NOT_LIVE_READY with reasons.

    With ``for_live=False`` (paper/sandbox sessions) the live-declaration
    gate is skipped — paper support is the default — while every safety
    gate (data, warmup, risk, account, clock, reconcile, kill switch)
    still applies.
    """
    reasons: list[str] = []
    if contract.missing:
        reasons.append(f"contract incomplete: {', '.join(contract.missing)}")
    if for_live and not contract.supports_live:
        reasons.append("strategy does not declare live support (SUPPORTS_LIVE)")
    for required in contract.data_requirements:
        if required not in data_capabilities:
            reasons.append(f"data capability missing: {required}")
    for required in contract.required_broker_capabilities:
        if required not in broker_capabilities:
            reasons.append(f"broker capability missing: {required}")
    if not native_check_live_readiness_basic(
        warmup_bars_available,
        contract.warmup_bars,
        risk_policy_ok,
        account_ok,
        clock_ok,
        reconcile_ok,
        persistence_ok,
        kill_ok,
        observability_ok,
    ):
        if warmup_bars_available < contract.warmup_bars:
            reasons.append(
                f"warmup shortfall: have {warmup_bars_available}, need {contract.warmup_bars}"
            )
        if not risk_policy_ok:
            reasons.append("risk policy invalid")
        if not account_ok:
            reasons.append("account check failed")
        if not clock_ok:
            reasons.append("clock check failed")
        if not reconcile_ok:
            reasons.append("reconciliation mismatch unresolved")
        if not persistence_ok:
            reasons.append("persistence unavailable")
        if not kill_ok:
            reasons.append("kill switch engaged or unavailable")
        if not observability_ok:
            reasons.append("observability (journal) unavailable")
    return ReadinessReport(ready=not reasons, reasons=tuple(reasons))


class LiveSession:
    """Owns one live/paper run: lifecycle, pipeline, recovery, journal."""

    def __init__(
        self,
        config: SessionConfig,
        provider: MarketDataProvider,
        risk_policy: RiskPolicy,
        request_id: str = "live-1",
    ) -> None:
        self._config = config
        self._provider = provider
        self._request_id = request_id
        self._kill_switch = KillSwitch(config.kill_switch_path)
        self._risk = RiskEngine(risk_policy, self._kill_switch)
        self._planner = OrderPlanner()
        self._engine = ExecutionEngine()
        self._journal = ExecutionJournal(config.journal_path)
        self._latency = LatencyTracker()
        self._normalizer = StreamNormalizer(NormalizerConfig())
        self._attention = AttentionFilter(AttentionConfig())
        self._working_memory = WorkingMemory()
        self._long_term_memory = LongTermMemory(config.memory_path)
        self._confidence = ConfidencePolicy()
        self._regime = StatisticalRegimeDetector()
        self._recorder = LiveEventRecorder()
        self._ledger = PositionLedger(config.paper_capital)
        self._reconciliation = ReconciliationState()
        self._contexts: dict[str, StrategyContext] = {}
        self._registrations: dict[str, StrategyRegistration] = {}
        self._broker: BrokerAdapter | None = None
        self._mode = ExecutionMode.PAPER
        self._armed = LiveArm.DISARMED
        self._broker_calls = 0
        self._broker_stream_errors = 0
        self._lifecycle = StrategyLifecycle()
        self._last_order_epoch: float | None = None
        self._orders_today = 0
        self._bus: Any = None
        # Phase-3 LIVE sizing verdicts by intent id: the final `_submit`
        # gate re-validates the planned quantity against the verdict's
        # ceiling, so even a planner-adjusted quantity can never exceed the
        # 0.15% per-stock limit. One-shot entries (popped on submit).
        self._sized_risk: dict[str, SizingVerdict] = {}
        # Phase-4 protective-stop state (per symbol, from the strategy's own
        # stop value — never invented): required stop price, lifecycle and
        # the active SL order. Filled quantity alone sizes protection.
        self._stop_levels: dict[str, float] = {}
        self._stop_protection: dict[str, dict[str, Any]] = {}
        self._sl_seq = 0
        self._sl_attention: set[str] = set()
        # Fill ids already folded (venue retransmissions must never double
        # a position or an order's filled quantity).
        self._seen_fills: set[tuple[str, float, float]] = set()

    # ── wiring ──────────────────────────────────────────────────

    def _emit(self, event: Any) -> None:
        if self._bus is not None:
            self._bus.publish(event)

    def register_strategy(
        self,
        definition_id: str,
        version: str,
        logic_factory: Callable[[], StrategyLogic],
        params: StrategyParameters,
        definition: Any = None,
    ) -> StrategyRuntimeContract:
        """Register one isolated strategy instance; returns its contract."""
        logic = logic_factory()
        stub = (
            definition
            if definition is not None
            else StrategyDefinition(
                id=definition_id,
                name=definition_id,
                version=version,
                kind="live",
                params=params,
            )
        )
        contract = inspect_strategy(stub, logic)
        context = StrategyContext(
            strategy_id=definition_id,
            strategy_version=version,
            logic=logic,
            params=params,
            contract=contract,
        )
        key = f"{definition_id}:{version}"
        self._contexts[key] = context
        self._registrations[key] = StrategyRegistration(
            definition_id=definition_id,
            version=version,
            logic_factory=logic_factory,
            params=params,
            contract=contract,
        )
        return contract

    # ── startup sequence: RECOVER → RECONCILE → VALIDATE → WARMUP → READY ──

    def start(
        self,
        symbols: tuple[str, ...],
        timeframe: str,
        warmup_bars: dict[str, tuple[Bar, ...]] | None = None,
    ) -> ReadinessReport:
        """Bring the session to READY. Returns NOT_LIVE_READY with reasons on any failure."""
        recovering = self._lifecycle.state == LifecycleState.RECOVERING
        if self._lifecycle.state == LifecycleState.CREATED:
            self._lifecycle.transition(LifecycleState.VALIDATING, reason="start requested")
        elif recovering:
            self._lifecycle.transition(LifecycleState.RECONCILING, reason="recovered, reconciling")
        else:
            raise LifecycleError(
                f"start requires CREATED or RECOVERING, found {self._lifecycle.state.value}"
            )
        broker, mode, notes = resolve_broker(
            self._config.mode, self._config.gates, adapter_name=self._config.adapter_name
        )
        self._broker = broker
        self._mode = mode
        for note in notes:
            self._journal.record("MODE_DOWNGRADE", reason=note)
        if recovering:
            # FINAL §K/§L: rebuilt idempotency state reconciles against broker
            # truth BEFORE validation — unresolved mismatch blocks (never
            # blind-resubmit). A fresh broker after a crash reports nothing:
            # restored open orders then fail closed until the operator
            # clears the checkpoint for a clean restart.
            self.reconcile_now()
            self._lifecycle.transition(LifecycleState.VALIDATING, reason="reconciled, validating")
        for context in self._contexts.values():
            lifecycle = context.lifecycle
            if lifecycle.state == LifecycleState.CREATED:
                lifecycle.transition(LifecycleState.VALIDATING, reason="session start")
        report = self._readiness(warmup_bars or {})
        if not report.ready:
            for context in self._contexts.values():
                if context.lifecycle.state == LifecycleState.VALIDATING:
                    context.lifecycle.transition(
                        LifecycleState.ERROR, reason="; ".join(report.reasons)
                    )
            self._journal.record("NOT_LIVE_READY", reasons=list(report.reasons))
            return report
        self._provider.open(symbols, timeframe)
        self._lifecycle.transition(LifecycleState.WARMING_UP, reason="warming strategies")
        for context in self._contexts.values():
            context.lifecycle.transition(LifecycleState.WARMING_UP, reason="warming")
            bars = (warmup_bars or {}).get(context.strategy_id, ())
            LiveStrategyDriver(context).warmup(bars)
            context.lifecycle.transition(LifecycleState.READY, reason="warmed")
            context.lifecycle.transition(LifecycleState.RUNNING, reason="session running")
        self._lifecycle.transition(LifecycleState.READY, reason="strategies ready")
        self._lifecycle.transition(LifecycleState.RUNNING, reason="ready")
        self._journal.record("LIVE_READY", mode=self._mode.value, symbols=list(symbols))
        return report

    def _readiness(self, warmup_bars: dict[str, tuple[Bar, ...]]) -> ReadinessReport:
        assert self._broker is not None
        broker_caps = tuple(self._broker.capabilities)
        data_caps = tuple(self._provider.capabilities)
        reasons: list[str] = []
        for key, context in self._contexts.items():
            available = len(warmup_bars.get(context.strategy_id, ()))
            report = check_live_readiness(
                context.contract,
                data_capabilities=data_caps,
                broker_capabilities=broker_caps,
                warmup_bars_available=available,
                risk_policy_ok=True,
                account_ok=self._broker.health()[0],
                clock_ok=True,
                reconcile_ok=not self._reconciliation.blocks_live,
                persistence_ok=self._journal is not None,
                kill_ok=not self._kill_switch.is_halted(),
                observability_ok=True,
                for_live=self._mode == ExecutionMode.LIVE,
            )
            reasons.extend(f"{key}: {reason}" for reason in report.reasons)
        if self._mode == ExecutionMode.LIVE and self._reconciliation.blocks_live:
            reasons.append("reconciliation mismatch blocks live")
        return ReadinessReport(ready=not reasons, reasons=tuple(reasons))

    # ── event pipeline ──────────────────────────────────────────

    def step(self, now_epoch: float) -> int:
        """Poll once; drive every deliverable event through the pipeline."""
        if self._broker is None:
            raise RuntimeError("session not started")
        delivered = 0
        for raw in self._provider.poll():
            self._recorder.record(raw)
            if isinstance(raw, HeartbeatEvent):
                self._normalizer.observe(raw, now_epoch)
                continue
            # Staleness is judged BEFORE ingesting: a stalled stream must not
            # refresh its own health timestamp by delivering old events.
            healthy, _ = self._normalizer.check_health(raw.symbol, now_epoch)
            if not healthy:
                self._journal.record("STALE_DATA", symbol=raw.symbol, seq=raw.seq)
                continue
            for event in self._normalizer.observe(raw, now_epoch):
                decision = self._attention.observe(event, under_load=False)
                if not decision.process:
                    continue
                self._working_memory.note_event(type(event).__name__, event.symbol, event.seq)
                self._on_event(event, now_epoch)
                delivered += 1
        self._drain_broker(now_epoch)
        return delivered

    def _on_event(self, event: MarketEvent, now_epoch: float) -> None:
        if not isinstance(event, CandleEvent):
            if isinstance(event, HeartbeatEvent):
                return
            self._update_market_state(event)
            return
        regime = self._regime.update(event.close, float(event.volume))
        self._working_memory.regime = regime.value
        for context in self._contexts.values():
            try:
                driver = LiveStrategyDriver(context)
                signal = driver.on_candle(event)
            except Exception as exc:
                # One strategy's failure must never corrupt another instance.
                self._journal.record(
                    "STRATEGY_ERROR", strategy_id=context.strategy_id, reason=str(exc)
                )
                self._long_term_memory_record(context.strategy_id, str(exc), event.timestamp)
                continue
            if signal is None:
                continue
            self._journal.record(
                "SIGNAL_GENERATED",
                strategy_id=signal.strategy_id,
                signal_id=signal.signal_id,
                event_seq=signal.event_seq,
            )
            self._emit(
                SignalGenerated(
                    self._request_id, signal.strategy_id, signal.signal_id, signal.event_seq
                )
            )
            self._working_memory.note_signal(signal.signal_id, signal.side)
            try:
                self._handle_signal(context, signal, now_epoch)
            except Exception as exc:
                self._journal.record(
                    "STRATEGY_ERROR", strategy_id=context.strategy_id, reason=str(exc)
                )
        # Venue-agnostic settlement AFTER submits: simulated venues fill
        # pending orders (including just-submitted) at this bar, preserving
        # same-bar fill economics. Live venues ignore the call.
        if self._broker is not None:
            self._broker.on_market_price(event.symbol, event.close, event.timestamp)
        self._process_broker_stream()

    def _long_term_memory_record(self, strategy_id: str, reason: str, timestamp: str) -> None:
        with suppress(Exception):
            self._long_term_memory.record_incident(
                Incident(
                    kind="strategy_error", detail=f"{strategy_id}: {reason}", timestamp=timestamp
                )
            )

    def _confidence_input(self, symbol: str) -> ConfidenceInput:
        """Real confidence signals. Overridable seam for tests and future models."""
        broker_ok = self._broker.health()[0] if self._broker else False
        return ConfidenceInput(
            data_stale=self._normalizer.is_stale(symbol),
            broker_unstable=not broker_ok,
        )

    def _update_market_state(self, event: MarketEvent) -> None:
        price = getattr(event, "price", None)
        if price and self._broker is not None:
            self._broker.on_market_price(event.symbol, float(price), event.timestamp)

    def _handle_signal(
        self, context: StrategyContext, signal: StrategySignal, now_epoch: float
    ) -> None:
        ledger_qty, side, _ = self._ledger.strategy_state_for(signal.symbol)
        if signal.side == "BUY" and ledger_qty > 0:
            return  # already long — single-position model skips re-entry
        if signal.side == "SELL" and ledger_qty <= 0:
            return  # nothing to exit in the single-position model
        context.intent_seq += 1
        intent_id = make_intent_id(
            signal.strategy_id, signal.strategy_version, signal.event_seq, context.intent_seq
        )
        if signal.side == "BUY":
            if self._mode == ExecutionMode.LIVE:
                # LIVE entries size ONLY from the risk engine: broker
                # available capital × 4 × 0.15% against the strategy's own
                # entry/stop. A denial blocks the order here (fail-closed).
                sized = self._live_sized_quantity(intent_id, signal, now_epoch)
                if sized is None:
                    return
                quantity = float(sized.quantity)
            else:
                quantity = self._default_quantity(signal)
                # Non-live entries still anchor protective-stop placement on
                # the strategy's own stop (consumed, never invented).
                if signal.stop_loss is not None and signal.stop_loss > 0:
                    self._stop_levels[signal.symbol] = signal.stop_loss
            target = ledger_qty + quantity
        else:
            quantity = abs(ledger_qty)
            target = 0.0
        posture = self._confidence.evaluate(self._confidence_input(signal.symbol))
        preferences = self._confidence.preferences(posture)
        if preferences.get("halt"):
            self._journal.record(
                "EXECUTION_HALTED", reason="adaptive posture HALT", intent_id=intent_id
            )
            return
        intent = ExecutionIntent(
            intent_id=intent_id,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            signal_id=signal.signal_id,
            timestamp=signal.timestamp,
            event_seq=signal.event_seq,
            symbol=signal.symbol,
            side=signal.side,
            target_position_qty=target,
            quantity=quantity,
            urgency="normal",
            preferred_order_type="LIMIT" if preferences.get("prefer_limit") else "MARKET",
            reason=signal.reason,
            confidence=signal.confidence,
        )
        snapshot = self._ledger.snapshot({signal.symbol: signal.price})
        spread = self._broker.reference_spread(signal.symbol) if self._broker else None
        request = RiskRequest(
            intent_id=intent.intent_id,
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=signal.price,
            timestamp=signal.timestamp,
            position_qty=ledger_qty,
            day_pnl=snapshot.day_pnl,
            strategy_day_pnl=snapshot.day_pnl,
            equity=snapshot.equity,
            available_capital=snapshot.available_capital,
            spread_pct=spread,
            data_age_seconds=0.0,
            broker_healthy=self._broker.health()[0] if self._broker else False,
            orders_today=self._orders_today,
            last_order_epoch=self._last_order_epoch,
            now_epoch=now_epoch,
        )
        decision = self._risk.evaluate(request)
        if not decision.approved:
            self._journal.record(
                "RISK_DENIED", intent_id=intent.intent_id, reasons=list(decision.reasons)
            )
            self._emit(RiskDenied(self._request_id, intent.intent_id, tuple(decision.reasons)))
            return
        self._journal.record("RISK_APPROVED", intent_id=intent.intent_id)
        self._emit(RiskApproved(self._request_id, intent.intent_id))
        prefs = ExecutionPreferences(
            prefer_limit=bool(preferences.get("prefer_limit")),
            size_multiplier=native_narrow_multiplier(preferences.get("size_multiplier", 1.0)),
        )
        plan = self._planner.plan(intent, signal.price, prefs)
        # Risk re-validates the FINAL planned quantity (adaptive shrink applied).
        final_request = RiskRequest(
            intent_id=intent.intent_id + ":final",
            strategy_id=intent.strategy_id,
            symbol=plan.symbol,
            side=plan.side,
            quantity=plan.quantity,
            price=signal.price,
            timestamp=signal.timestamp,
            position_qty=ledger_qty,
            day_pnl=snapshot.day_pnl,
            strategy_day_pnl=snapshot.day_pnl,
            equity=snapshot.equity,
            available_capital=snapshot.available_capital,
            spread_pct=spread,
            data_age_seconds=0.0,
            broker_healthy=self._broker.health()[0] if self._broker else False,
            orders_today=self._orders_today,
            last_order_epoch=self._last_order_epoch,
            now_epoch=now_epoch,
        )
        final_decision = self._risk.evaluate(final_request)
        if not final_decision.approved:
            self._journal.record(
                "RISK_DENIED", intent_id=intent.intent_id, reasons=list(final_decision.reasons)
            )
            self._emit(
                RiskDenied(self._request_id, intent.intent_id, tuple(final_decision.reasons))
            )
            return
        self._submit(intent, plan, now_epoch)

    def _default_quantity(self, signal: StrategySignal) -> float:
        snapshot = self._ledger.snapshot({signal.symbol: signal.price})
        return native_default_quantity(
            snapshot.available_capital, signal.price, self._risk.policy.max_order_qty
        )

    def _live_broker_capital(self, now_epoch: float) -> BrokerCapital | None:
        """Read REAL broker available capital (LIVE sizing source of truth).

        Returns ``None`` when the venue reports nothing usable — the caller
        blocks instead of fabricating capital. Never raises, never caches:
        every signal re-reads, so a capital change between calculations is
        picked up and a failed read cannot launder a stale number in.
        """
        broker = self._broker
        funds_fn = getattr(broker, "funds", None)
        if not callable(funds_fn):
            return None
        try:
            payload = funds_fn()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        raw_available: Any = payload.get("available")
        try:
            available = float(raw_available)
        except (TypeError, ValueError):
            return None
        if available != available or available in (float("inf"), float("-inf")):
            return None
        return BrokerCapital(available=available, fetched_epoch=now_epoch, source="broker")

    def _live_sized_quantity(
        self, intent_id: str, signal: StrategySignal, now_epoch: float
    ) -> SizingVerdict | None:
        """Size one LIVE entry through the Phase-3 risk pipeline.

        Returns the READY verdict (remembered for the final ``_submit``
        gate) or records ``RISK_DENIED`` and returns ``None`` to block.
        The strategy's entry/stop values are consumed unchanged — this
        method never invents a stop level.
        """
        verdict = size_position(
            broker_capital=self._live_broker_capital(now_epoch),
            entry_price=signal.price,
            stop_price=signal.stop_loss,
            mode="LIVE",
            now_epoch=now_epoch,
        )
        if not verdict.ready:
            self._journal.record("RISK_DENIED", intent_id=intent_id, reasons=[verdict.reason])
            self._emit(RiskDenied(self._request_id, intent_id, (verdict.reason,)))
            return None
        self._journal.record(
            "RISK_VALIDATED",
            intent_id=intent_id,
            quantity=verdict.quantity,
            planned_risk=verdict.planned_risk,
        )
        self._emit(RiskApproved(self._request_id, intent_id))
        self._sized_risk[intent_id] = verdict
        # The strategy's own stop now anchors this symbol's protective-stop
        # requirement (placed after the entry fill, sized on filled qty).
        if verdict.stop_price is not None and verdict.stop_price > 0:
            self._stop_levels[signal.symbol] = verdict.stop_price
        return verdict

    def _pre_order_checks(self, intent: ExecutionIntent, plan: OrderPlan) -> tuple[str, str] | None:
        """Final pre-order safety gate (fail-closed). Returns ``(kind,
        reason)`` to BLOCK, or ``None`` to let the order continue.

        Every check reads live facts — mode, arming, broker health, order
        stream, market-data freshness, reconciliation, sizing verdict and
        the engine's own open orders. Uncertain means blocked, never
        partially executed. The two legacy journal kinds for unarmed /
        unhealthy venues are preserved verbatim.
        """
        assert self._broker is not None
        live = self._mode == ExecutionMode.LIVE
        if self._lifecycle.state != LifecycleState.RUNNING:
            return ("ORDER_BLOCKED", f"session not running ({self._lifecycle.state.value})")
        if live and self._armed != LiveArm.ARMED:
            return ("ORDER_BLOCKED_UNARMED", "live not armed")
        if live:
            healthy, health_reason = self._broker.health()
            if not healthy:
                return ("ORDER_BLOCKED_UNHEALTHY", f"broker unhealthy: {health_reason}")
            stream_health_fn: Any = getattr(self._broker, "order_stream_health", None)
            if callable(stream_health_fn):
                try:
                    stream_result: Any = stream_health_fn()
                    stream_ok, stream_reason = bool(stream_result[0]), str(stream_result[1])
                except Exception as exc:
                    return ("ORDER_BLOCKED", f"order stream unreadable: {exc}")
                if not stream_ok:
                    return ("ORDER_BLOCKED", f"order stream unhealthy: {stream_reason}")
            if self._normalizer.is_stale(plan.symbol):
                return ("ORDER_BLOCKED", f"market data stale for {plan.symbol}")
        if self._reconciliation.blocks_live:
            return ("ORDER_BLOCKED", "reconciliation blocks live")
        if not plan.symbol:
            return ("ORDER_BLOCKED", "invalid symbol")
        if live and plan.side == "BUY" and intent.intent_id not in self._sized_risk:
            return ("ORDER_BLOCKED", "risk not validated for live order")
        for open_order in self._engine.open_orders():
            if open_order.intent_id.startswith("sl:"):
                continue  # protective stops never block strategy orders
            if open_order.symbol == plan.symbol and open_order.side == plan.side:
                return (
                    "ORDER_BLOCKED",
                    f"duplicate active order for {plan.symbol} {plan.side}",
                )
        return None

    def _submit(self, intent: ExecutionIntent, plan: OrderPlan, now_epoch: float) -> None:
        assert self._broker is not None
        blocked = self._pre_order_checks(intent, plan)
        if blocked is not None:
            kind, reason = blocked
            self._journal.record(kind, intent_id=intent.intent_id, reason=reason)
            return
        client_order_id = f"{intent.intent_id}:o1"
        order = BrokerOrder(
            client_order_id=client_order_id,
            intent_id=intent.intent_id,
            symbol=plan.symbol,
            side=plan.side,
            quantity=plan.quantity,
            order_type=plan.order_type,
            limit_price=plan.limit_price,
        )
        if plan.quantity <= 0:
            self._journal.record(
                "ORDER_SKIPPED", intent_id=intent.intent_id, reason="zero quantity"
            )
            return
        # NSE equity has no fractional shares: floor advisory-shrunk
        # quantities to whole shares (FLOOR only ever reduces risk, so the
        # sizing ceiling still holds). A floored-to-zero order is skipped,
        # never rounded up.
        whole_shares = floor(plan.quantity)
        if whole_shares <= 0:
            self._journal.record(
                "ORDER_SKIPPED", intent_id=intent.intent_id, reason="quantity floors to zero"
            )
            return
        if float(whole_shares) != plan.quantity:
            self._journal.record(
                "QUANTITY_FLOORED",
                intent_id=intent.intent_id,
                intended=plan.quantity,
                quantity=float(whole_shares),
            )
            plan = replace(plan, quantity=float(whole_shares))
        # FINAL Phase-3 gate: re-validate the planned quantity against the
        # sizing verdict's ceiling immediately before the LIVE order path.
        # Catches planner-adjusted or manually inflated quantities that
        # would push planned risk past the 0.15% per-stock limit.
        sized = self._sized_risk.pop(intent.intent_id, None)
        if sized is not None and self._mode == ExecutionMode.LIVE:
            allowed, gate_reason = validate_planned_quantity(
                quantity=plan.quantity,
                entry_price=sized.entry_price,
                stop_price=sized.stop_price,
                max_risk_per_stock=sized.max_risk_per_stock,
            )
            if not allowed:
                self._journal.record(
                    "RISK_DENIED", intent_id=intent.intent_id, reasons=[gate_reason]
                )
                self._emit(RiskDenied(self._request_id, intent.intent_id, (gate_reason,)))
                return
        self._journal.record(
            "ORDER_PLANNED",
            intent_id=intent.intent_id,
            client_order_id=client_order_id,
            order_type=plan.order_type,
            broker=self._broker.name if self._broker else "",
            environment=self._mode.value.lower(),
        )
        self._emit(OrderPlanned(self._request_id, intent.intent_id, client_order_id))
        tracked = self._engine.create(order)
        tracked = self._engine.transition(
            tracked.client_order_id, OrderState.VALIDATED, reason="risk approved"
        )
        try:
            broker_id = self._broker.place_order(plan, tracked.client_order_id)
        except Exception as exc:
            # Transport uncertainty (sent-but-unconfirmed) must NEVER be
            # recorded as a rejection: the order becomes UNKNOWN and exits
            # only via broker reconciliation. Deterministic refusals stay
            # REJECTED with the venue's own reason.
            if getattr(exc, "code", "") in ("NETWORK_ERROR", "NETWORK", "TIMEOUT"):
                self._engine.transition(
                    tracked.client_order_id, OrderState.UNKNOWN, reason=str(exc)
                )
                self._journal.record(
                    "ORDER_UNKNOWN", client_order_id=client_order_id, reason=str(exc)
                )
                return
            self._engine.transition(tracked.client_order_id, OrderState.REJECTED, reason=str(exc))
            self._journal.record("ORDER_REJECTED", client_order_id=client_order_id, reason=str(exc))
            self._emit(OrderRejected(self._request_id, client_order_id, str(exc)))
            return
        self._broker_calls += 1
        tracked = self._engine.transition(
            tracked.client_order_id, OrderState.SUBMITTED, reason="sent to broker"
        )
        self._journal.record(
            "ORDER_SUBMITTED",
            client_order_id=client_order_id,
            broker_order_id=broker_id,
            broker=self._broker.name,
            environment=self._mode.value.lower(),
        )
        self._emit(OrderSubmitted(self._request_id, client_order_id))
        self._orders_today += 1
        self._last_order_epoch = now_epoch

    def _apply_fill(self, tracked: BrokerOrder, fill: Fill) -> None:
        # Venue retransmissions must never double a position: the same
        # (order, qty, price) report folds exactly once.
        fill_key = (fill.client_order_id, float(fill.fill_qty), float(fill.fill_price))
        if fill_key in self._seen_fills:
            self._journal.record("FILL_DUPLICATE_IGNORED", client_order_id=fill.client_order_id)
            return
        self._seen_fills.add(fill_key)
        updated = self._engine.apply_fill(tracked, fill)
        if fill.partial:
            updated = self._engine.transition(
                updated.client_order_id, OrderState.PARTIALLY_FILLED, reason="partial"
            )
        else:
            updated = self._engine.transition(
                updated.client_order_id, OrderState.FILLED, reason="fill"
            )
        position = self._ledger.apply_fill(fill)
        qty, side, entry = self._ledger.strategy_state_for(fill.symbol)
        if side is None:
            # Flat is global truth (covers protective-stop exits, whose
            # synthetic intent maps to no strategy instance).
            for context in self._contexts.values():
                context.state = StrategyState()
        else:
            for context in self._contexts.values():
                if context.strategy_id == self._strategy_of_intent(updated.intent_id):
                    # Live has no bar index; the ledger quantity/side/avg drive state.
                    context.state = StrategyState.open(side, entry or 0.0, 0)
        self._working_memory.position_qty = qty
        self._journal.record(
            "FILL",
            client_order_id=fill.client_order_id,
            broker_order_id=fill.broker_order_id,
            intent_id=tracked.intent_id,
            fill_qty=fill.fill_qty,
            fill_price=fill.fill_price,
            partial=fill.partial,
            broker=self._broker.name if self._broker else "",
            environment=self._mode.value.lower(),
        )
        self._journal.record("POSITION_UPDATED", symbol=fill.symbol, quantity=qty)
        self._emit(
            OrderFill(
                self._request_id, fill.client_order_id, fill.fill_qty, fill.fill_price, fill.partial
            )
        )
        self._emit(PositionUpdated(self._request_id, fill.symbol, qty))
        _ = position
        if tracked.intent_id.startswith("sl:"):
            return  # protective-stop fills only fold; state via _note_sl_event
        if side is None:
            self._clear_stop_protection(fill.symbol, "position flat")
        else:
            self._place_protective_stop(fill.symbol)

    def _sl_order_open(self, client_order_id: str) -> bool:
        """True while the tracked SL order is still live at the venue."""
        if not client_order_id:
            return False
        tracked = self._engine.get(client_order_id)
        return tracked is not None and tracked.state not in TERMINAL_STATES

    def _place_protective_stop(self, symbol: str) -> None:
        """(Re)arm the protective stop for the ACTUAL open position.

        Sized strictly on confirmed filled quantity at the strategy's own
        stop price (recorded at signal time, never invented). A stop that
        cannot be placed, or that the venue rejects, marks the symbol as
        needing immediate attention — protection is never claimed
        silently. Never raises.
        """
        assert self._broker is not None
        position = self._ledger.position(symbol)
        if position.flat:
            return
        stop = self._stop_levels.get(symbol)
        if stop is None or stop <= 0:
            self._journal.record("SL_SKIPPED", symbol=symbol, reason="no strategy stop known")
            return
        required_qty = abs(position.quantity)
        existing = self._stop_protection.get(symbol)
        if (
            existing is not None
            and existing.get("state") in ("SENT", "ACKNOWLEDGED", "WORKING")
            and existing.get("quantity") == required_qty
            and self._sl_order_open(str(existing.get("client_order_id", "")))
        ):
            return  # correctly sized protection already working
        if existing is not None:
            self._cancel_stop_protection(symbol, "resize")
        side = "SELL" if position.quantity > 0 else "BUY"
        self._sl_seq += 1
        intent_id = f"sl:{symbol}:{self._sl_seq}"
        client_order_id = f"{intent_id}:o1"
        plan = OrderPlan(
            intent_id=intent_id,
            symbol=symbol,
            side=side,
            quantity=required_qty,
            order_type="STOP_MARKET",
            stop_price=stop,
        )
        order = BrokerOrder(
            client_order_id=client_order_id,
            intent_id=intent_id,
            symbol=symbol,
            side=side,
            quantity=required_qty,
            order_type="STOP_MARKET",
        )
        try:
            self._engine.create(order)
            self._engine.transition(client_order_id, OrderState.VALIDATED, reason="protective stop")
        except Exception as exc:
            self._fail_stop_protection(symbol, stop, required_qty, client_order_id, str(exc))
            return
        try:
            broker_id = self._broker.place_order(plan, client_order_id)
        except Exception as exc:
            with suppress(Exception):
                self._engine.transition(client_order_id, OrderState.REJECTED, reason=str(exc))
            self._fail_stop_protection(symbol, stop, required_qty, client_order_id, str(exc))
            return
        self._engine.transition(client_order_id, OrderState.SUBMITTED, reason="stop sent")
        self._stop_protection[symbol] = {
            "stop_price": stop,
            "quantity": required_qty,
            "state": "SENT",
            "client_order_id": client_order_id,
            "broker_order_id": broker_id,
        }
        self._journal.record(
            "SL_SENT",
            symbol=symbol,
            client_order_id=client_order_id,
            broker_order_id=broker_id,
            quantity=required_qty,
            stop_price=stop,
        )

    def _fail_stop_protection(
        self, symbol: str, stop: float, quantity: float, client_order_id: str, reason: str
    ) -> None:
        """Record unprotected exposure: FAILED state + attention flag."""
        self._stop_protection[symbol] = {
            "stop_price": stop,
            "quantity": quantity,
            "state": "FAILED",
            "client_order_id": client_order_id,
            "broker_order_id": None,
        }
        self._sl_attention.add(symbol)
        self._journal.record(
            "SL_FAILED", symbol=symbol, client_order_id=client_order_id, reason=reason
        )

    def _cancel_stop_protection(self, symbol: str, reason: str) -> None:
        """Best-effort venue cancel of the working stop (never raises)."""
        prot = self._stop_protection.get(symbol)
        if prot is None:
            return
        broker_id = prot.get("broker_order_id")
        if broker_id and self._broker is not None:
            cancel = getattr(self._broker, "cancel_order", None)
            if callable(cancel):
                with suppress(Exception):
                    cancel(str(broker_id))
        with suppress(Exception):
            self._engine.transition(
                str(prot.get("client_order_id", "")), OrderState.CANCELLED, reason=reason
            )
        self._journal.record(
            "SL_CANCELLED",
            symbol=symbol,
            reason=reason,
        )

    def _clear_stop_protection(self, symbol: str, reason: str) -> None:
        """Drop protection bookkeeping once the position is flat."""
        if symbol not in self._stop_protection:
            return
        self._cancel_stop_protection(symbol, reason)
        self._stop_protection.pop(symbol, None)
        self._sl_attention.discard(symbol)
        self._journal.record("SL_CLEARED", symbol=symbol, reason=reason)

    def _note_sl_event(self, client_order_id: str, kind: str, event: dict) -> None:
        """Fold venue acks/fills/rejects for protective stops (never raises).

        Only broker responses advance SL state: SENT → ACKNOWLEDGED →
        WORKING → SAFE, or FAILED (venue reject/cancel) with the symbol
        flagged for immediate attention.
        """
        try:
            for symbol, prot in self._stop_protection.items():
                if prot.get("client_order_id") != client_order_id:
                    continue
                if kind == "ack":
                    broker_id = str(event.get("broker_order_id", "") or "")
                    if broker_id:
                        prot["broker_order_id"] = broker_id
                    prot["state"] = "WORKING"
                    self._journal.record(
                        "SL_ACKNOWLEDGED",
                        symbol=symbol,
                        client_order_id=client_order_id,
                        broker_order_id=prot.get("broker_order_id"),
                    )
                elif kind == "fill":
                    prot["state"] = "SAFE"
                    self._journal.record("SL_SAFE", symbol=symbol, client_order_id=client_order_id)
                elif kind in ("reject", "cancel"):
                    prot["state"] = "FAILED"
                    self._sl_attention.add(symbol)
                    self._journal.record(
                        "SL_FAILED",
                        symbol=symbol,
                        client_order_id=client_order_id,
                        reason=str(event.get("reason", kind)),
                    )
                break
        except Exception:
            return

    def _strategy_of_intent(self, intent_id: str) -> str:
        return intent_id.split(":")[0]

    def _process_broker_stream(self) -> None:
        """Drain venue events into engine transitions + journal (all adapters).

        Event dicts: ack / fill / reject / cancel, each carrying
        client_order_id (+ broker_order_id, and fill/reason payloads).
        Unknown orders or illegal transitions are journaled, never fatal:
        one bad venue message must not corrupt the session.
        """
        if self._broker is None:
            return
        try:
            events = self._broker.stream_events()
        except Exception as exc:
            self._broker_stream_errors += 1
            self._journal.record("BROKER_STREAM_ERROR", reason=str(exc))
            return
        for event in events:
            self._dispatch_broker_event(event)

    def _dispatch_broker_event(self, event: dict) -> None:
        kind = event.get("type", "")
        client_order_id = str(event.get("client_order_id", ""))
        tracked = self._engine.get(client_order_id) if client_order_id else None
        if tracked is None:
            self._journal.record("BROKER_EVENT_IGNORED", reason=f"unknown order: {client_order_id}")
            return
        try:
            if kind == "ack":
                updated = self._engine.transition(
                    client_order_id, OrderState.ACKNOWLEDGED, reason="broker ack"
                )
                self._journal.record(
                    "ORDER_ACK",
                    client_order_id=client_order_id,
                    broker_order_id=str(event.get("broker_order_id", "")),
                    broker=self._broker.name if self._broker else "",
                    environment=self._mode.value.lower(),
                )
                self._emit(
                    OrderAcknowledged(
                        self._request_id, client_order_id, str(event.get("broker_order_id", ""))
                    )
                )
                _ = updated
                self._note_sl_event(client_order_id, kind, event)
            elif kind == "fill":
                fill = event.get("fill")
                if fill is None:
                    self._journal.record(
                        "BROKER_EVENT_IGNORED",
                        reason=f"fill event without fill: {client_order_id}",
                    )
                    return
                self._apply_fill(tracked, fill)
                self._note_sl_event(client_order_id, kind, event)
            elif kind == "reject":
                reason = str(event.get("reason", "venue reject"))
                self._engine.transition(client_order_id, OrderState.REJECTED, reason=reason)
                self._journal.record(
                    "ORDER_REJECTED", client_order_id=client_order_id, reason=reason
                )
                self._emit(OrderRejected(self._request_id, client_order_id, reason))
                self._note_sl_event(client_order_id, kind, event)
            elif kind == "cancel":
                self._engine.transition(
                    client_order_id, OrderState.CANCELLED, reason="venue cancel"
                )
                self._journal.record("ORDER_CANCELLED", client_order_id=client_order_id)
                self._note_sl_event(client_order_id, kind, event)
            else:
                self._journal.record("BROKER_EVENT", at=client_order_id)
        except Exception as exc:
            self._journal.record(
                "BROKER_EVENT_IGNORED",
                reason=f"{kind} for {client_order_id}: {exc}",
            )

    def _drain_broker(self, now_epoch: float) -> None:  # noqa: ARG002
        """Kept for compatibility; stream processing now happens per event."""
        self._process_broker_stream()

    # ── recovery / teardown ─────────────────────────────────────

    def checkpoint(self) -> dict[str, Any]:
        """Persistable session state (bars windows, seqs, journal length).

        Includes the engine idempotency snapshot (FINAL §K) so a restart
        rebuilds known order/intent state and reconciles before submitting.
        """
        return {
            "mode": self._mode.value,
            "windows": {
                key: [
                    {
                        "symbol": b.symbol,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                        "timestamp": b.timestamp,
                        "bar_size": b.bar_size,
                    }
                    for b in LiveStrategyDriver(ctx).export_window()
                ]
                for key, ctx in self._contexts.items()
            },
            "orders_today": self._orders_today,
            "last_order_epoch": self._last_order_epoch,
            "engine": self._engine.snapshot(),
            # Protective-stop requirements survive restarts (stop price per
            # symbol + sequence); live SL orders re-arm only AFTER broker
            # reconciliation, never before.
            "stop_levels": dict(self._stop_levels),
            "sl_seq": self._sl_seq,
            "stop_protection": {
                symbol: {
                    "stop_price": prot.get("stop_price"),
                    "quantity": prot.get("quantity"),
                    "state": prot.get("state"),
                    "client_order_id": prot.get("client_order_id"),
                    "broker_order_id": prot.get("broker_order_id"),
                }
                for symbol, prot in self._stop_protection.items()
            },
        }

    def recover(self, checkpoint: dict[str, Any]) -> None:
        """Restore windows for re-warm (logic state rebuilds via warmup)."""
        if self._lifecycle.state == LifecycleState.RUNNING:
            self._lifecycle.transition(LifecycleState.STOPPING, reason="recover requested")
            self._lifecycle.transition(LifecycleState.STOPPED, reason="recover requested")
        self._lifecycle.transition(LifecycleState.RECOVERING, reason="recover requested")
        windows = checkpoint.get("windows", {})
        for key, ctx in self._contexts.items():
            # Fresh operational lifecycle; data comes from the checkpoint.
            # Position state rebuilds from fills (ledger is the truth).
            ctx.lifecycle = StrategyLifecycle()
            ctx.state = StrategyState()
            raw = windows.get(key, [])
            bars = tuple(
                Bar(
                    symbol=b["symbol"],
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    volume=b["volume"],
                    timestamp=b["timestamp"],
                    bar_size=b.get("bar_size", "live"),
                )
                for b in raw
            )
            LiveStrategyDriver(ctx).restore_window(bars)
        self._orders_today = int(checkpoint.get("orders_today", 0))
        self._last_order_epoch = checkpoint.get("last_order_epoch")
        # Restored stop requirements re-arm as PENDING: live SL orders are
        # placed only after broker reconciliation (see reconcile_now).
        raw_levels = checkpoint.get("stop_levels", {})
        if isinstance(raw_levels, dict):
            for symbol, stop in raw_levels.items():
                try:
                    price = float(stop)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    self._stop_levels[str(symbol)] = price
        try:
            self._sl_seq = int(checkpoint.get("sl_seq", 0))
        except (TypeError, ValueError):
            self._sl_seq = 0
        raw_prot = checkpoint.get("stop_protection", {})
        if isinstance(raw_prot, dict):
            for symbol, prot in raw_prot.items():
                if not isinstance(prot, dict):
                    continue
                self._stop_protection[str(symbol)] = {
                    "stop_price": prot.get("stop_price"),
                    "quantity": prot.get("quantity"),
                    "state": "PENDING",
                    "client_order_id": None,
                    "broker_order_id": prot.get("broker_order_id"),
                }
        # Idempotency state rebuilds BEFORE any new submission (FINAL §K):
        # restored orders (incl. UNKNOWN) must reconcile before trading.
        engine_data = checkpoint.get("engine", {})
        if engine_data:
            self._engine.restore(engine_data)
            restored = self._engine.snapshot()["orders"]
            unknowns = sum(1 for o in restored if o["state"] == OrderState.UNKNOWN.value)
            self._journal.record(
                "IDEMPOTENCY_RESTORED",
                orders=len(restored),
                unknown_orders=unknowns,
                reconcile_required=bool(restored),
            )
        # Ends at RECOVERING; start() moves RECOVERING -> VALIDATING explicitly.

    def arm(self, reason: str = "") -> LiveArm:
        """Explicit live arming: DISARMED → ARMING → ARMED. Journaled.

        Arming never starts order flow by itself (that needs RUNNING +
        per-signal risk approval); it only *permits* LIVE submissions.
        Paper/sandbox sessions ignore arming entirely.
        """
        self._armed = arm_transition(self._armed, LiveArm.ARMING)
        self._armed = arm_transition(self._armed, LiveArm.ARMED)
        self._journal.record("LIVE_ARMED", reason=reason)
        return self._armed

    def disarm(self, reason: str = "") -> LiveArm:
        """Release arming from any armed state. Journaled."""
        if self._armed != LiveArm.DISARMED:
            self._armed = arm_transition(self._armed, LiveArm.DISARMED)
            self._journal.record("LIVE_DISARMED", reason=reason)
        return self._armed

    @property
    def armed(self) -> LiveArm:
        return self._armed

    def stop(self) -> None:
        with suppress(Exception):
            self._lifecycle.transition(LifecycleState.STOPPING, reason="stop requested")
        with suppress(Exception):
            if self._broker is not None:
                self._broker.disconnect()
        with suppress(Exception):
            self._lifecycle.transition(LifecycleState.STOPPED, reason="stopped")

    # ── introspection (UI/ops consume these; UI never touches the broker) ──

    def state(self) -> dict[str, Any]:
        """Full runtime snapshot for operations/UI. No broker handles inside.

        Covers mode, broker identity/health, account environment, connection,
        strategies, positions, orders, fills, PnL, risk, reconciliation,
        latency and lifecycle. Secrets never appear (account ids only).
        """
        broker = self._broker
        broker_state: dict[str, Any] = {"connected": False}
        fills = 0
        if broker is not None:
            healthy, reason = broker.health()
            fills = len(getattr(broker, "fills", ()))
            try:
                account = broker.account()
            except Exception:
                account = {}
            broker_state = {
                "connected": healthy,
                "reason": reason,
                "name": broker.name,
                "mode": self._mode.value,
                "account_id": str(account.get("account_id", "")),
                "environment": str(account.get("environment", account.get("mode", ""))),
                "fills": fills,
                "broker_calls": self._broker_calls,
                "stream_errors": self._broker_stream_errors,
            }
        kinds: dict[str, int] = {}
        for entry in self._journal.entries:
            kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
        marks = {p.symbol: p.avg_price for p in self._ledger.all_positions()}
        snapshot = self._ledger.snapshot(marks)
        stream_health: dict[str, Any] = {"supported": False}
        if broker is not None:
            stream_fn: Any = getattr(broker, "order_stream_health", None)
            if callable(stream_fn):
                try:
                    stream_result: Any = stream_fn()
                    stream_ok, stream_reason = bool(stream_result[0]), str(stream_result[1])
                    stream_health = {
                        "supported": True,
                        "connected": bool(stream_ok),
                        "reason": str(stream_reason),
                    }
                except Exception as exc:
                    stream_health = {"supported": True, "connected": False, "reason": str(exc)}
        return {
            "mode": self._mode.value,
            "armed": self._armed.value,
            "lifecycle": self._lifecycle.state.value,
            "strategies": {
                key: {
                    "lifecycle": ctx.lifecycle.state.value,
                    "event_seq": ctx.event_seq,
                    "signals": ctx.signal_seq,
                }
                for key, ctx in self._contexts.items()
            },
            "broker": broker_state,
            "risk": {"kill_halted": self._risk.kill_switch.is_halted()},
            "positions": [
                {"symbol": p.symbol, "quantity": p.quantity, "avg_price": p.avg_price}
                for p in self._ledger.all_positions()
            ],
            "orders": {
                "open": len(self._engine.open_orders()),
                "total": len(self._engine._orders),
            },
            "fills": fills,
            "pnl": snapshot.day_pnl,
            "journal": kinds,
            "reconciliation": {"blocks_live": self._reconciliation.blocks_live},
            "order_stream": stream_health,
            "stop_protection": {
                symbol: {
                    "stop_price": prot.get("stop_price"),
                    "quantity": prot.get("quantity"),
                    "state": prot.get("state"),
                }
                for symbol, prot in self._stop_protection.items()
            },
            "sl_attention": sorted(self._sl_attention),
            "latency": {stage: self._latency.summary(stage) for stage in self._latency.stages()},
        }

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    @property
    def journal(self) -> ExecutionJournal:
        return self._journal

    @property
    def latency(self) -> LatencyTracker:
        return self._latency

    @property
    def ledger(self) -> PositionLedger:
        return self._ledger

    @property
    def engine(self) -> ExecutionEngine:
        return self._engine

    @property
    def recorder(self) -> LiveEventRecorder:
        return self._recorder

    @property
    def reconciliation(self) -> ReconciliationState:
        return self._reconciliation

    def reconcile_now(self) -> ReconciliationState:
        """Compare ledger vs broker truth; live stays blocked on mismatch."""
        if self._broker is None:
            return self._reconciliation
        try:
            broker_positions = self._broker.positions()
            broker_open = [o.get("client_order_id", "") for o in self._broker.open_orders()]
        except Exception as exc:
            self._journal.record("RECONCILE_ERROR", reason=str(exc))
            return self._reconciliation
        positions = reconcile_positions(tuple(self._ledger.all_positions()), broker_positions)
        orders = reconcile_orders(
            tuple(o.client_order_id for o in self._engine.open_orders()), tuple(broker_open)
        )
        self._reconciliation = ReconciliationState(positions=positions, orders=orders)
        verdict = self._reconciliation.verdict()
        self._journal.record(
            "RECONCILED",
            matched=not self._reconciliation.blocks_live,
            mismatches=len(positions.mismatches) + len(orders.mismatches),
            status=verdict.status.value,
        )
        # Post-reconcile SL re-arm (restart safety §17): open positions with
        # a known strategy stop and no working protection get re-armed ONLY
        # once the broker truth matches. Blocked reconciliation places
        # nothing — consistent with the pre-order gate.
        if not self._reconciliation.blocks_live:
            for position in self._ledger.all_positions():
                existing = self._stop_protection.get(position.symbol)
                if existing is not None and existing.get("state") in (
                    "SENT",
                    "ACKNOWLEDGED",
                    "WORKING",
                ):
                    continue
                self._place_protective_stop(position.symbol)
        return self._reconciliation
