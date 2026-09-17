"""Paper execution service — 00_app composition of the live architecture.

Runs a real strategy from the user strategy library against real SQLite
history through ReplayProvider → LiveSession → PaperBroker, headlessly:
no QApplication, no threads, no window. All trading objects come from the
existing layers (market repository, strategy compiler, risk, execution);
this module only composes them — the single place allowed to do so.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.modes import ExecutionMode
from execution.runtime.session import LiveSession, SessionConfig
from market import Bar
from market.repository.symbol_repository import SymbolRepository
from risk import RiskPolicy
from strategy import StrategyParameters
from strategy.language.compiler import compile_strategy
from strategy.language.storage import (
    list_strategies,
    load_strategy_record,
)
from strategy.language.storage import (
    strategy_dir as resolve_strategy_dir,
)
from strategy.models.definition import StrategyDefinition

# Default library root — resolved (env var → data_dir → per-user), not a
# hardcoded drive letter.
DEFAULT_STRATEGY_DIR = resolve_strategy_dir(None)


class PaperError(RuntimeError):
    """Paper startup failure — raised BEFORE any order path exists."""


@dataclass
class PaperRunConfig:
    data_dir: str | Path
    strategy_dir: str | Path | None = None
    symbol: str | None = None
    strategy: str | None = None
    timeframe: str | None = None
    limit: int | None = None
    capital: float = 1_000_000.0
    risk_policy: RiskPolicy = field(default_factory=RiskPolicy)


@dataclass
class PaperRunReport:
    strategy_id: str
    symbol: str
    timeframe: str
    mode: str
    events: int
    signals: int
    orders: int
    fills: int
    rejected: int
    pnl: float
    journal_path: str
    checkpoint_path: str
    clean_shutdown: bool


class PaperService:
    """Owns one headless paper run. Idempotent shutdown, no Qt, no threads."""

    def __init__(self, config: PaperRunConfig) -> None:
        self._config = config
        self._data_dir = Path(config.data_dir)
        self._strategy_dir = Path(config.strategy_dir) if config.strategy_dir else None
        self._repository = SymbolRepository(self._data_dir)
        self._session_dir = self._data_dir / "paper" / (config.symbol or "auto")
        self._session: LiveSession | None = None
        self._shutdown_done = False
        self._symbol = ""
        self._strategy_name = ""
        self._timeframe = config.timeframe or ""
        self._bars: tuple[Bar, ...] = ()

    # ── build (steps 1-8) ─────────────────────────────────────

    def prepare(self) -> None:
        """Load data + strategy, inspect the contract, stop on any gap."""
        symbols = self._repository.list_symbols()
        if not symbols:
            raise PaperError(f"no market data in {self._data_dir}")
        self._symbol = self._config.symbol or sorted(symbols)[0]
        if self._symbol not in symbols:
            raise PaperError(f"symbol not in store: {self._symbol}")
        strategy_dir = self._strategy_dir
        if strategy_dir is None or not strategy_dir.is_dir():
            raise PaperError(f"strategy library not found: {strategy_dir}")
        names = sorted(list_strategies(strategy_dir))
        if not names:
            raise PaperError(f"no strategies in library: {strategy_dir}")
        self._strategy_name = self._config.strategy or names[0]
        record = load_strategy_record(self._strategy_name, strategy_dir)
        if record is None:
            raise PaperError(f"strategy not found: {self._strategy_name}")
        try:
            compiled = compile_strategy(record.code)
        except Exception as exc:
            raise PaperError(f"strategy does not compile: {exc}") from exc
        params = StrategyParameters(compiled.param_defaults)

        def factory() -> Any:
            return compiled.create_logic(params)

        from execution.runtime.inspector import inspect_strategy

        definition = StrategyDefinition(
            id=record.id,
            name=record.name,
            version=record.version,
            kind="paper",
            params=params,
        )
        contract = inspect_strategy(definition, factory())
        if contract.missing:
            raise PaperError(f"strategy requirements missing: {', '.join(contract.missing)}")
        if self._config.timeframe:
            bars = self._repository.get_candles_timeframe(
                self._symbol, self._config.timeframe, self._config.limit
            )
            self._timeframe = self._config.timeframe
        else:
            bars = self._repository.get_candles(self._symbol, self._config.limit)
            self._timeframe = bars[0].bar_size if bars else ""
        if not bars:
            raise PaperError(f"no candles for {self._symbol}")
        self._bars = tuple(bars)
        candles = bars_to_candles(self._bars, self._timeframe or "live")
        provider = ReplayProvider(candles, chunk_size=256)
        session_dir = self._data_dir / "paper" / self._symbol
        self._session_dir = session_dir
        session = LiveSession(
            SessionConfig(
                mode=ExecutionMode.PAPER,
                paper_capital=self._config.capital,
                journal_path=str(session_dir / "journal.jsonl"),
                memory_path=str(session_dir / "memory.json"),
                kill_switch_path=str(session_dir / "kill.json"),
            ),
            provider,
            self._config.risk_policy,
        )
        session.register_strategy(record.id, record.version, factory, params, definition)
        checkpoint = session_dir / "checkpoint.json"
        if checkpoint.is_file():
            import json

            try:
                session.recover(json.loads(checkpoint.read_text(encoding="utf-8")))
            except Exception as exc:
                raise PaperError(f"checkpoint unreadable: {exc}") from exc
        self._session = session

    # ── run (steps 9-21) ──────────────────────────────────────

    def run(self) -> PaperRunReport:
        """Execute READY → RUNNING → drain → shutdown. Returns the summary."""
        if self._session is None:
            self.prepare()
        assert self._session is not None
        session = self._session
        warmup_bars = self._warmup_split(session)
        report = session.start((self._symbol,), self._timeframe or "live", warmup_bars)
        if not report.ready:
            raise PaperError(f"not ready: {'; '.join(report.reasons)}")
        session.reconcile_now()
        now = time.time()
        events = 0
        provider = session._provider
        from execution.market_data.replay import ReplayProvider

        assert isinstance(provider, ReplayProvider), "paper runs on replay tapes"
        try:
            while not provider.exhausted:
                events += session.step(now)
        finally:
            self.shutdown()  # idempotent: Ctrl+C lands here too
        return self.summarize(events)

    def _warmup_split(self, session: LiveSession) -> dict[str, tuple[Bar, ...]]:
        """Split stored bars into warmup history vs live tape per contract."""
        contexts = list(session._contexts.values())
        if not contexts:
            return {}
        needed = max(ctx.contract.warmup_bars for ctx in contexts)
        warmup = self._bars[:needed] if needed else ()
        live = self._bars[needed:] if needed else self._bars
        candles = bars_to_candles(live, self._timeframe or "live")
        session._provider = ReplayProvider(candles, chunk_size=256)
        return {ctx.strategy_id: warmup for ctx in contexts}

    # ── shutdown (step 22, idempotent) ────────────────────────

    def shutdown(self) -> None:
        """Stop orders, persist checkpoint, disconnect. Safe to call twice."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        session = self._session
        if session is None:
            return
        with contextlib.suppress(Exception):
            import json

            checkpoint = session.checkpoint()
            path = self._session_dir / "checkpoint.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
        with contextlib.suppress(Exception):
            session.stop()

    # ── observability (future UI consumes this, never the broker) ──

    def state(self) -> dict[str, Any]:
        """Runtime snapshot for operations/UI. No broker handles inside."""
        session = self._session
        if session is None:
            return {"started": False}
        ledger = session.ledger
        broker = session._broker
        fills = 0
        broker_state: dict[str, Any] = {"connected": False}
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
                "mode": session.mode.value,
                "account_id": str(account.get("account_id", "")),
                "environment": str(account.get("environment", account.get("mode", ""))),
                "fills": fills,
            }
        journal = session.journal
        kinds: dict[str, int] = {}
        for entry in journal.entries:
            kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
        snapshot = ledger.snapshot()
        return {
            "started": True,
            "mode": session.mode.value,
            "lifecycle": session._lifecycle.state.value,
            "strategy": {"name": self._strategy_name, "symbol": self._symbol},
            "data": {"bars": len(self._bars), "timeframe": self._timeframe},
            "broker": broker_state,
            "risk": {"kill_halted": session._risk.kill_switch.is_halted()},
            "positions": [
                {"symbol": p.symbol, "quantity": p.quantity, "avg_price": p.avg_price}
                for p in ledger.all_positions()
            ],
            "orders": {
                "open": len(session.engine.open_orders()),
                "total": len(session.engine._orders),
            },
            "fills": broker_state.get("fills", 0),
            "pnl": snapshot.day_pnl,
            "journal": kinds,
            "reconciliation": {"blocks_live": session.reconciliation.blocks_live},
            "latency": {
                stage: session.latency.summary(stage) for stage in session.latency.stages()
            },
        }

    def summarize(self, events: int) -> PaperRunReport:
        """Build the operational summary (also the --paper printout)."""
        state = self.state()
        journal = state.get("journal", {})
        broker = state.get("broker", {})
        entries = list(self._session.journal.entries) if self._session else []
        rejected = sum(1 for e in entries if e.kind in ("ORDER_REJECTED", "RISK_DENIED"))
        return PaperRunReport(
            strategy_id=self._strategy_name,
            symbol=self._symbol,
            timeframe=self._timeframe,
            mode="PAPER",
            events=events,
            signals=int(journal.get("SIGNAL_GENERATED", 0)),
            orders=int(state.get("orders", {}).get("total", 0)),
            fills=int(broker.get("fills", 0)),
            rejected=rejected,
            pnl=float(state.get("pnl", 0.0)),
            journal_path=str(self._session_dir / "journal.jsonl"),
            checkpoint_path=str(self._session_dir / "checkpoint.json"),
            clean_shutdown=self._shutdown_done,
        )


def format_paper_report(report: PaperRunReport) -> str:
    """Concise operational printout. Never claims LIVE."""
    return "\n".join(
        [
            "VAYREN PAPER",
            f"Strategy: {report.strategy_id}",
            f"Data: {report.symbol} {report.timeframe}",
            "Mode: PAPER",
            "",
            "Lifecycle:",
            "RECOVER ok",
            "RECONCILE ok",
            "VALIDATE ok",
            "WARMUP ok",
            "READY ok",
            "RUNNING ok",
            "",
            f"Events: {report.events}",
            f"Signals: {report.signals}",
            f"Orders: {report.orders}",
            f"Fills: {report.fills}",
            f"Rejected: {report.rejected}",
            f"PnL: {report.pnl:+.2f}",
            "",
            f"Shutdown: {'CLEAN' if report.clean_shutdown else 'UNCLEAN'}",
        ]
    )


def run_paper(args: Any, selection_service: Any = None) -> int:
    """CLI entry for --paper. Returns the process exit code.

    ``selection_service`` (M4) is the authoritative BrokerSelectionService;
    when absent (legacy/test callers) one is established locally so the
    paper run still reads the single selection source.
    """
    from risk import RiskPolicy

    if selection_service is None:
        from app.__init__ import establish_selection

        selection_service = establish_selection(args)
    selection = selection_service.current()

    try:
        service = PaperService(
            PaperRunConfig(
                data_dir=args.data_dir,
                strategy_dir=getattr(args, "strategy_dir", None) or DEFAULT_STRATEGY_DIR,
                symbol=getattr(args, "paper_symbol", None),
                strategy=getattr(args, "paper_strategy", None),
                timeframe=getattr(args, "paper_timeframe", None),
                limit=args.limit,
                risk_policy=RiskPolicy(),
            )
        )
        report = service.run()
    except PaperError as exc:
        print(f"PAPER ABORTED: {exc}")
        return 1
    except Exception as exc:
        print(f"PAPER FAILED: {exc}")
        return 1
    print(format_paper_report(report))
    print(
        f"Broker selection: {selection.name} ({selection.environment.value}) — {selection.reason}"
    )
    return 0


def run_check_live(args: Any, selection_service: Any = None) -> int:
    """CLI entry for --check-live. Diagnostic only: never places an order.

    Evaluates strategy/data readiness plus the five live gates against the
    real resolution paths. Always returns 0 when the evaluation itself ran
    (readiness is data, printed as LIVE READY / LIVE NOT READY).

    M4: the probe adapter name comes from the authoritative selection when
    the selected broker serves the trading domain; otherwise the legacy
    "live" probe is kept (compatibility shim — it still fails closed).
    """
    from execution.broker.credentials import (
        BrokerCredentials,
        EnvCredentialStore,
        default_account_id,
    )
    from execution.broker.factory import resolve_broker
    from execution.broker.gates import evaluate_live_gates, format_gates_report
    from execution.modes import ExecutionMode, gates_from_env
    from risk import RiskPolicy

    if selection_service is None:
        from app.__init__ import establish_selection

        selection_service = establish_selection(args)
    selection = selection_service.current()

    try:
        service = PaperService(
            PaperRunConfig(
                data_dir=args.data_dir,
                strategy_dir=getattr(args, "strategy_dir", None) or DEFAULT_STRATEGY_DIR,
                symbol=getattr(args, "paper_symbol", None),
                strategy=getattr(args, "paper_strategy", None),
                timeframe=getattr(args, "paper_timeframe", None),
                limit=args.limit,
                risk_policy=RiskPolicy(),
            )
        )
        service.prepare()
    except PaperError as exc:
        print(f"LIVE NOT READY\n[FAIL] PREPARATION — {exc}")
        return 0
    except Exception as exc:
        print(f"CHECK-LIVE FAILED: {exc}")
        return 1
    session = service._session
    assert session is not None
    contracts = [ctx.contract for ctx in session._contexts.values()]
    strategy_ok = all(c.complete for c in contracts) and bool(contracts)
    data_ok = bool(service._bars)
    # M4: probe the SELECTED broker's trading face when it has one; the
    # selection itself never bypasses the gates (a paper/sandbox account
    # still cannot confirm as LIVE — confirm_account enforces environment).
    from broker.capabilities import Domain

    selected_allows_trading, trading_reason = selection_service.surface_allowed(Domain.TRADING)
    probe_name = selection.name if selected_allows_trading else "live"
    try:
        broker, effective, downgrade_notes = resolve_broker(
            ExecutionMode.LIVE, gates_from_env(), adapter_name=probe_name
        )
        # A downgrade to PAPER is not a live-capable adapter: the gate
        # must fail rather than bless paper plumbing as LIVE ready.
        if effective != ExecutionMode.LIVE:
            adapter, adapter_error = None, "; ".join(downgrade_notes) or "downgraded"
        else:
            adapter, adapter_error = broker, ""
    except Exception as exc:
        adapter, adapter_error = None, str(exc)
    creds = BrokerCredentials(
        account_id=default_account_id(),
        environment="live",
        key_refs=("API_KEY", "API_SECRET"),
    )
    report = evaluate_live_gates(
        adapter=adapter,
        adapter_error=adapter_error,
        credentials=creds,
        credential_store=EnvCredentialStore(),
        expected_account_id=default_account_id(),
        expected_environment="live",
        risk_policy=service._config.risk_policy,
        kill_halted=session._risk.kill_switch.is_halted(),
    )
    lines = [format_gates_report(report)]
    lines.append(
        f"SELECTED BROKER: {selection.name} ({selection.environment.value}) — {selection.reason}"
    )
    if not selected_allows_trading:
        lines.append(f"LIVE PROBE: {trading_reason}")
    lines.append(
        f"[{'ok' if strategy_ok else 'FAIL'}] STRATEGY_REQUIREMENTS"
        + ("" if strategy_ok else " — contract incomplete")
    )
    lines.append(
        f"[{'ok' if data_ok else 'FAIL'}] DATA_REQUIREMENTS" + ("" if data_ok else " — no candles")
    )
    lines.append("ARMING: DISARMED (no live session exists to arm)")
    import datetime

    lines.append(
        "CLOCK: local UTC "
        + datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        + " (local reference only, no venue clock to compare)"
    )
    lines.append("ENVIRONMENT: requested=live configured=none")
    lines.append("RECONCILIATION: not evaluated (no live session)")
    env_gates = gates_from_env()
    lines.append(
        f"env flags live={env_gates.live_trading_enabled} broker={env_gates.broker_live_enabled}"
    )
    print("\n".join(lines))
    with contextlib.suppress(Exception):
        service.shutdown()
    return 0
