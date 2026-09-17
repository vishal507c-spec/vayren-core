"""Bootstrap — the only place event subscriptions are created."""

import contextlib
import uuid
from logging import getLogger
from pathlib import Path
from typing import Any

from chart.engine.chart_engine import ChartEngine
from chart.events.chart_ready import ChartReady
from chart.manifest import chart_manifest
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.tools_toolbar import ChartToolsToolbar
from chart.widgets.watchlist_widget import WatchlistWidget
from chart.windows.chart_window import ChartWindow
from core.event_bus.event_bus import EventBus
from core.events.app_started import AppStarted
from core.registry.component_registry import ComponentRegistry
from core.registry.registry import Registry
from core.system.system_model import SystemModel
from data import (
    DownloadSettings,
    DownloadWorker,
    HistoricalDownloadEngine,
    data_manifest,
)
from data.provider.manager import ProviderCredentialsManager
from data.ui.historical_panel import HistoricalDownloadPanel
from market.events.data_loaded import DataLoaded
from market.events.list_symbols import ListSymbols
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.quotes_loaded import QuotesLoaded
from market.events.symbols_listed import SymbolsListed
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from market.loader.market_data_loader import MarketDataLoader
from market.loader.quote_loader import QuoteLoader
from market.loader.symbol_list_loader import SymbolListLoader
from market.loader.timeframe_list_loader import TimeframeListLoader
from market.manifest import market_manifest
from market.repository.symbol_repository import SymbolRepository
from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QApplication

from app.lifecycle.lifecycle import AppLifecycle

logger = getLogger(__name__)


def _history_domain() -> Any:
    """The UBL historical-data domain (lazy import keeps bootstrap light)."""
    from broker.capabilities import Domain

    return Domain.HISTORICAL_DATA


def _trading_domain() -> Any:
    """The UBL trading domain."""
    from broker.capabilities import Domain

    return Domain.TRADING


def _on_broker_selected(name: str, selection_service: Any, data_window: Any) -> None:
    """Handle one UI selection change (fail-closed, previous state kept)."""
    from broker.selection import SelectionError

    try:
        outcome = selection_service.select(name)
    except SelectionError as exc:
        logger.warning("broker selection rejected (%s): %s", name, exc)
        data_window.show_broker_error(str(exc))
        return
    data_window.set_broker_selection(outcome.selection, outcome.capabilities)
    data_window.show_broker_error("")


class _HistoryDomain:
    """Namespace alias so bootstrap reads like the design table."""

    HISTORICAL_DATA = _history_domain()


def build_execution_history(
    result: Any, strategy_registry: Any, data_dir: str
) -> tuple[str | None, list[tuple[str, str]]]:
    """Build and save the immutable execution history for one result.

    Verbatim semantics of the former inline Phase-5 block: resolves the
    canonical strategy/version, materializes per-trade events + signals,
    writes the JSON history and lineage. Pure data work (no widgets) so it
    can run off the UI thread. Returns ``(last_execution_id, log_lines)``
    with one ``(level, text)`` line per result, exactly as logged inline.
    """
    import hashlib

    from backtest.execution import (
        ExecutionEvent,
        ExecutionHistory,
        create_snapshot,
        save_history,
    )
    from strategy.language.storage import (
        get_strategy_by_id,
        load_strategy,
        load_strategy_record,
    )
    from strategy.version import list_versions

    saved_id: str | None = None
    saved_version = ""
    lines: list[tuple[str, str]] = []
    for res in result.results:
        # Find strategy definition and resolve canonical UUID via library
        try:
            definition = strategy_registry.get(res.strategy_id)
            # Resolve canonical id: prefer library UUID if name exists
            canonical_id = definition.id
            source = None
            version = None
            # Try to map definition.name -> library UUID
            try:
                rec = load_strategy_record(definition.name, data_dir)  # type: ignore[attr-defined]
                if rec is not None:
                    canonical_id = rec.id
                    source = rec.code
            except Exception:
                pass
            if source is None:
                source = load_strategy(definition.name, data_dir) or ""
            # Also try get_strategy_by_id with registry id (might be UUID already)
            if not source:
                rec2 = get_strategy_by_id(definition.id, data_dir)
                if rec2 is not None:
                    canonical_id = rec2.id
                    source = rec2.code
            strategy_id = canonical_id
            # Find latest version for canonical strategy
            versions = list_versions(strategy_id, data_dir)
            if versions:
                version = versions[-1]
                source_hash = version.source_hash
                ir_version = version.ir_version
                version_id = version.version_id
            else:
                # Fallback: if canonical had no version, check slug-based listing (backward compat)  # noqa: E501
                alt_versions = list_versions(definition.id, data_dir)
                if alt_versions:
                    version = alt_versions[-1]
                    strategy_id = definition.id
                    source_hash = version.source_hash
                    ir_version = version.ir_version
                    version_id = version.version_id
                else:
                    source_hash = (
                        hashlib.sha256((source or "").encode("utf-8")).hexdigest() if source else ""
                    )
                    ir_version = 1  # noqa: F841
                    version_id = "v0"
            # Build snapshot — exact version reference preserved forever (Python-native)
            snap = create_snapshot(
                strategy_id,
                version_id,
                source_hash,
                None,
                dict(definition.params) if hasattr(definition, "params") else {},
                res.config,  # type: ignore[attr-defined]
                data_dir,
            )
            # Build generic events from trades
            events = []
            seq = 0
            for t in res.trades:
                events.append(
                    ExecutionEvent(
                        execution_id=snap.execution_id,
                        sequence=seq,
                        event_type="TradeClosed",
                        timestamp=t.exit_time,
                        data={
                            "symbol": t.symbol,
                            "side": t.side,
                            "pnl": t.pnl,
                            "entry": t.entry_price,
                            "exit": t.exit_price,
                        },
                    )
                )
                seq += 1
            # Also add BarProcessed-like for replay determinism (using equity curve length)  # noqa: E501
            for idx, pt in enumerate(res.equity_curve[:5]):
                events.append(
                    ExecutionEvent(
                        execution_id=snap.execution_id,
                        sequence=seq,
                        event_type="BarProcessed",
                        timestamp=pt.timestamp,
                        data={"index": idx},
                    )
                )
                seq += 1
            signals = [
                {
                    "index": t.entry_index,
                    "kind": "BUY" if t.side == "LONG" else "SELL",
                    "price": t.entry_price,
                    "timestamp": t.entry_time,
                }
                for t in res.trades
            ]
            history = ExecutionHistory(
                snapshot=snap, events=events, signals=signals, trades=res.trades
            )
            save_history(history, data_dir)
            # Lineage: VERSION -> EXECUTION (and STRATEGY -> VERSION already via version creation)  # noqa: E501
            try:
                from strategy.research.lineage import (
                    load_lineage,
                    save_lineage,
                )

                g = load_lineage(data_dir)
                snap_id = snap.execution_id
                g.add_node("STRATEGY", strategy_id)
                g.add_node("VERSION", version_id)
                g.add_edge(
                    "STRATEGY", strategy_id, "VERSION", version_id, relationship="has_version"
                )
                g.add_edge(
                    "VERSION",
                    version_id,
                    "EXECUTION",
                    snap_id,
                    relationship="executed_as",
                )
                g.add_edge(
                    "STRATEGY",
                    strategy_id,
                    "EXECUTION",
                    snap_id,
                    relationship="executed",
                )
                save_lineage(g, data_dir)
            except Exception:
                pass
            saved_id = snap.execution_id
            saved_version = version_id
            lines.append(("INFO", f"Execution {saved_id} saved (v{saved_version[:8]})"))
        except Exception as e:  # noqa: BLE001
            lines.append(("WARN", f"History save failed: {e}"))
    return saved_id, lines


class _HistorySaved(QObject):
    """Completion emitter for the background history save (UI-thread slot)."""

    finished = Signal(object)  # (generation, execution_id | None, log_lines)


class _HistorySaveTask(QRunnable):
    """QRunnable wrapper so the 193k-trade JSON build never blocks the UI."""

    def __init__(
        self,
        result: Any,
        strategy_registry: Any,
        data_dir: str,
        generation: int,
        emitter: _HistorySaved,
    ) -> None:
        super().__init__()
        self._result = result
        self._strategy_registry = strategy_registry
        self._data_dir = data_dir
        self._generation = generation
        self._emitter = emitter

    def run(self) -> None:
        try:
            execution_id, lines = build_execution_history(
                self._result, self._strategy_registry, self._data_dir
            )
        except Exception as exc:  # noqa: BLE001
            execution_id, lines = None, [("WARN", f"History save failed: {exc}")]
        self._emitter.finished.emit((self._generation, execution_id, lines))
        self._result = None  # release the heavy result reference


class Bootstrap:
    """Constructs the bus, services and registry; wires every subscription.

    This is the single wiring point of the application and the ONLY place
    EventBus.subscribe may be called. Modules never subscribe themselves.
    """

    def __init__(
        self,
        data_dir: str | Path,
        limit: int | None = None,
        selection_service: Any | None = None,
        strategy_dir: str | Path | None = None,
    ) -> None:
        self._bus = EventBus()
        self._services: Registry[Any] = Registry()
        self._data_dir = Path(data_dir)
        # Strategy Library root — injected by the composition root (CLI
        # ``--strategy-dir`` / ``VAYREN_STRATEGIES``) or derived from
        # ``data_dir``. Never a hardcoded drive letter.
        from strategy.language.storage import strategy_dir as _resolve_strategy_dir

        self._strategy_dir = (
            Path(strategy_dir) if strategy_dir is not None else _resolve_strategy_dir(data_dir)
        )

        # ── authoritative broker selection (Phase 21 M4) ──
        # Every broker-facing surface reads THE selection through this
        # service; no component keeps its own broker state.
        from app.services.broker_selection_service import BrokerSelectionService

        if selection_service is None:
            import data.provider.factory  # noqa: F401  (seeds zerodha)
            import execution.broker.factory  # noqa: F401  (seeds paper/sandbox)

            from app.services.broker_selection_service import app_selection_store

            selection_service = BrokerSelectionService(app_selection_store(data_dir))
        self._selection_service: BrokerSelectionService = selection_service
        selection = selection_service.current()

        repository = SymbolRepository(data_dir)
        loader = MarketDataLoader(repository, self._bus)
        symbol_loader = SymbolListLoader(repository, self._bus)
        quote_loader = QuoteLoader(repository, self._bus)
        timeframe_loader = TimeframeListLoader(repository, self._bus)
        engine = ChartEngine(self._bus)
        widget = CandleChartWidget()
        watchlist = WatchlistWidget()
        toolbar = TimeframeToolbar()
        tools = ChartToolsToolbar()
        data_window = HistoricalDownloadPanel(self._bus)

        # ── Strategy Lab platform — VM-only (no Python builtins) ──
        from backtest import BacktestRunner, BacktestWorker  # noqa: I001
        from backtest.ui.overlay import TradeOverlay
        from strategy.registry import StrategyRegistry

        # legacy panels kept for service registry compat but not shown in market
        from strategy.ui.control_panel import StrategyControlPanel
        from strategy.ui.strategy_list_panel import StrategyListPanel

        from app.ui.event_log_panel import EventLogPanel
        from app.ui.market_status_panel import MarketStatusPanel
        from app.ui.strategy_lab_workspace import StrategyLabWorkspace
        from app.ui.system_health_panel import SystemHealthPanel
        from app.ui.top_nav_bar import TopNavBar

        strategy_registry = StrategyRegistry()

        # Strategy Library is user-owned — do NOT auto-create OBR/SMA files.
        # ``ensure_builtin_strategies`` is now a deprecated no-op kept only
        # for backward compat; no seeding on VAYREN START.

        # BacktestRunner is Python-only: .py Strategy -> PythonStrategy -> Signal
        # Strategy source = the resolved library root (CLI/env/data_dir), never a
        # hardcoded drive letter.
        backtest_runner = BacktestRunner(
            repository, registry=strategy_registry, data_dir=self._strategy_dir
        )
        backtest_worker = BacktestWorker(backtest_runner)
        trade_overlay = TradeOverlay()
        widget.set_overlay(trade_overlay)
        # Plot overlay — generic TradingView plot() primitive
        try:
            from chart.renderer.plot_renderer import PlotOverlay

            plot_overlay = PlotOverlay()
            plot_overlay.set_visibility_checker(widget.is_indicator_visible)  # type: ignore[attr-defined]
            # Single overlay handles all plot series; per-series visibility checked inside
            # Store under generic key, visibility per series title/strategy handled internally
            widget.set_named_overlay("PLOT", plot_overlay)
        except Exception:
            plot_overlay = None  # type: ignore[assignment]

        # legacy controls (kept for service list, not injected into market)
        lab_control = StrategyControlPanel()
        lab_list = StrategyListPanel()
        # dedicated single-strategy lab workspace (isolated from market)
        lab_workspace = StrategyLabWorkspace()
        # LIVE execution is native Slint only (constitution §3): no legacy
        # Qt workspace is constructed or mounted. The same provider state
        # feeds the in-window Slint viewport constructed below.

        def _live_state_provider() -> dict:
            """Read-only snapshot for the LIVE tab. Never trades, never raises."""
            from execution.broker.credentials import (
                BrokerCredentials,
                EnvCredentialStore,
                default_account_id,
            )
            from execution.broker.gates import evaluate_live_gates

            state: dict = {
                "mode": "PAPER",
                "broker": {
                    "name": "NOT CONFIGURED",
                    "environment": "",
                    "connected": None,
                    "reason": "no live venue adapter is registered",
                    "capabilities": [],
                    "latency_ms": None,
                    "last_heartbeat": None,
                },
                "strategy": None,
                "position": None,
                "orders": [],
                "fills": [],
                "pnl": {},
                "risk": {"status": "READY", "limits": [], "decisions": []},
                "reconciliation": {
                    "status": "NOT CONFIGURED",
                    "positions": "N/A",
                    "orders": "N/A",
                    "last_check": None,
                    "mismatches": 0,
                    "blocks_live": True,
                },
                "kill": {"halted": False, "level": "global"},
                "gates": [],
                "can_arm": False,
                "arm_blockers": [],
                "can_halt": False,
                "lifecycle": "STOPPED",
                "events": [],
            }
            try:
                # ── M4: broker identity from THE authoritative selection ──
                # The pill may only name a broker that actually serves the
                # trading domain; anything else stays NOT CONFIGURED (no
                # fake readiness). Gates stay backend-authoritative.
                trading_allowed, trading_reason = selection_service.surface_allowed(
                    _trading_domain()
                )
                sel = selection_service.current_or_none()
                if trading_allowed and sel is not None:
                    state["broker"]["name"] = sel.name
                    state["broker"]["environment"] = sel.environment.value
                    state["broker"]["reason"] = f"selected: {sel.reason}"
                else:
                    state["broker"]["reason"] = trading_reason or "no live venue adapter"
                state["broker"]["selection"] = (
                    {
                        "name": sel.name,
                        "environment": sel.environment.value,
                        "origin": sel.reason,
                    }
                    if sel is not None
                    else None
                )
                # SYSTEM → BROKERS is the auth authority: overlay the manager's
                # live state so the LIVE pill shows CONNECTED / LOGIN_REQUIRED
                # without any broker logic leaking into this view.
                manager = getattr(self, "_broker_manager", None)
                if manager is not None and sel is not None:
                    with contextlib.suppress(Exception):
                        bstate = manager.state(sel.name) or {}
                        status = getattr(bstate.get("status"), "value", None)
                        if status:
                            state["broker"]["status"] = status
                            if bstate.get("reason"):
                                state["broker"]["reason"] = str(bstate["reason"])
                            state["broker"]["connected"] = status in ("CONNECTED", "LIVE_READY")
                names: list[str] = []
                with contextlib.suppress(Exception):
                    from strategy.language.storage import list_strategies

                    names = sorted(list_strategies(self._strategy_dir))
                if names:
                    state["strategy"] = {
                        "id": names[0],
                        "version": "",
                        "status": "NOT RUNNING",
                        "mode": "PAPER",
                        "params": {},
                        "instrument": "",
                        "timeframe": "",
                        "live_supported": None,
                        "warmup": None,
                        "state": "STOPPED",
                    }
                try:
                    symbols = repository.list_symbols()
                except Exception:
                    symbols = ()
                state["market_symbol"] = ""
                state["market_timeframe"] = ""
                state["market_bars"] = None
                creds = BrokerCredentials(
                    account_id=default_account_id(),
                    environment="live",
                    key_refs=("API_KEY", "API_SECRET"),
                )
                report = evaluate_live_gates(
                    adapter=None,
                    credentials=creds,
                    credential_store=EnvCredentialStore(),
                    expected_account_id=default_account_id(),
                    expected_environment="live",
                    risk_policy=None,
                    kill_halted=False,
                )
                gates = []
                for gate in report.gates:
                    gates.append(
                        {
                            "name": gate.name,
                            "status": "READY" if gate.passed else "NOT READY",
                            "reason": gate.detail,
                        }
                    )
                gates.append(
                    {
                        "name": "STRATEGY",
                        "status": "READY" if names else "NOT READY",
                        "reason": "" if names else "strategy library is empty",
                    }
                )
                gates.append(
                    {
                        "name": "MARKET DATA",
                        "status": "READY" if symbols else "NOT READY",
                        "reason": "" if symbols else "no symbols in store",
                    }
                )
                gates.append({"name": "CLOCK", "status": "READY", "reason": "local clock only"})
                gates.append(
                    {
                        "name": "ENVIRONMENT",
                        "status": "NOT READY",
                        "reason": "requested=live configured=none",
                    }
                )
                gates.append(
                    {
                        "name": "ARMING",
                        "status": "NOT READY",
                        "reason": "DISARMED — no live session exists to arm",
                    }
                )
                state["gates"] = gates
                state["arm_blockers"] = [
                    f"{g['name']}: {g['reason']}"
                    for g in gates
                    if g["status"] != "READY" and g["reason"]
                ] or ["LIVE broker is not configured"]
                # ── LIVE trading backend (Strategy Lab → LIVE bridge) ──
                # Service snapshot owns runtime + setup keys; the static
                # broker-selection pill and UBL gates above stay authoritative
                # for identity/readiness display.
                try:
                    live = live_service.snapshot()
                except Exception:  # noqa: BLE001
                    live = {}
                if isinstance(live, dict):
                    for key in (
                        "mode",
                        "session_status",
                        "status_reason",
                        "positions",
                        "position",
                        "orders",
                        "fills",
                        "pnl",
                        "risk",
                        "reconciliation",
                        "kill",
                        "can_halt",
                        "lifecycle",
                        "events",
                        "market_symbol",
                        "market_timeframe",
                        "market_bars",
                        "available_strategies",
                        "available_symbols",
                        "selected_symbols",
                        "available_timeframes",
                        "selected_timeframe",
                        "quantity",
                        "start_blockers",
                        "active_positions",
                        "open_orders",
                        "account",
                        "execution",
                        "feed",
                    ):
                        if live.get(key) is not None or key == "position":
                            state[key] = live.get(key)
                    # Readiness rows concatenate: UBL venue gates first, then
                    # the service rows (ACCOUNT / ORDER EXECUTION / FEED).
                    service_gates = live.get("gates") or []
                    if isinstance(service_gates, list) and service_gates:
                        state["gates"] = [*gates, *service_gates]
                        state["arm_blockers"] = [
                            *state.get("arm_blockers", []),
                            *[
                                f"{g['name']}: {g['reason']}"
                                for g in service_gates
                                if isinstance(g, dict)
                                and g.get("status") != "READY"
                                and g.get("reason")
                            ],
                        ]
                    if live.get("strategy") is not None:
                        state["strategy"] = live["strategy"]
                        broker_live = live.get("broker") or {}
                        if isinstance(broker_live, dict):
                            state["broker"]["connected"] = broker_live.get("connected")
                            if broker_live.get("reason"):
                                state["broker"]["reason"] = broker_live["reason"]
            except Exception:
                pass
            return state

        # ── SYSTEM → BROKERS: centralized broker management ──
        # One manager owns configuration/authentication/connection state for
        # every venue; LIVE and the download engine only consume it. All
        # network/SDK work runs on the manager's worker thread; the UI sees
        # snapshots + signals only.
        from app.services.broker_manager import BrokerManager

        broker_manager = BrokerManager(data_dir=data_dir)
        self._broker_manager = broker_manager
        # SYSTEM is native Slint only (constitution §3): no legacy Qt
        # brokers workspace is constructed or mounted. Backend flows
        # (configure/login/check/disconnect/remove) stay in `BrokerManager`,
        # untouched; the in-window Slint viewport below renders status.
        from app.services.slint_system_host import SlintSystemHost

        def _system_state_provider() -> dict:
            """Read-only SYSTEM snapshot: current selection + manager record."""
            try:
                sel = selection_service.current_or_none()
            except Exception:
                return {"selection": None, "record": None, "callback_url": ""}
            if sel is None:
                return {"selection": None, "record": None, "callback_url": ""}
            try:
                snapshot = broker_manager.snapshot()
            except Exception:
                snapshot = {}
            record = None
            for entry in snapshot.get("brokers", []) if isinstance(snapshot, dict) else []:
                if isinstance(entry, dict) and entry.get("id") == sel.name:
                    record = entry
                    break
            try:
                environment = sel.environment.value
            except Exception:
                environment = "paper"
            return {
                "selection": {"name": sel.name, "environment": environment},
                "record": record,
                "callback_url": str(snapshot.get("callback_url", "") or ""),
            }

        slint_system_host = SlintSystemHost(state_provider=_system_state_provider)
        self._slint_system_host = slint_system_host
        # Panel action intents route to the SAME manager methods the
        # retained Qt cards used (validation/auth/safety live in the
        # manager, untouched here). REMOVE arrives only after the Slint
        # inline YES/NO confirmation.
        slint_system_host.login_requested.connect(
            lambda broker_id: broker_manager.start_login(broker_id)
        )
        slint_system_host.refresh_requested.connect(
            lambda broker_id: broker_manager.submit_check(broker_id)
        )
        slint_system_host.disconnect_requested.connect(
            lambda broker_id: broker_manager.disconnect_broker(broker_id)
        )
        slint_system_host.configure_requested.connect(
            lambda broker_id, values: broker_manager.configure(broker_id, values)
        )
        slint_system_host.remove_requested.connect(
            lambda broker_id: broker_manager.remove(broker_id)
        )

        def _on_copy_callback(_broker_id: str) -> None:
            try:
                from PySide6.QtWidgets import QApplication

                url = str(broker_manager.snapshot().get("callback_url", "") or "")
                clipboard = QApplication.clipboard()
                if clipboard is not None and url:
                    clipboard.setText(url)
            except Exception:  # noqa: BLE001
                pass

        slint_system_host.copy_callback_requested.connect(_on_copy_callback)

        def _on_broker_message(text: str) -> None:
            panel = getattr(self, "_event_log", None)
            with contextlib.suppress(Exception):
                if panel is not None:
                    panel.add_entry("INFO", f"Broker: {text}")

        def _on_broker_state_changed(_broker_id: str) -> None:
            with contextlib.suppress(Exception):
                system_host = getattr(self, "_slint_system_host", None)
                if system_host is not None:
                    system_host.refresh_now()
                live_host = getattr(self, "_slint_live_host", None)
                if live_host is not None:
                    live_host.refresh_now()

        broker_manager.message.connect(_on_broker_message)
        broker_manager.state_changed.connect(_on_broker_state_changed)
        # Startup session check: stored token → CONNECTED or LOGIN_REQUIRED.
        for broker_id in broker_manager.broker_ids():
            broker_manager.submit_check(broker_id)

        # LIVE trading backend: Strategy Lab store → per-symbol LiveSessions.
        # The workspace stays a pure view; every action arrives here as a Qt
        # signal (subscriptions still only on the bus — G2 untouched).
        from app.services.live_trading_service import LiveTradingService

        live_service = LiveTradingService(
            data_dir=data_dir,
            strategy_dir=self._strategy_dir,
            selection_service=selection_service,
            broker_manager=broker_manager,
        )
        self._live_service = live_service

        def _on_live_setup(setup: Any) -> None:
            try:
                if not isinstance(setup, dict):
                    return
                live_service.configure(
                    strategy_name=str(setup.get("strategy_name", "") or ""),
                    symbols=tuple(setup.get("symbols", ()) or ()),
                    timeframe=str(setup.get("timeframe", "") or ""),
                    quantity=setup.get("quantity", None),
                )
            except Exception:  # noqa: BLE001
                pass

        def _on_live_start(confirmed: bool = False) -> None:
            """START intent (Qt workspace signal or Slint host action).

            LIVE mode keeps the existing explicit-consent dialog unless the
            operator already confirmed in the Slint confirmation control
            (the host forwards ``confirmed=True``). PAPER never prompts.
            """
            try:
                if live_service.config.mode == "LIVE" and not confirmed:
                    from PySide6.QtWidgets import QMessageBox

                    answer = QMessageBox.warning(
                        window,
                        "Confirm LIVE trading",
                        "Start REAL-MONEY live trading with strategy "
                        f"'{live_service.config.strategy_name}' on "
                        f"{', '.join(live_service.config.symbols)}?",
                        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    if answer != QMessageBox.StandardButton.Ok:
                        return
                    live_service.start(confirmed=True)
                else:
                    live_service.start(confirmed=confirmed)
            except Exception:  # noqa: BLE001
                pass

        def _on_live_halt() -> None:
            """HALT intent: stop the session immediately (kill-switch path
            of the service — no new signals, open orders stay tracked)."""
            with contextlib.suppress(Exception):
                live_service.stop(reason="execution halted")

        # LIVE is native Slint only (constitution §3): no legacy Qt
        # workspace is constructed or mounted here. The same
        # action handlers the Qt signals used are wired to the in-window
        # Slint viewport below — backend semantics unchanged.
        from backtest.execution import list_histories
        from strategy.language.storage import list_strategies

        from app.services.research_service import ResearchService
        from app.ui.research_workspace import ResearchWorkspace

        research_service = ResearchService(
            data_dir=data_dir,
            strategy_dir=self._strategy_dir,
            list_strategies_fn=list_strategies,
            list_histories_fn=list_histories,
            repository=repository,
        )
        research_workspace = ResearchWorkspace()
        research_workspace.set_service(research_service)
        # In-window native Slint Research viewport (constitution §3): the
        # production RESEARCH surface. The viewport draws Rust+Slint pixels
        # only and dispatches UI intents back to the SAME Python Research
        # service/engine — zero Qt Research presentation is mounted in the
        # route below (legacy workspace kept injected only as no-host
        # fallback).
        from app.services.slint_research_host import SlintResearchHost

        slint_research_host = SlintResearchHost(service=research_service)
        self._slint_research_host = slint_research_host
        # Portfolio is native Slint only (constitution §3): no legacy Qt
        # workspace is constructed or mounted.
        # This viewport draws Slint pixels only — no Qt Portfolio UI — fed by
        # the same provider shape the LIVE tab consumes. Degrades to an
        # honest status line when the native library is not built.
        from app.services.slint_portfolio_host import SlintPortfolioHost

        slint_portfolio_host = SlintPortfolioHost(state_provider=_live_state_provider)
        self._slint_portfolio_host = slint_portfolio_host
        # STRATEGY LAB is native Slint in production (constitution §3): the
        # legacy Qt workspace stays constructed as the canonical state holder
        # and engine bridge (subscriptions/updates keep feeding it), but it is
        # NOT mounted — this viewport draws Slint pixels only, fed by
        # read-only snapshots of that state; Slint interactions are drained
        # back into the same public methods/signals a user click would use.
        from app.services.slint_strategy_lab_host import (
            SlintStrategyLabHost,
            apply_lab_strategy_action,
            strategy_lab_snapshot_dict,
        )

        slint_lab_host = SlintStrategyLabHost(
            state_provider=lambda: strategy_lab_snapshot_dict(lab_workspace),
            action_sink=lambda action: apply_lab_strategy_action(lab_workspace, action),
        )
        self._slint_lab_host = slint_lab_host
        # LIVE is native Slint only (constitution §3): this viewport draws
        # Slint pixels only — no Qt Live UI — fed by the same provider the
        # removed legacy Qt workspace consumed. UI action intents (accepted
        # fail-closed in Rust) arrive back as the SAME signal contract that
        # Qt workspace carried, so the live_service handlers below are
        # unchanged. Degrades to an honest status line when the native
        # library is not built.
        from app.services.slint_live_host import SlintLiveHost

        slint_live_host = SlintLiveHost(state_provider=_live_state_provider)
        self._slint_live_host = slint_live_host
        slint_live_host.configure_broker_requested.connect(
            lambda: getattr(window, "show_brokers", lambda: None)()
        )
        slint_live_host.setup_changed.connect(_on_live_setup)
        slint_live_host.start_requested.connect(_on_live_start)
        slint_live_host.stop_requested.connect(lambda: live_service.stop())
        slint_live_host.halt_requested.connect(_on_live_halt)
        slint_live_host.arm_requested.connect(
            lambda: logger.debug("slint live host: arm requested (no armed backend)")
        )
        slint_live_host.mode_requested.connect(
            lambda mode: (
                live_service.configure(mode=str(mode)) if str(mode) in ("PAPER", "LIVE") else None
            )
        )
        from backtest.ui.performance_panel import PerformancePanel

        performance_panel = PerformancePanel()
        # Phase 3: generic — sync params from currently selected strategy, fallback to first available  # noqa: E501
        try:
            current_name = ""
            try:  # noqa: SIM105
                current_name = lab_workspace.current_tab_name().strip()  # type: ignore[attr-defined]
            except Exception:
                pass
            if not current_name:
                try:  # noqa: SIM105
                    current_name = lab_workspace.left_nav.current_name() or ""  # type: ignore[attr-defined]
                except Exception:
                    pass
            target_id = (current_name or "").lower().replace(" ", "-") if current_name else ""
            obr_def = None
            if target_id:
                with contextlib.suppress(Exception):
                    obr_def = strategy_registry.get(target_id)
            if obr_def is None:
                try:
                    defs = strategy_registry.list()
                    if defs:
                        obr_def = defs[0]
                except Exception:
                    pass
            if obr_def is not None:
                lab_workspace.center_detail.set_params(dict(obr_def.params))
        except Exception:
            pass

        nav_bar = TopNavBar()
        market_status = MarketStatusPanel()
        event_log = EventLogPanel()
        system_health = SystemHealthPanel(data_dir=data_dir)

        from app.ui.trade_context_panel import TradeContextPanel

        trade_context_panel = TradeContextPanel()

        window = ChartWindow(
            widget,
            watchlist,
            toolbar,
            tools,
            self._bus,
            limit=limit,
            download=data_window,
            nav=nav_bar,
            left_extra=market_status,
            lab_workspace=lab_workspace,
            research_workspace=research_workspace,
            slint_research_host=slint_research_host,
            slint_portfolio_host=slint_portfolio_host,
            slint_live_host=slint_live_host,
            slint_system_host=slint_system_host,
            slint_lab_host=slint_lab_host,
            event_log=event_log,
            system_health=system_health,
            trade_context=trade_context_panel,
        )
        # MARKET is native Slint in production (constitution §3): the Qt
        # market splitter widgets stay alive as the canonical state holders
        # (subscriptions and the ChartReady chain keep feeding them) but the
        # splitter is NOT mounted — this viewport draws Slint pixels only,
        # fed by read-only snapshots of the same widgets; Slint interactions
        # drain back through the SAME signals a user click emits.
        from app.services.slint_market_host import (
            SlintMarketHost,
            apply_market_action,
            market_snapshot_dict,
        )

        slint_market_host = SlintMarketHost(
            state_provider=lambda: market_snapshot_dict(window),
            action_sink=lambda action: apply_market_action(window, action),
        )
        self._slint_market_host = slint_market_host
        window.set_slint_market_host(slint_market_host)
        lifecycle = AppLifecycle(self._bus)

        # ── Chart session persistence (TradingView exact restore) ──
        from chart.session.session_store import ChartSessionStore

        chart_session_store = ChartSessionStore()
        _initial_chart_session = chart_session_store.load()
        # pre-seed window with saved symbol/timeframe so on_symbols_listed can restore
        # (validated later against actual symbol list)
        try:
            if _initial_chart_session.symbol:
                window._current_symbol = _initial_chart_session.symbol  # type: ignore[attr-defined]
            if _initial_chart_session.timeframe:
                window._current_timeframe = _initial_chart_session.timeframe  # type: ignore[attr-defined]
        except Exception:
            pass
        # restore active indicators (name, visible, settings)
        try:
            for ind in _initial_chart_session.indicators:
                with contextlib.suppress(Exception):
                    widget.add_indicator(ind.name)
                    # restore visibility
                    if not ind.visible:
                        widget.set_indicator_visible(ind.name, False)
                    # restore settings if any (e.g., OBR params) — keep for future
                    if ind.settings:
                        # store settings on widget for later use (not affecting calculation)
                        with contextlib.suppress(Exception):
                            if not hasattr(widget, "_indicator_settings"):
                                widget._indicator_settings = {}  # type: ignore[attr-defined]
                            widget._indicator_settings[ind.name] = dict(ind.settings)  # type: ignore[attr-defined]
        except Exception:
            pass

        # ── auto-save helper (symbol/timeframe/indicator add-remove/visibility/settings/layout) ──
        def _save_chart_session(*_a: Any, **_kw: Any) -> None:
            try:
                from chart.session.session_store import ChartSession, IndicatorState

                # collect active indicators — only those with a row (TradingView active)
                inds: list[IndicatorState] = []
                try:
                    for rname in list(widget.visibility_panel.indicators):  # type: ignore[attr-defined]
                        vis = widget.is_indicator_visible(rname)  # type: ignore[attr-defined]
                        settings: dict[str, Any] = {}
                        try:
                            if hasattr(widget, "_indicator_settings"):
                                settings = dict(widget._indicator_settings.get(rname, {}))  # type: ignore[attr-defined]
                        except Exception:
                            settings = {}
                        inds.append(IndicatorState(name=rname, visible=vis, settings=settings))
                except Exception:
                    inds = []
                # chart layout stub (future)
                layout: dict[str, Any] = {}
                with contextlib.suppress(Exception):
                    layout["follow_latest"] = bool(getattr(widget, "_follow_latest", True))
                sess = ChartSession(
                    symbol=getattr(window, "_current_symbol", None),
                    timeframe=getattr(window, "_current_timeframe", None),
                    indicators=inds,
                    chart_layout=layout,
                )
                chart_session_store.save(sess)
            except Exception:
                pass

        # connect auto-save to all relevant signals (symbol/timeframe/indicator)
        with contextlib.suppress(Exception):
            window.session_changed.connect(_save_chart_session)
        with contextlib.suppress(Exception):
            widget.session_changed.connect(_save_chart_session)  # type: ignore[attr-defined]
        # also direct panel signals as fallback (widget already emits via session_changed)
        with contextlib.suppress(Exception):
            widget.visibility_panel.visibility_changed.connect(
                lambda *_a: _save_chart_session()  # type: ignore[attr-defined]
            )
            widget.visibility_panel.indicator_removed.connect(
                lambda *_a: _save_chart_session()  # type: ignore[attr-defined]
            )
        # ensure we save on close/crash via aboutToQuit
        with contextlib.suppress(Exception):
            app_inst = QApplication.instance()
            if isinstance(app_inst, QApplication):
                app_inst.aboutToQuit.connect(_save_chart_session)

        # ── historical data: provider DERIVED from the selection (M4) ──
        # `settings.provider` is no longer an independent source of truth:
        # the authoritative selection decides which plugin's historical
        # face is used; without the capability the download path fails
        # closed with an explicit, honest reason (no silent fallback).
        history_allowed, history_reason = selection_service.surface_allowed(
            _HistoryDomain.HISTORICAL_DATA
        )
        data_settings = DownloadSettings(data_dir=data_dir, provider=selection.name)
        if history_allowed:
            # M7 direct UBL resolution: the composition root resolves the
            # selected broker's historical face through the single
            # BrokerRegistry. The legacy build_provider shim is retained
            # only as a compatibility delegate and is no longer used here.
            from broker.registry import default_registry
            from data.provider.contract import Provider

            record = default_registry().get(selection.name)
            face = record.plugin.face(_HistoryDomain.HISTORICAL_DATA, data_settings)
            if not isinstance(face, Provider):
                raise ValueError(
                    f"provider {selection.name!r} historical face does not satisfy "
                    "the Provider contract"
                )
            data_provider = face
        else:
            from data.provider.factory import unavailable_provider

            data_provider = unavailable_provider(history_reason)
            logger.warning("historical data unavailable: %s", history_reason)
        data_credentials = ProviderCredentialsManager(data_settings, data_provider)
        data_engine = HistoricalDownloadEngine(data_settings, provider=data_provider)
        data_worker = DownloadWorker(data_engine)
        data_engine.set_reporter(data_worker)
        data_window.set_credentials_manager(data_credentials)
        # Broker selection UI: registry-backed choices in, selection signal out.
        data_window.set_broker_choices(selection_service.broker_choices())
        data_window.set_broker_selection(selection)
        data_window.broker_selected.connect(
            lambda name: _on_broker_selected(name, selection_service, data_window)
        )

        # ── Trade → Chart instant context (§1-34) ───────────────
        from app.services.multi_symbol_backtest import MultiSymbolBacktestCoordinator
        from app.services.trade_chart_controller import TradeChartController

        trade_chart_controller = TradeChartController(
            self._bus, widget, window, repository, trade_overlay, trade_context_panel, lab_workspace
        )
        multi_symbol_coordinator = MultiSymbolBacktestCoordinator(self._bus, backtest_worker)

        # dynamic attrs for validation bookkeeping
        self._last_train_result: object | None = None
        self._last_test_cfg: object | None = None
        # keep references for closures
        self._strategy_registry = strategy_registry
        self._lab_control = lab_control
        self._lab_list = lab_list
        self._lab_workspace = lab_workspace
        self._trade_overlay = trade_overlay
        try:
            self._plot_overlay = plot_overlay  # type: ignore[name-defined]
        except Exception:
            self._plot_overlay = None  # type: ignore[attr-defined]
        self._performance_panel = performance_panel
        self._market_status = market_status
        self._event_log = event_log
        self._system_health = system_health
        self._nav_bar = nav_bar
        self._widget = widget
        self._trade_context_panel = trade_context_panel
        self._trade_chart_controller = trade_chart_controller
        self._multi_symbol_coordinator = multi_symbol_coordinator

        self._register_services(
            repository,
            loader,
            symbol_loader,
            quote_loader,
            timeframe_loader,
            engine,
            window,
            lifecycle,
            data_engine,
            data_worker,
            data_window,
            strategy_registry,
            backtest_runner,
            backtest_worker,
            trade_overlay,
            lab_control,
            lab_list,
            lab_workspace,
            performance_panel,
            nav_bar,
            market_status,
            event_log,
            system_health,
        )
        self._wire_events(
            loader,
            symbol_loader,
            quote_loader,
            timeframe_loader,
            engine,
            window,
            lifecycle,
            data_worker,
            data_window,
            strategy_registry,
            backtest_runner,
            backtest_worker,
            trade_overlay,
            lab_control,
            lab_list,
            lab_workspace,
            performance_panel,
            nav_bar,
            market_status,
            event_log,
            system_health,
            widget,
        )
        self._build_architecture(
            repository, engine, data_engine, strategy_registry, backtest_runner
        )

    def _build_architecture(
        self,
        repository: SymbolRepository,
        engine: ChartEngine,
        data_engine: HistoricalDownloadEngine,
        strategy_registry: Any,
        backtest_runner: Any,
    ) -> None:
        """Register the real components and build the system model."""
        from backtest.manifest import backtest_manifest
        from strategy.manifest import strategy_manifest

        self._components = ComponentRegistry()
        self._components.register(
            market_manifest(),
            implementations={
                "data.query.candles": repository,
                "data.query.timeframes": repository,
                "data.query.quotes": repository,
                "data.transform.aggregate": repository,
            },
        )
        self._components.register(chart_manifest(), implementations={"chart.render": engine})
        self._components.register(
            data_manifest(),
            implementations={
                "historical_data.download": data_engine,
                "historical_data.coverage": data_engine,
                "historical_data.status": data_engine,
            },
        )
        self._components.register(
            strategy_manifest(),
            implementations={
                "strategy.registry": strategy_registry,
                "strategy.runtime": strategy_registry,
            },
        )
        self._components.register(
            backtest_manifest(),
            implementations={
                "backtest.run": backtest_runner,
                "backtest.metrics": backtest_runner,
            },
        )
        self._system_model = SystemModel(self._components)

    def _register_services(
        self,
        repository: SymbolRepository,
        loader: MarketDataLoader,
        symbol_loader: SymbolListLoader,
        quote_loader: QuoteLoader,
        timeframe_loader: TimeframeListLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
        data_engine: HistoricalDownloadEngine,
        data_worker: DownloadWorker,
        data_window: HistoricalDownloadPanel,
        strategy_registry: Any,
        backtest_runner: Any,
        backtest_worker: Any,
        trade_overlay: Any,
        lab_control: Any,
        lab_list: Any,
        lab_workspace: Any,
        performance_panel: Any,
        nav_bar: Any,
        market_status: Any,
        event_log: Any,
        system_health: Any,
    ) -> None:
        self._services.register("symbol_repository", repository)
        self._services.register("market_data_loader", loader)
        self._services.register("symbol_list_loader", symbol_loader)
        self._services.register("quote_loader", quote_loader)
        self._services.register("timeframe_list_loader", timeframe_loader)
        self._services.register("chart_engine", engine)
        self._services.register("chart_window", window)
        self._services.register("app_lifecycle", lifecycle)
        self._services.register("data_engine", data_engine)
        self._services.register("data_worker", data_worker)
        self._services.register("data_window", data_window)
        self._services.register("strategy_registry", strategy_registry)
        self._services.register("backtest_runner", backtest_runner)
        self._services.register("backtest_worker", backtest_worker)
        self._services.register("trade_overlay", trade_overlay)
        if hasattr(self, "_multi_symbol_coordinator"):
            self._services.register("multi_symbol_coordinator", self._multi_symbol_coordinator)  # type: ignore[attr-defined]
        self._services.register("lab_control_panel", lab_control)
        self._services.register("lab_strategy_list", lab_list)
        self._services.register("lab_workspace", lab_workspace)
        self._services.register("lab_performance_panel", performance_panel)
        self._services.register("top_nav_bar", nav_bar)
        self._services.register("market_status_panel", market_status)
        self._services.register("event_log_panel", event_log)
        self._services.register("system_health_panel", system_health)
        # trade-context services (Strategy Lab → Chart)
        if hasattr(self, "_trade_context_panel"):
            self._services.register("trade_context_panel", self._trade_context_panel)  # type: ignore[attr-defined]
        if hasattr(self, "_trade_chart_controller"):
            self._services.register("trade_chart_controller", self._trade_chart_controller)  # type: ignore[attr-defined]

    def _wire_events(
        self,
        loader: MarketDataLoader,
        symbol_loader: SymbolListLoader,
        quote_loader: QuoteLoader,
        timeframe_loader: TimeframeListLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
        data_worker: DownloadWorker,
        data_window: HistoricalDownloadPanel,
        strategy_registry: Any,
        backtest_runner: Any,  # noqa: ARG002
        backtest_worker: Any,
        trade_overlay: Any,
        lab_control: Any,
        lab_list: Any,
        lab_workspace: Any,
        performance_panel: Any,
        nav_bar: Any,
        market_status: Any,
        event_log: Any,
        system_health: Any,
        widget: Any,
    ) -> None:
        self._bus.subscribe(AppStarted, lifecycle.on_app_started)
        self._bus.subscribe(ListSymbols, symbol_loader.on_list_symbols)
        self._bus.subscribe(SymbolsListed, window.on_symbols_listed)
        self._bus.subscribe(SymbolsListed, quote_loader.on_symbols_listed)
        self._bus.subscribe(SymbolsListed, data_window.on_symbols_listed)
        self._bus.subscribe(QuotesLoaded, window.on_quotes_loaded)
        self._bus.subscribe(LoadSymbol, loader.on_load_symbol)
        self._bus.subscribe(TimeframeChanged, loader.on_timeframe_changed)
        self._bus.subscribe(ListTimeframes, timeframe_loader.on_list_timeframes)
        self._bus.subscribe(TimeframesListed, window.on_timeframes_listed)
        self._bus.subscribe(DataLoaded, engine.on_data_loaded)
        self._bus.subscribe(ChartReady, window.on_chart_ready)

        # ── TradingView: keep active indicators alive across symbol/timeframe, recalculate ──
        def _recalc_active_indicators(event: ChartReady) -> None:  # type: ignore[no-untyped-def]
            try:
                # Do not trigger automatic recalc while a trade context is active
                # (would overwrite Strategy Lab trades and clear chart context)
                try:
                    tcc = getattr(self, "_trade_chart_controller", None)
                    if tcc is not None and getattr(tcc, "_selected_index", None) is not None:
                        return
                except Exception:
                    pass
                # ...nor while a multi-symbol batch is in flight (the merged
                # aggregate event drives the final recalc instead)
                try:
                    multi = getattr(self, "_multi_symbol_coordinator", None)
                    if multi is not None and multi.active:  # type: ignore[attr-defined]
                        return
                except Exception:
                    pass
                # only recalc if there are active indicators (panel has rows)
                active = []
                try:
                    active = list(widget.visibility_panel.indicators)  # type: ignore[attr-defined]
                except Exception:
                    active = []
                if not active:
                    return
                # for each active strategy indicator, rerun with new symbol/timeframe
                # keep settings/visibility/style intact, just change data source
                for ind_name in active:
                    # check if this indicator is a strategy (in STRATEGIES list)
                    is_strategy = False
                    try:
                        from strategy.language.storage import list_strategies

                        strats = list_strategies(self._strategy_dir)
                        if ind_name in strats or ind_name.lower() == "obr":
                            is_strategy = True
                        # also check registry
                        if not is_strategy:
                            try:
                                # strategy ids are lower-kebab
                                sid = ind_name.lower().replace(" ", "-")
                                if strategy_registry.contains(sid):
                                    is_strategy = True
                            except Exception:
                                pass
                    except Exception:
                        is_strategy = ind_name.lower() == "obr"
                    if not is_strategy:
                        # pure indicator (VWAP, EMA, etc.) — no backtest, just keep row
                        # recalculation will happen via its own renderer on next paint
                        continue
                    # strategy: rerun plots on the new chart bars (live REF HIGH/LOW)
                    self._run_strategy_plots(ind_name, event.model.bars)
                    # also rerun backtest with new symbol/timeframe, preserve settings
                    try:
                        from strategy.language.storage import load_strategy_record

                        rec = load_strategy_record(ind_name, self._strategy_dir)
                        if rec is None and ind_name.lower() == "obr":
                            # fallback for OBR
                            rec = load_strategy_record("OBR", self._strategy_dir)
                        if rec is None:
                            continue
                        strategy_id = rec.id
                        symbol = event.model.symbol
                        timeframe = event.model.timeframe
                        # use current chart's bar range for backtest period
                        try:
                            bars = event.model.bars
                            start_date = bars[0].timestamp[:10] if bars else "2024-01-01"
                            end_date = bars[-1].timestamp[:10] if bars else "2026-12-31"
                        except Exception:
                            start_date = "2024-01-01"
                            end_date = "2026-12-31"
                        from backtest.events import RunBacktest

                        self._bus.publish(
                            RunBacktest(
                                request_id=uuid.uuid4().hex[:8],
                                strategy_ids=(strategy_id,),
                                symbol=symbol,  # type: ignore[arg-type]
                                timeframe=timeframe,
                                start_date=start_date,
                                end_date=end_date,
                            )
                        )
                    except Exception:
                        continue
            except Exception:
                pass

        # Connect indicator_added signal to live plot runner (chart bars via widget._model)
        widget.indicator_added.connect(
            lambda name: self._run_strategy_plots(name, widget._model.bars if widget._model else ())
        )
        # Session-restore fix: indicators added BEFORE this connection
        # (via bootstrap init session restore) never fired indicator_added.
        # Run plots for any already-active visible indicators now.
        if widget._model is not None and widget._model.bars:
            for ind_name, vis in widget._indicator_visible.items():
                if vis:
                    self._run_strategy_plots(ind_name, widget._model.bars)
        self._bus.subscribe(ChartReady, _recalc_active_indicators)
        data_worker.started.connect(self._bus.publish)
        data_worker.progress.connect(self._bus.publish)
        data_worker.completed.connect(self._bus.publish)
        data_worker.failed.connect(self._bus.publish)
        data_worker.coverage.connect(self._bus.publish)
        data_worker.log.connect(data_window.on_log_line)
        data_window.close_requested.connect(lambda: window.toggle_panel("download"))
        # ── multi-symbol batch: the worker runs one bounded batch job and
        # reports back directly (stock-level progress + per-symbol outcomes);
        # only the final merged result travels the bus to the regular UI ──
        _multi = getattr(self, "_multi_symbol_coordinator", None)
        if _multi is not None:
            backtest_worker.batch_progress.connect(_multi.on_batch_progress)  # type: ignore[attr-defined]
            backtest_worker.batch_done.connect(_multi.on_batch_done)  # type: ignore[attr-defined]
            backtest_worker.batch_failed.connect(_multi.on_batch_failed)  # type: ignore[attr-defined]
        # ── Strategy Lab wiring ──
        self._wire_lab(
            strategy_registry,
            backtest_worker,
            trade_overlay,
            lab_control,
            lab_list,
            lab_workspace,
            performance_panel,
            nav_bar,
            market_status,
            event_log,
            system_health,
            window,
            widget,
        )
        # ── trade-chart controller bind LAST so its ChartReady runs after window clears stale overlay  # noqa: E501
        try:
            ctrl = getattr(self, "_trade_chart_controller", None)
            if ctrl is not None and hasattr(ctrl, "bind"):
                ctrl.bind()  # type: ignore[attr-defined]
        except Exception:
            pass
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.aboutToQuit.connect(data_worker.shutdown)
            app.aboutToQuit.connect(backtest_worker.shutdown)
            if hasattr(system_health, "stop"):
                app.aboutToQuit.connect(system_health.stop)

    def _wire_lab(
        self,
        strategy_registry: Any,
        backtest_worker: Any,
        trade_overlay: Any,
        lab_control: Any,
        lab_list: Any,
        lab_workspace: Any,
        performance_panel: Any,
        nav_bar: Any,
        market_status: Any,
        event_log: Any,
        system_health: Any,
        window: ChartWindow,
        widget: Any,
    ) -> None:
        from backtest.events import (
            BacktestCompleted,
            BacktestFailed,
            BacktestProgress,
            BacktestStarted,
            RunBacktest,
        )
        from strategy.events import LabReset, PaperTradeRequested
        from strategy.models.form import StrategyDraft

        # ── nav bar ──
        nav_bar.market_clicked.connect(window.show_market)
        if hasattr(nav_bar, "strategy_lab_clicked"):
            nav_bar.strategy_lab_clicked.connect(window.show_lab)
        if hasattr(nav_bar, "research_clicked"):
            nav_bar.research_clicked.connect(window.show_slint_research)
        # PORTFOLIO is owned by the native Slint view hosted in-window (see
        # SlintPortfolioHost wiring below). No Qt Portfolio surface is mounted.
        if hasattr(nav_bar, "portfolio_clicked"):
            nav_bar.portfolio_clicked.connect(window.show_slint_portfolio)
        # LIVE is owned by the native Slint view hosted in-window (see
        # SlintLiveHost wiring above). The legacy Qt workspace was removed
        # — only its backend contracts remain.
        if hasattr(nav_bar, "live_clicked"):
            nav_bar.live_clicked.connect(window.show_slint_live)
        # SYSTEM → native Slint viewport (see SlintSystemHost wiring above).
        # The legacy Qt brokers workspace was removed — only its backend
        # contracts remain.
        if hasattr(nav_bar, "system_clicked"):
            nav_bar.system_clicked.connect(window.show_slint_system)

        # ── control ↔ bus ──
        def _on_backtest_form(form: Any) -> None:
            from backtest.validation import validate_backtest_form

            symbol = window.current_symbol
            timeframe = form.timeframe
            enabled_ids = {d.id for d in strategy_registry.enabled()}
            errors = validate_backtest_form(form, symbol, enabled_ids)
            if errors:
                for err in errors:
                    event_log.add_entry("WARN", f"Validation failed: {err}")
                return
            request_id = uuid.uuid4().hex[:8]
            self._bus.publish(
                RunBacktest(
                    request_id=request_id,
                    strategy_ids=(form.strategy_id,),
                    symbol=symbol,  # type: ignore[arg-type]
                    timeframe=timeframe,
                    start_date=form.start_date,
                    end_date=form.end_date,
                    initial_capital=form.initial_capital,
                    slippage_pct=form.slippage_pct,
                    commission_pct=form.commission_pct,
                )
            )

        lab_control.backtest_requested.connect(_on_backtest_form)
        lab_control.paper_trade_requested.connect(
            lambda sid: (
                self._bus.publish(
                    PaperTradeRequested(
                        strategy_id=sid,
                        symbol=window.current_symbol or "",
                        timeframe=window.current_timeframe or "",
                    )
                ),
                event_log.add_entry(
                    "WARN", "Paper trading requires execution engine (not yet available)"
                ),
            )
        )
        lab_control.reset_requested.connect(lambda: self._bus.publish(LabReset()))

        # ── strategy list ↔ registry ──
        def _refresh_strategies() -> None:
            from strategy.events import StrategiesListed

            strats = strategy_registry.list()
            event = StrategiesListed(strategies=strats)
            self._bus.publish(event)
            lab_control.set_strategies(tuple(s for s in strats if s.enabled) or strats)
            lab_list.set_strategies(strats)
            system_health.set_engine_state("Strategy Engine", f"{len(strats)} strategies")

        def _on_strategy_toggled(sid: str, enabled: bool) -> None:
            try:
                strategy_registry.set_enabled(sid, enabled)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy {'enabled' if enabled else 'disabled'}: {sid}")
            _refresh_strategies()

        def _on_allocation(sid: str, pct: float) -> None:
            try:
                strategy_registry.set_allocation(sid, pct)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
            _refresh_strategies()

        def _on_duplicate(sid: str) -> None:
            try:
                strategy_registry.duplicate(sid)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy duplicated: {sid}")
            _refresh_strategies()

        def _on_remove(sid: str) -> None:
            try:
                strategy_registry.remove(sid)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy removed: {sid}")
            _refresh_strategies()

        def _on_add() -> None:
            from strategy.ui.strategy_dialog import StrategyDialog

            kinds = strategy_registry.kinds
            if not kinds:
                event_log.add_entry("WARN", "No strategy kinds registered")
                return
            specs_map = {k: strategy_registry.param_specs(k) for k in kinds}
            dialog = StrategyDialog(kinds=kinds, specs_map=specs_map, parent=window)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            draft: StrategyDraft | None = dialog.draft()
            if draft is None:
                event_log.add_entry("WARN", "Validation failed: check strategy form fields")
                return
            self._create_strategy_from_draft(
                draft, strategy_registry, event_log, _refresh_strategies
            )

        def _on_configure(sid: str) -> None:
            from strategy.models.parameters import StrategyParameters
            from strategy.ui.strategy_dialog import StrategyDialog

            try:
                definition = strategy_registry.get(sid)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            kinds = strategy_registry.kinds
            specs_map = {k: strategy_registry.param_specs(k) for k in kinds}
            dialog = StrategyDialog(
                kinds=kinds, specs_map=specs_map, definition=definition, parent=window
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            draft = dialog.draft()
            if draft is None:
                event_log.add_entry("WARN", "Validation failed: check strategy form fields")
                return
            try:
                strategy_registry.set_params(sid, StrategyParameters(draft.params))
                strategy_registry.set_allocation(sid, draft.allocation_pct)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy configured: {sid}")
            _refresh_strategies()

        lab_list.strategy_toggled.connect(_on_strategy_toggled)
        lab_list.allocation_changed.connect(_on_allocation)
        lab_list.duplicate_requested.connect(_on_duplicate)
        lab_list.remove_requested.connect(_on_remove)
        lab_list.configure_requested.connect(_on_configure)
        lab_list.add_requested.connect(_on_add)

        # performance → overlay highlight
        performance_panel.trade_selected.connect(trade_overlay.set_highlight)
        # ── Strategy Lab workspace (single-strategy) ──
        if lab_workspace is not None:
            # sync symbols/timeframes — selector universe is the Market
            # Watchlist only (never the full NSE universe)
            def _feed_watchlist_symbols(*_a: Any) -> None:
                with contextlib.suppress(Exception):
                    lab_workspace.right_settings.set_symbols(window.watchlist.symbols)

            self._bus.subscribe(SymbolsListed, _feed_watchlist_symbols)
            with contextlib.suppress(Exception):
                window.watchlist.watchlist_changed.connect(_feed_watchlist_symbols)
            self._bus.subscribe(
                TimeframesListed,
                lambda e: lab_workspace.right_settings.set_timeframes(e.timeframes),
            )  # noqa: E501
            self._bus.subscribe(
                ChartReady,
                lambda e: lab_workspace.right_settings.select_timeframe(e.model.timeframe),
            )  # noqa: E501

            # Phase 3: generic — canonical is StrategyRecord.id (UUID)  # noqa: E501
            def _current_strategy_id() -> str:
                # Prefer resolving via Strategy Library (UUID)  # noqa: E501
                def _resolve_canonical(name: str, data_dir: str | None) -> str:
                    try:
                        from strategy.language.storage import (
                            load_strategy_record,
                        )

                        rec = load_strategy_record(name, data_dir)
                        if rec is not None:
                            return rec.id
                    except Exception:
                        pass
                    # Backward compat fallback for builtins / registry-kinds (slug)
                    return name.lower().replace(" ", "-")

                # Need data_dir for library lookup
                try:
                    _d = _strategy_data_dir()  # type: ignore[has-type]
                except Exception:
                    _d = None
                try:
                    name = lab_workspace.current_tab_name().strip()  # type: ignore[attr-defined]
                    if name:
                        return _resolve_canonical(name, _d)
                except Exception:
                    pass
                try:
                    name = lab_workspace.left_nav.current_name()  # type: ignore[attr-defined]
                    if name:
                        return _resolve_canonical(str(name), _d)
                except Exception:
                    pass
                try:
                    defs = strategy_registry.list()
                    if defs:
                        return defs[0].id
                except Exception:
                    pass
                return ""

            def _on_workspace_param(key: str, value: float) -> None:
                sid = _current_strategy_id()
                try:
                    cur = strategy_registry.get(sid)
                except Exception:
                    # Generic fallback: first available strategy
                    try:
                        defs = strategy_registry.list()
                        if defs:
                            cur = defs[0]
                            sid = cur.id
                        else:
                            raise
                    except Exception as exc:  # noqa: BLE001
                        event_log.add_entry("ERROR", str(exc))
                        return
                try:
                    new_params = dict(cur.params)
                    new_params[key] = float(value)
                    from strategy.models.parameters import StrategyParameters

                    strategy_registry.set_params(sid, StrategyParameters(new_params))
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            lab_workspace.param_changed.connect(_on_workspace_param)

            # workspace run → generic: currently selected strategy
            def _on_workspace_run(cfg: Any) -> None:
                from backtest.validation import validate_backtest_form
                from strategy.models.form import BacktestForm

                sid = _current_strategy_id()
                # Generic fallback: first available strategy if selected not in registry
                if not strategy_registry.contains(sid):
                    try:
                        defs = strategy_registry.list()
                        if defs and not strategy_registry.contains(sid):
                            sid = defs[0].id
                    except Exception:
                        pass
                form = BacktestForm(
                    strategy_id=sid,
                    timeframe=cfg.get("timeframe", ""),
                    start_date=cfg.get("start_date", ""),
                    end_date=cfg.get("end_date", ""),
                    initial_capital=cfg.get("initial_capital", 1_000_000),
                    slippage_pct=cfg.get("slippage_pct", 0.02),
                    commission_pct=cfg.get("commission_pct", 0.03),
                )
                symbols = tuple(cfg.get("symbols") or ())
                symbol = symbols[0] if symbols else (cfg.get("symbol") or window.current_symbol)
                # Lab → LIVE handoff: mirror symbols/timeframe to the LIVE tab.
                with contextlib.suppress(Exception):
                    svc = getattr(self, "_live_service", None)
                    if svc is not None:
                        svc.configure(symbols=symbols, timeframe=form.timeframe)
                enabled_ids = {d.id for d in strategy_registry.enabled()}
                errors = validate_backtest_form(form, symbol, enabled_ids)
                if errors:
                    for err in errors:
                        event_log.add_entry("WARN", f"Validation failed: {err}")
                    # Surface in Strategy Lab UI — event_log is hidden by
                    # default, the user would otherwise see nothing.
                    lab_workspace.center_detail.show_compile_result(
                        False, "BACKTEST VALIDATION FAILED — " + "; ".join(errors)
                    )
                    return
                # Multi-symbol: chain one RunBacktest per symbol through the
                # existing worker; results merge into one aggregate at the end.
                if len(symbols) > 1:
                    multi = getattr(self, "_multi_symbol_coordinator", None)
                    if multi is not None:
                        from app.services.multi_symbol_backtest import BatchRequest

                        multi.start(  # type: ignore[attr-defined]
                            symbols,
                            BatchRequest(
                                strategy_id=sid,
                                timeframe=form.timeframe,
                                start_date=form.start_date,
                                end_date=form.end_date,
                                initial_capital=form.initial_capital,
                                slippage_pct=form.slippage_pct,
                                commission_pct=form.commission_pct,
                            ),
                        )
                        self._last_test_cfg = cfg
                        self._last_train_result = None
                        return
                request_id = uuid.uuid4().hex[:8]
                self._bus.publish(
                    RunBacktest(
                        request_id=request_id,
                        strategy_ids=(sid,),
                        symbol=symbol,  # type: ignore[arg-type]
                        timeframe=form.timeframe,
                        start_date=form.start_date,
                        end_date=form.end_date,
                        initial_capital=form.initial_capital,
                        slippage_pct=form.slippage_pct,
                        commission_pct=form.commission_pct,
                    )
                )
                # store test period for validation
                self._last_test_cfg = cfg
                self._last_train_result = None

            lab_workspace.run_backtest.connect(_on_workspace_run)

            lab_workspace.stop_requested.connect(lambda: self._on_lab_stop(event_log))

            # ── multi-symbol batch: surface per-symbol errors + bar windows ──
            _multi_lab = getattr(self, "_multi_symbol_coordinator", None)
            if _multi_lab is not None:

                def _on_batch_finished(outcome: Any) -> None:
                    # Single ranking refresh: the terminal BacktestCompleted
                    # (published right after) rebuilds it once via set_result.
                    # Merge is done — UI publication (finalizing) starts now.
                    with contextlib.suppress(Exception):
                        lab_workspace.set_run_state("finalizing")
                    with contextlib.suppress(Exception):
                        lab_workspace.set_symbol_windows(outcome.bars_by_symbol)
                    with contextlib.suppress(Exception):
                        lab_workspace.set_last_run_symbols(outcome.symbols, refresh=False)
                    with contextlib.suppress(Exception):
                        lab_workspace.set_ranking_errors(dict(outcome.errors), refresh=False)
                    if outcome.errors:
                        detail = "; ".join(f"{s}: {e}" for s, e in outcome.errors.items())
                        event_log.add_entry("WARN", f"Some symbols failed — {detail}")
                        lab_workspace.center_detail.show_compile_result(
                            True,
                            f"✓ {len(outcome.results)}/{len(outcome.symbols)} symbols — "
                            f"failed: {', '.join(outcome.errors)}",
                        )

                def _on_batch_failed(reason: str) -> None:
                    event_log.add_entry("ERROR", f"Multi-symbol backtest failed: {reason}")
                    lab_workspace.center_detail.show_compile_result(False, reason)
                    with contextlib.suppress(Exception):
                        lab_workspace.set_run_state("failed")

                _multi_lab.batch_finished.connect(_on_batch_finished)  # type: ignore[attr-defined]
                _multi_lab.batch_failed.connect(_on_batch_failed)  # type: ignore[attr-defined]

                def _on_batch_finalizing(_request_id: object) -> None:
                    with contextlib.suppress(Exception):
                        lab_workspace.set_run_state("aggregating")

                _multi_lab.batch_finalizing.connect(_on_batch_finalizing)  # type: ignore[attr-defined]

                def _on_batch_progress(payload: object) -> None:
                    try:
                        done, total = payload  # type: ignore[misc]
                    except Exception:  # noqa: BLE001
                        return
                    with contextlib.suppress(Exception):
                        lab_workspace.set_batch_progress(int(done), int(total))

                _multi_lab.batch_progress.connect(_on_batch_progress)  # type: ignore[attr-defined]

            # ── instant trade → chart (spec §5) ─────────────────
            # Bridge lab trade clicks → chart controller; chart reuse, cached, race-safe.
            # Keep legacy highlight for backward compat but primary path is controller.
            controller = getattr(self, "_trade_chart_controller", None)
            trade_panel = getattr(self, "_trade_context_panel", None)

            def _on_trade_focus_new(idx: int) -> None:
                # Update blotter selection state (professional subtle highlight)
                # Try to keep compare blotter in sync as well
                try:
                    if hasattr(lab_workspace, "journal") and hasattr(
                        lab_workspace.journal, "set_selected_index"
                    ):
                        lab_workspace.journal.set_selected_index(idx)  # type: ignore[attr-defined]
                    # also sync compare blotter if visible
                    with contextlib.suppress(Exception):
                        cmp_b = getattr(lab_workspace, "_compare_view", None)
                        if cmp_b is not None:
                            cmp_blotter = getattr(cmp_b, "_trade_blotter", None)
                            if cmp_blotter is not None and hasattr(
                                cmp_blotter, "set_selected_index"
                            ):
                                cmp_blotter.set_selected_index(idx)  # type: ignore[attr-defined]
                except Exception:
                    pass
                if controller is not None:
                    try:
                        # Resolve actual TradeRecord from visible blotter to handle filtered views
                        # (BUY/SELL filtered indices differ from controller's full list).
                        actual_trade = None
                        # Try journal first
                        with contextlib.suppress(Exception):
                            # Determine which blotter is currently visible/has the trade
                            candidates: list[Any] = []
                            _journal = getattr(lab_workspace, "journal", None)
                            _j_trades = getattr(_journal, "_trades", None)
                            if _j_trades and 0 <= idx < len(_j_trades):
                                candidates.append(_j_trades[idx])
                            # Compare blotter (holds combined trades in COMPARE)
                            _cmp_b = getattr(lab_workspace, "_compare_view", None)
                            _cmp_blotter = (
                                getattr(_cmp_b, "_trade_blotter", None)
                                if _cmp_b is not None
                                else None
                            )
                            _c_trades = getattr(_cmp_blotter, "_trades", None)
                            if _c_trades and 0 <= idx < len(_c_trades):
                                candidates.append(_c_trades[idx])
                            # Prefer trade that matches current view mode
                            if candidates:
                                # Filtered trade; map to full via entry_time
                                filtered = candidates[0]
                                # Try to find in controller's full list
                                found = None
                                for t in getattr(controller, "_current_trades", ()):
                                    if getattr(t, "entry_time", None) == getattr(
                                        filtered, "entry_time", None
                                    ) and getattr(t, "symbol", None) == getattr(
                                        filtered, "symbol", None
                                    ):
                                        found = t
                                        break
                                actual_trade = found if found is not None else filtered
                            else:
                                actual_trade = None
                        if actual_trade is not None and hasattr(
                            controller, "select_trade_by_record"
                        ):
                            ok = controller.select_trade_by_record(actual_trade)  # type: ignore[attr-defined]
                            if ok:
                                with contextlib.suppress(Exception):
                                    window.show_market()
                                    widget.update()
                                return
                        # Fallback to index-based (full list)
                        ok = controller.select_trade(idx)
                        if ok:
                            # bring chart into view instantly (persistent workspace)
                            with contextlib.suppress(Exception):
                                # slight delay ensures ChartReady/UI ready; instant for cached
                                window.show_market()
                                widget.update()
                        else:
                            trade_overlay.set_highlight(idx)
                            widget.update()
                        return
                    except Exception:
                        pass
                # fallback legacy
                trade_overlay.set_highlight(idx)
                try:
                    last = getattr(self, "_last_train_result", None)
                    if last is not None and hasattr(last, "trades") and 0 <= idx < len(last.trades):
                        _ = last.trades[idx].entry_index
                        widget.update()
                except Exception:
                    pass

            # wire single-click handler — avoid double emission (journal forwards to trade_focus)
            lab_workspace.trade_focus.connect(_on_trade_focus_new)
            # also handle performance panel trade selection
            with contextlib.suppress(Exception):
                performance_panel.trade_selected.connect(_on_trade_focus_new)  # type: ignore[attr-defined]
            # COMPARE mode has its own blotter not forwarded to trade_focus
            with contextlib.suppress(Exception):
                # compare trade blotter (visible in COMPARE)
                cmp_blotter = getattr(lab_workspace, "_compare_view", None)
                if cmp_blotter is not None:
                    cmp_blotter = getattr(cmp_blotter, "_trade_blotter", None)
                    if cmp_blotter is not None and hasattr(cmp_blotter, "trade_clicked"):
                        cmp_blotter.trade_clicked.connect(_on_trade_focus_new)  # type: ignore[attr-defined]
            # Also handle direct journal clicks that bypass trade_focus (defensive)
            # journal already forwards to trade_focus, but keep as fallback if forwarding disabled
            with contextlib.suppress(Exception):
                # Only connect if not already double (check receivers)
                # Use trade_focus as primary; journal direct is redundant
                pass

            # TradeContextPanel: prev/next + open in market (§29, §17)
            if trade_panel is not None and controller is not None:
                try:
                    trade_panel.prev_trade.connect(lambda: controller.select_prev())  # type: ignore[attr-defined]
                    trade_panel.next_trade.connect(lambda: controller.select_next())  # type: ignore[attr-defined]
                    trade_panel.open_in_market.connect(lambda: controller.open_in_market())  # type: ignore[attr-defined]

                    # keep blotter selection in sync when navigating from chart panel
                    # Map full index back to filtered view's row for correct highlight
                    def _sync_blotter_next() -> None:
                        with contextlib.suppress(Exception):
                            cur = getattr(controller, "_selected_index", None)
                            if cur is None:
                                return
                            # Find full trade
                            full_trades = getattr(controller, "_current_trades", ())
                            if not full_trades or cur < 0 or cur >= len(full_trades):
                                return
                            full_trade = full_trades[cur]
                            # Map to visible blotter's filtered index
                            # Try journal
                            try:
                                journal = getattr(lab_workspace, "journal", None)
                                if journal is not None and getattr(journal, "_trades", None):
                                    for fi, ft in enumerate(journal._trades):  # type: ignore[attr-defined]
                                        if getattr(ft, "entry_time", None) == getattr(
                                            full_trade, "entry_time", None
                                        ) and getattr(ft, "symbol", None) == getattr(
                                            full_trade, "symbol", None
                                        ):
                                            journal.set_selected_index(fi)  # type: ignore[attr-defined]
                                            break
                            except Exception:
                                pass
                            # Also sync compare blotter if visible
                            try:
                                cmp_b = getattr(lab_workspace, "_compare_view", None)
                                if cmp_b is not None:
                                    cmp_blotter = getattr(cmp_b, "_trade_blotter", None)
                                    if cmp_blotter is not None and getattr(
                                        cmp_blotter, "_trades", None
                                    ):
                                        for fi, ft in enumerate(cmp_blotter._trades):  # type: ignore[attr-defined]
                                            if getattr(ft, "entry_time", None) == getattr(
                                                full_trade, "entry_time", None
                                            ):
                                                cmp_blotter.set_selected_index(fi)  # type: ignore[attr-defined]
                                                break
                            except Exception:
                                pass

                    trade_panel.prev_trade.connect(_sync_blotter_next)  # type: ignore[attr-defined]
                    trade_panel.next_trade.connect(_sync_blotter_next)  # type: ignore[attr-defined]
                except Exception:
                    pass

            # Keyboard global for chart context: ←/→ navigates trades when focused (§28)
            # Installed on window so it works whether market or lab is active
            try:
                from PySide6.QtCore import Qt as _Qt
                from PySide6.QtGui import QAction as _QAct
                from PySide6.QtGui import QKeySequence as _QKS  # noqa: N814

                def _chart_prev_trade() -> None:
                    if (
                        controller is not None
                        and getattr(controller, "_selected_index", None) is not None
                    ):
                        controller.select_prev()

                def _chart_next_trade() -> None:
                    if (
                        controller is not None
                        and getattr(controller, "_selected_index", None) is not None
                    ):
                        controller.select_next()

                # Use Shortcut context WidgetWithChildrenShortcut on window for previous/next
                prev_action = _QAct(window)
                prev_action.setShortcut(_QKS(_Qt.Key.Key_Left))
                prev_action.setShortcutContext(_Qt.ShortcutContext.WidgetWithChildrenShortcut)
                prev_action.triggered.connect(_chart_prev_trade)  # type: ignore[arg-type]
                window.addAction(prev_action)

                next_action = _QAct(window)
                next_action.setShortcut(_QKS(_Qt.Key.Key_Right))
                next_action.setShortcutContext(_Qt.ShortcutContext.WidgetWithChildrenShortcut)
                next_action.triggered.connect(_chart_next_trade)  # type: ignore[arg-type]
                window.addAction(next_action)

                esc_action = _QAct(window)
                esc_action.setShortcut(_QKS(_Qt.Key.Key_Escape))
                esc_action.setShortcutContext(_Qt.ShortcutContext.WidgetWithChildrenShortcut)

                def _on_esc() -> None:
                    with contextlib.suppress(Exception):
                        if trade_panel is not None and trade_panel.isVisible():
                            # clear focused trade overlay, keep chart
                            if hasattr(controller, "clear"):
                                controller.clear()  # type: ignore[attr-defined]
                            trade_panel.clear()  # type: ignore[attr-defined]
                            widget.update()
                            # return focus to lab blotter
                            with contextlib.suppress(Exception):
                                window.show_lab()
                                if hasattr(lab_workspace, "journal"):
                                    lab_workspace.journal.setFocus()  # type: ignore[attr-defined]

                esc_action.triggered.connect(_on_esc)  # type: ignore[arg-type]
                window.addAction(esc_action)
            except Exception:
                pass

            # ── code editor SAVE / COMPILE ──
            # The strategy library is its own root (resolved once in __init__)
            # and is deliberately NOT the market-data directory.
            def _strategy_data_dir() -> str | None:
                return self._strategy_dir

            def _refresh_my_strategies() -> None:
                try:
                    from strategy.language.storage import list_strategies_with_mtime

                    items = list_strategies_with_mtime(_strategy_data_dir())
                    lab_workspace.left_nav.set_strategies(items)  # type: ignore[attr-defined]
                except Exception:
                    pass
                # Keep INDICATORS → STRATEGIES in sync (single control, same storage)
                try:
                    from strategy.language.storage import list_strategies

                    names = tuple(sorted(list_strategies(self._strategy_dir)))
                    window.set_indicator_strategies(names)
                except Exception:
                    pass

            def _refresh_indicator_strategies() -> None:
                try:
                    from strategy.language.storage import list_strategies

                    names = tuple(sorted(list_strategies(self._strategy_dir)))
                    window.set_indicator_strategies(names)
                except Exception:
                    pass

            _refresh_indicator_strategies()

            def _on_chart_indicator_strategy(name: str) -> None:
                try:
                    from strategy.language.storage import load_strategy_record

                    rec = load_strategy_record(name, self._strategy_dir)
                    if rec is None:
                        event_log.add_entry("WARN", f"Strategy not found: {name}")
                        return
                    strategy_id = rec.id
                    symbol = window.current_symbol
                    timeframe = window.current_timeframe
                    if symbol is None or timeframe is None:
                        event_log.add_entry("WARN", f"No chart loaded — cannot run {name}")
                        return
                    start_date = "2024-01-01"
                    end_date = "2026-12-31"
                    try:
                        model = getattr(window._widget, "_model", None)  # type: ignore[attr-defined]
                        if model is not None and getattr(model, "bars", None):
                            bars = model.bars  # type: ignore[attr-defined]
                            if bars:
                                start_date = bars[0].timestamp[:10]  # type: ignore[index]
                                end_date = bars[-1].timestamp[:10]  # type: ignore[index]
                    except Exception:
                        pass
                    import uuid

                    from backtest.events import RunBacktest

                    self._bus.publish(
                        RunBacktest(
                            request_id=uuid.uuid4().hex[:8],
                            strategy_ids=(strategy_id,),
                            symbol=symbol,  # type: ignore[arg-type]
                            timeframe=timeframe,
                            start_date=start_date,
                            end_date=end_date,
                        )
                    )
                    event_log.add_entry("INFO", f"Indicator strategy run: {name}")
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            window.indicators.strategy_selected.connect(_on_chart_indicator_strategy)  # type: ignore[attr-defined]

            def _on_save(code: str) -> None:
                try:
                    import re as _re

                    from strategy.language.storage import (
                        list_strategies,
                        save_strategy,
                    )

                    data_dir = _strategy_data_dir()
                    current_id = lab_workspace.current_tab_name().strip()  # type: ignore[attr-defined]
                    existing = set(list_strategies(data_dir))
                    is_update = bool(current_id) and current_id in existing

                    if is_update:
                        target_name = current_id
                    else:
                        match = _re.search(r'strategy\s*\(\s*["\']([^"\']+)["\']', code)
                        candidate = match.group(1).strip() if match else ""
                        if candidate and candidate not in existing:
                            target_name = candidate
                        elif candidate and candidate in existing:
                            # Dedup: candidate exists, append counter
                            base = candidate
                            counter = 2
                            target_name = f"{base} {counter}"
                            while target_name in existing:
                                counter += 1
                                target_name = f"{base} {counter}"
                        else:
                            # Use draft name with dedup if needed
                            base = current_id or "Untitled Strategy"
                            if not base.strip():
                                base = "Untitled Strategy"
                            target_name = base
                            if target_name in existing:
                                counter = 2
                                candidate_name = f"{base} {counter}"
                                while candidate_name in existing:
                                    counter += 1
                                    candidate_name = f"{base} {counter}"
                                target_name = candidate_name

                    save_strategy(code, target_name, data_dir)
                    # Phase 5: immutable version (canonical = StrategyRecord.id)  # noqa: E501
                    try:
                        import hashlib

                        from strategy.language import compile_strategy
                        from strategy.language.storage import load_strategy_record
                        from strategy.version import (
                            DuplicateVersionError,
                            create_version,
                            list_versions,
                        )

                        rec = load_strategy_record(target_name, data_dir)
                        strategy_id = rec.id if rec else target_name
                        # Determine parent version (latest) for linear history
                        parent_id = None
                        try:
                            versions = list_versions(strategy_id, data_dir)
                            if versions:
                                parent_id = versions[-1].version_id
                        except Exception:
                            pass
                        # Build Python snapshot + hash + params
                        ir_snapshot: str | None = None
                        ir_hash = ""
                        ir_version = 1
                        params: dict[str, float] = {}
                        try:
                            compiled = compile_strategy(code)
                            ir_snapshot = compiled.code
                            ir_hash = hashlib.sha256(compiled.code.encode("utf-8")).hexdigest()
                            params = dict(compiled.param_defaults)
                        except Exception:
                            ir_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
                        try:
                            create_version(
                                strategy_id,
                                code,
                                ir_version=ir_version,
                                ir_hash=ir_hash,
                                ir_snapshot=ir_snapshot,
                                parameters=params,
                                parent_version_id=parent_id,
                                data_dir=data_dir,
                                metadata={"name": target_name},
                            )
                        except DuplicateVersionError as dup:
                            event_log.add_entry(
                                "INFO",
                                f"No change — version {dup.existing_version_id} already has identical source",  # noqa: E501
                            )
                        except Exception as ve:
                            # Graph or other version error — surface but don't break save
                            event_log.add_entry("WARN", f"Version not created: {ve}")
                    except Exception:
                        pass
                    event_log.add_entry("SUCCESS", f"Strategy saved: {target_name}")
                    lab_workspace.center_detail.mark_saved()  # type: ignore[attr-defined]
                    if not is_update and target_name != current_id:
                        lab_workspace.center_detail.rename_buffer(  # type: ignore[attr-defined]
                            current_id, target_name
                        )
                    lab_workspace.set_strategy_name(target_name)
                    _refresh_my_strategies()
                    lab_workspace.left_nav.select_name(target_name)  # type: ignore[attr-defined]
                    lab_workspace.center_detail.show_compile_result(  # type: ignore[attr-defined]
                        True, "✓ Saved"
                    )
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", f"Save failed: {exc}")
                    lab_workspace.center_detail.show_compile_result(False, str(exc))  # type: ignore[attr-defined]  # noqa: E501

            def _on_compile(code: str):
                try:
                    from strategy.language import compile_strategy

                    compiled = compile_strategy(code)
                    self._compiled_strategy = compiled  # type: ignore[attr-defined]

                    def _factory(params):  # type: ignore[no-untyped-def]
                        return compiled.create_logic(params)

                    # Phase 3: generic — register under currently selected strategy's kind
                    sid = _current_strategy_id()
                    kind = sid.replace("-", "_")
                    target_kind = kind
                    # Ensure kind exists — register if new, otherwise overwrite factory for live update  # noqa: E501
                    if strategy_registry.has_kind(target_kind):
                        strategy_registry._kinds[target_kind] = _factory  # type: ignore[attr-defined]
                    else:
                        # Register new kind for this user strategy (generic)
                        try:
                            from strategy.models.parameters import ParameterSpec

                            # Prefer declared specs (unique keys, real min/max) —
                            # param_defaults would duplicate label+key rows.
                            declared = tuple(getattr(compiled, "param_specs", ()) or ())
                            if declared:
                                specs = declared
                            else:
                                specs = tuple(
                                    ParameterSpec(
                                        key=k,
                                        label=k,
                                        default=float(v),
                                        minimum=0,
                                        maximum=1e9,
                                        decimals=2,
                                    )
                                    for k, v in compiled.param_defaults.items()
                                )
                            strategy_registry.register_kind(target_kind, _factory, specs)
                            # ALWAYS register/update the definition — even if
                            # a previous compile registered one. Without this,
                            # a re-compiled strategy (new UUID or changed
                            # params) would keep the stale definition and
                            # RUN BACKTEST validation would reject it.
                            from strategy.models.definition import StrategyDefinition
                            from strategy.models.parameters import StrategyParameters

                            # Spec keys ONLY — raw param_defaults contains both
                            # label and key entries, which fails validated()
                            # (unknown parameters) and silently skips registration.
                            definition_params = StrategyParameters(
                                {
                                    spec.key: compiled.param_defaults[spec.key]
                                    for spec in specs
                                    if spec.key in compiled.param_defaults
                                }
                            )
                            definition = StrategyDefinition(
                                id=sid,
                                name=lab_workspace.current_tab_name().strip() or sid,  # type: ignore[attr-defined]
                                version="1.0",
                                kind=target_kind,
                                params=definition_params,
                            )
                            # Registration failure must surface, not vanish —
                            # an unregistered definition makes RUN BACKTEST
                            # fail validation forever with no visible reason.
                            try:
                                if strategy_registry.contains(sid):
                                    strategy_registry.set_params(sid, definition_params)
                                else:
                                    strategy_registry.register_definition(definition)
                            except Exception as reg_exc:
                                event_log.add_entry(
                                    "ERROR",
                                    f"Strategy registration failed: {reg_exc}",
                                )
                        except Exception:
                            strategy_registry._kinds[target_kind] = _factory  # type: ignore[attr-defined]
                    # also refresh params UI from compiled defaults
                    lab_workspace.center_detail._refresh_params_from_code(code)  # type: ignore[attr-defined]
                    # sync registry params to compiled defaults for current strategy
                    try:
                        from strategy.models.parameters import StrategyParameters

                        # Unique spec keys only (label keys would fail validation)
                        spec_keys = [s.key for s in getattr(compiled, "param_specs", ()) or ()]
                        if spec_keys:
                            new_params = {k: float(compiled.param_defaults[k]) for k in spec_keys}
                        else:
                            new_params = {k: float(v) for k, v in compiled.param_defaults.items()}
                        mapped = {}
                        for lk, lv in compiled.param_defaults.items():
                            lk_lower = lk.lower().replace(" ", "_").replace("-", "_")
                            if "c1" in lk_lower:
                                mapped["c1"] = lv
                            elif "c4" in lk_lower:
                                mapped["c4"] = lv
                            elif "rsi" in lk_lower:
                                mapped["rsi_threshold"] = lv
                            else:
                                mapped[lk] = lv
                        try:
                            strategy_registry.set_params(sid, StrategyParameters(mapped))
                        except Exception:
                            strategy_registry.set_params(sid, StrategyParameters(new_params))
                    except Exception:
                        pass
                    # Phase 3: show compile info (unique param count, warmup)
                    specs = tuple(getattr(compiled, "param_specs", ()) or ())
                    unique_keys = {getattr(s, "key", None) for s in specs} - {None}
                    param_count = len(unique_keys) if unique_keys else len(compiled.param_defaults)
                    try:
                        warmup = compiled.create_logic(
                            StrategyParameters(compiled.param_defaults)  # type: ignore[reportPossiblyUnboundVariable]
                        ).warmup()
                    except Exception:
                        warmup = None
                    ir = getattr(compiled, "ir", None)
                    if ir is not None:
                        msg = (
                            f"✓ COMPILED (IR v{ir.ir_version} · "
                            f"{len(ir.parameters)} params · {len(ir.statements)} stmts · "
                            f"{len(ir.data_requirements)} data req)"
                        )
                    elif warmup is not None:
                        msg = f"✓ COMPILED — {param_count} params · warmup {warmup} · Python"
                    else:
                        msg = f"Strategy compiled successfully ({param_count} params)"  # noqa: E501
                    lab_workspace.center_detail.show_compile_result(True, msg)
                    event_log.add_entry("SUCCESS", msg)
                except Exception as exc:  # noqa: BLE001
                    from strategy.language.compiler import StrategyLanguageError

                    if isinstance(exc, StrategyLanguageError) and exc.errors:
                        e = exc.errors[0]
                        if isinstance(e, str):
                            msg = e
                            lab_workspace.center_detail.show_compile_result(False, msg)
                            event_log.add_entry("ERROR", msg)
                        else:
                            msg = f"Line {e.line}, Col {e.col}: {e.message}"
                            if getattr(e, "hint", None):
                                msg += f" ({e.hint})"
                            lab_workspace.center_detail.show_compile_result(
                                False, msg, e.line, e.col
                            )
                            event_log.add_entry("ERROR", msg)
                    else:
                        msg = str(exc)
                        lab_workspace.center_detail.show_compile_result(False, msg)
                        event_log.add_entry("ERROR", f"Compile failed: {msg}")

            # wire editor toolbar buttons
            lab_workspace.center_detail.save_requested.connect(_on_save)  # type: ignore[attr-defined]
            lab_workspace.save_requested_relay.connect(_on_save)
            lab_workspace.center_detail.compile_requested.connect(_on_compile)  # type: ignore[attr-defined]
            lab_workspace.compile_requested_relay.connect(_on_compile)
            # initial compile at startup (only for non-empty buffers)
            try:
                initial_code = lab_workspace.center_detail.get_code()  # type: ignore[attr-defined]
                if initial_code.strip():
                    _on_compile(initial_code)
            except Exception:
                pass

            # new strategy = instant blank draft (no template injection)
            def _on_new_draft() -> None:
                names = set(lab_workspace.left_nav.names())
                base = "Untitled Strategy"
                name = base if base not in names else f"{base} {len(names) + 1}"
                lab_workspace.open_strategy(name, "")
                event_log.add_entry("INFO", f"New strategy draft: {name}")

            lab_workspace.left_nav.new_strategy_requested.connect(_on_new_draft)

            def _on_file_duplicate(name: str) -> None:
                try:
                    from strategy.language.storage import (
                        duplicate_strategy,
                        list_strategies_with_mtime,
                    )

                    copy_name = f"{name} Copy"
                    existing = {n for n, _ in list_strategies_with_mtime(_strategy_data_dir())}
                    counter = 2
                    while copy_name in existing:
                        copy_name = f"{name} Copy {counter}"
                        counter += 1
                    duplicate_strategy(name, copy_name, _strategy_data_dir())
                    event_log.add_entry("INFO", f"Strategy duplicated: {copy_name}")
                    _refresh_my_strategies()
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            def _on_file_rename(old: str, new: str) -> None:
                try:
                    from strategy.language.storage import rename_strategy

                    rename_strategy(old, new, _strategy_data_dir())
                    lab_workspace.center_detail.rename_buffer(old, new)  # type: ignore[attr-defined]
                    if lab_workspace.current_tab_name() == old:
                        lab_workspace.set_strategy_name(new)
                    event_log.add_entry("INFO", f"Strategy renamed: {old} → {new}")
                    _refresh_my_strategies()
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            def _on_file_delete(name: str) -> None:
                try:
                    from strategy.language.storage import delete_strategy

                    delete_strategy(name, _strategy_data_dir())
                    event_log.add_entry("INFO", f"Strategy deleted: {name}")
                    _refresh_my_strategies()
                    lab_workspace.select_next_after_delete(name)
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            lab_workspace.left_nav.duplicate_requested.connect(_on_file_duplicate)  # type: ignore[attr-defined]
            lab_workspace.left_nav.rename_requested.connect(_on_file_rename)  # type: ignore[attr-defined]
            lab_workspace.left_nav.delete_requested.connect(_on_file_delete)  # type: ignore[attr-defined]

            def _on_strategy_selected(name: str) -> None:
                try:
                    from strategy.language.storage import load_strategy, load_strategy_record

                    code = load_strategy(name, _strategy_data_dir())
                    if code is not None:
                        try:
                            rec = load_strategy_record(name, _strategy_data_dir())
                            meta: dict[str, str] | None = (
                                {"version": rec.version, "updated_at": rec.updated_at}
                                if rec is not None
                                else None
                            )
                        except Exception:  # noqa: BLE001
                            meta = None
                        lab_workspace.open_strategy(name, code, meta)
                        event_log.add_entry("INFO", f"Opened strategy: {name}")
                    else:
                        event_log.add_entry("WARN", f"Strategy not found: {name}")
                    # Lab → LIVE handoff: preselect the same strategy on the
                    # LIVE tab (never starts anything by itself).
                    with contextlib.suppress(Exception):
                        svc = getattr(self, "_live_service", None)
                        if svc is not None:
                            svc.configure(strategy_name=name)
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            lab_workspace.left_nav.strategy_selected.connect(_on_strategy_selected)  # type: ignore[attr-defined]
            lab_workspace.strategy_open_requested.connect(_on_strategy_selected)
            # initial populate
            _refresh_my_strategies()

        # ── bus → panels ──
        self._bus.subscribe(DataLoaded, lambda e: self._on_data_loaded(e, market_status, event_log))
        self._bus.subscribe(
            ChartReady, lambda e: self._on_chart_ready_lab(e, lab_control, trade_overlay, event_log)
        )
        self._bus.subscribe(TimeframesListed, lambda e: lab_control.set_timeframes(e.timeframes))
        self._bus.subscribe(RunBacktest, backtest_worker.on_run_backtest)
        self._bus.subscribe(
            BacktestStarted,
            lambda e: self._on_backtest_started(e, lab_control, event_log, system_health),
        )
        self._bus.subscribe(
            BacktestProgress, lambda e: self._on_backtest_progress(e, trade_overlay, widget)
        )
        self._bus.subscribe(
            BacktestCompleted,
            lambda e: self._on_backtest_completed(
                e, performance_panel, trade_overlay, lab_control, event_log, system_health, widget
            ),
        )
        self._bus.subscribe(
            BacktestFailed,
            lambda e: self._on_backtest_failed(e, lab_control, event_log, system_health),
        )
        self._bus.subscribe(
            PaperTradeRequested, lambda e: logger.info("Paper trade requested: %s", e.strategy_id)
        )
        self._bus.subscribe(
            LabReset,
            lambda _e: self._on_lab_reset(performance_panel, trade_overlay, event_log, widget),
        )

        # Qt signals → bus
        backtest_worker.started.connect(self._bus.publish)
        backtest_worker.progress.connect(self._bus.publish)
        backtest_worker.completed.connect(self._bus.publish)
        backtest_worker.failed.connect(self._bus.publish)

        # initial population (post-subscriptions so StrategiesListed handlers exist)
        _refresh_strategies()
        system_health.set_engine_state(
            "Strategy Engine", f"{len(strategy_registry.list())} strategies"
        )
        system_health.set_engine_state("Backtest Engine", "Idle")
        system_health.set_engine_state("Chart Engine", "--")
        system_health.set_engine_state("Data Engine", "--")

    def _create_strategy_from_draft(
        self, draft: Any, registry: Any, event_log: Any, refresh: Any
    ) -> None:  # type: ignore[no-untyped-def]
        from strategy.models.definition import StrategyDefinition
        from strategy.models.parameters import StrategyParameters

        base_id = draft.name.lower().replace(" ", "-").strip("-") or "strategy"
        candidate = base_id
        suffix = 0
        while registry.contains(candidate):
            suffix += 1
            candidate = f"{base_id}-{suffix}"
        try:
            definition = StrategyDefinition(
                id=candidate,
                name=draft.name,
                version=draft.version,
                kind=draft.kind,
                params=StrategyParameters(draft.params),
                allocation_pct=draft.allocation_pct,
            )
            registry.register_definition(definition)
        except Exception as exc:  # noqa: BLE001
            event_log.add_entry("ERROR", f"Validation failed: {exc}")
            return
        event_log.add_entry("INFO", f"Strategy created: {definition.label}")
        refresh()

    def _on_data_loaded(self, event: DataLoaded, market_status: Any, event_log: Any) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        market_status.set_data_status(
            last_update=event.bars[-1].timestamp[:16] if event.bars else "--",
            bars_loaded=len(event.bars),
        )
        event_log.add_entry("INFO", f"Data loaded: {event.symbol} ({len(event.bars)} bars)")

    def _on_chart_ready_lab(
        self, event: ChartReady, lab_control: Any, trade_overlay: Any, event_log: Any
    ) -> None:  # type: ignore[no-untyped-def]
        lab_control.select_timeframe(event.model.timeframe)
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.select_timeframe(event.model.timeframe)
        except Exception:
            pass
        trade_overlay.clear()
        try:
            if hasattr(self, "_plot_overlay") and self._plot_overlay is not None:
                self._plot_overlay.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        # Re-populate live strategy plots AFTER clear (TradingView: keep active indicators alive)
        # _recalc_active_indicators also runs but order is before this clear, so repopulate here.
        try:
            if (
                hasattr(self, "_widget")
                and self._widget is not None
                and hasattr(self, "_plot_overlay")
                and self._plot_overlay is not None
            ):
                try:
                    active_names = list(self._widget.visibility_panel.indicators)  # type: ignore[attr-defined]
                except Exception:
                    active_names = []
                for ind_name in active_names:
                    try:
                        self._run_strategy_plots(ind_name, event.model.bars)
                    except Exception:
                        continue
                if active_names:
                    with contextlib.suppress(Exception):
                        self._widget.update()  # type: ignore[attr-defined]
        except Exception:
            pass
        self._system_health.set_engine_state("Chart Engine", "Active")
        event_log.add_entry("INFO", f"Chart ready: {event.model.symbol} ({event.model.timeframe})")

    def _on_backtest_started(
        self, event: Any, lab_control: Any, event_log: Any, system_health: Any
    ) -> None:  # type: ignore[no-untyped-def]
        lab_control.set_busy(True)
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.set_busy(True)
        except Exception:
            pass
        event_log.add_entry("INFO", f"Backtest started: {event.symbol} {event.timeframe}")
        system_health.set_engine_state("Backtest Engine", "Running")

    def _on_backtest_progress(self, event: Any, trade_overlay: Any, widget: Any) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        trade_overlay.set_replay_index(event.processed)
        widget.update()

    def _on_backtest_completed(
        self,
        event: Any,
        performance_panel: Any,
        trade_overlay: Any,
        lab_control: Any,
        event_log: Any,
        system_health: Any,
        widget: Any,
    ) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        from backtest.models.result import BacktestResult

        result: BacktestResult = event.result  # type: ignore[assignment]
        lab_control.set_busy(False)
        # also update dedicated workspace if present
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.set_busy(False)
                if result.results:
                    self._lab_workspace.set_result(result.results[0])
                self._last_train_result = result.results[0] if result.results else None
        except Exception:
            pass
        if result.results:
            first = result.results[0]
            performance_panel.set_result(first)
            trade_overlay.set_result(first)
            # notify trade→chart controller (instant context, caching, race-safe)
            # Controller's set_result preserves the selected trade if it still exists
            # in the new result (e.g., automatic recalc after ChartReady), otherwise clears.
            try:
                ctrl = getattr(self, "_trade_chart_controller", None)
                if ctrl is not None and hasattr(ctrl, "set_result"):
                    ctrl.set_result(first, getattr(first, "config", None))  # type: ignore[attr-defined]
                else:
                    # No controller — clear stale context
                    if hasattr(trade_overlay, "clear_focused"):
                        trade_overlay.clear_focused()  # type: ignore[attr-defined]
                    panel = getattr(self, "_trade_context_panel", None)
                    if panel is not None and hasattr(panel, "clear"):
                        panel.clear()  # type: ignore[attr-defined]
            except Exception:
                pass
            # chart plots — generic, no OBR-specific
            # Avoid stale overwrite: if backtest symbol/timeframe mismatches current chart,
            # skip plot overlay (live plots via _run_strategy_plots/_on_chart_ready_lab are correct)
            try:
                if hasattr(self, "_plot_overlay") and self._plot_overlay is not None:
                    # gate on current chart model to prevent stale backtest (e.g., quick TF switch)
                    try:
                        cur = getattr(self, "_widget", None)
                        cur_model = getattr(cur, "_model", None) if cur is not None else None
                        if cur_model is not None and (
                            getattr(first.config, "symbol", None)
                            != getattr(cur_model, "symbol", None)
                            or getattr(first.config, "timeframe", None)
                            != getattr(cur_model, "timeframe", None)
                        ):
                            raise ValueError("stale backtest")
                    except ValueError:
                        raise
                    except Exception:
                        pass
                    cs = getattr(first, "chart_series", ())
                    self._plot_overlay.set_from_chart_series(cs)  # type: ignore[attr-defined]
                    # Universal plots share one contract live/backtest/replay.
                    try:
                        plots = getattr(first, "chart_plots", ())
                        if plots:
                            self._plot_overlay.ingest_plot_events(plots)  # type: ignore[attr-defined]
                    except Exception:
                        pass
            except Exception:
                pass
            widget.update()
            event_log.add_entry(
                "SUCCESS",
                f"Backtest completed: {first.name} — {len(first.trades)} trades, net ₹{first.metrics.net_profit:+,.0f}",  # noqa: E501  # noqa: E501
            )
            # Phase 5: immutable execution history saves off the UI thread —
            # the 193k-trade JSON build must never block result display.
            self._save_execution_history_async(result, event_log)
        system_health.set_engine_state("Backtest Engine", "Idle")

    def _save_execution_history_async(self, result: Any, event_log: Any) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        """Persist execution history in the background (UI stays responsive).

        Same file, same log lines, same replay id — only the thread changes.
        A generation token drops stale completions (reset/rerun mid-save).
        """
        self._history_gen = getattr(self, "_history_gen", 0) + 1
        generation = self._history_gen
        emitter = _HistorySaved()
        # Keep emitter + task alive until the slot runs.
        pending = getattr(self, "_history_pending", None)
        if not isinstance(pending, list):
            pending = []
            self._history_pending = pending  # type: ignore[attr-defined]
        task = _HistorySaveTask(
            result,
            getattr(self, "_strategy_registry", None),
            self._strategy_dir,
            generation,
            emitter,
        )
        pending.append((emitter, task))

        def _done(payload: object) -> None:
            try:
                gen, execution_id, lines = payload  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                return
            with contextlib.suppress(ValueError):
                pending.remove((emitter, task))
            if gen != getattr(self, "_history_gen", 0):
                return  # stale (reset/rerun mid-save) — file on disk stays valid
            for level, text in lines or ():
                with contextlib.suppress(Exception):
                    event_log.add_entry(level, text)
            if execution_id is not None:
                try:
                    if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                        # Store last execution for replay button
                        self._last_execution_id = execution_id  # type: ignore[attr-defined]
                        self._last_execution_ir = None  # type: ignore[attr-defined]  # noqa: F821
                except Exception:  # noqa: BLE001
                    pass

        with contextlib.suppress(Exception):
            emitter.finished.connect(_done)  # type: ignore[attr-defined]
        QThreadPool.globalInstance().start(task)

    def _on_lab_stop(self, event_log: Any) -> None:  # type: ignore[no-untyped-def]
        """STOP a running batch; restore idle UI, keep completed results.

        STOP governs batches only (single runs have no safe cancel and never
        arm STOP). Drops in-flight work, restores idle UI, keeps previous
        completed results intact.
        """
        lab_workspace = getattr(self, "_lab_workspace", None)
        multi = getattr(self, "_multi_symbol_coordinator", None)
        if multi is not None and not multi.active:  # type: ignore[attr-defined]
            return
        try:
            if multi is not None:
                multi.cancel()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        if lab_workspace is None:
            return
        with contextlib.suppress(Exception):
            lab_workspace.right_settings.set_busy(False)  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            lab_workspace.set_run_state("ready")  # type: ignore[attr-defined]
        event_log.add_entry("INFO", "Backtest stopped by user")

    def _on_backtest_failed(
        self, event: Any, lab_control: Any, event_log: Any, system_health: Any
    ) -> None:  # type: ignore[no-untyped-def]
        lab_control.set_busy(False)
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.set_busy(False)
        except Exception:
            pass
        event_log.add_entry("ERROR", f"Backtest failed: {event.reason}")
        system_health.set_engine_state("Backtest Engine", "Idle")
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.set_run_state("failed")
        except Exception:
            pass

    def _run_strategy_plots(self, strategy_name: str, bars: tuple) -> None:
        """Compile the strategy and run it on the given bars to populate plot series
        on the chart overlay. This is called when an indicator is added or when
        chart data changes (symbol/timeframe), ensuring live plots are always fresh."""
        from logging import getLogger

        _log = getLogger(__name__)
        _log.info("_run_strategy_plots: %s (%d bars)", strategy_name, len(bars))
        try:
            from backtest.models.result import ChartSeries
            from strategy.language import compile_strategy
            from strategy.language.storage import load_strategy_record
            from strategy.models.parameters import StrategyParameters
            from strategy.runtime import StrategyRuntime

            rec = load_strategy_record(strategy_name, self._strategy_dir)
            if rec is None:
                _log.warning("_run_strategy_plots: no record for %s", strategy_name)
                return
            compiled = compile_strategy(rec.code)
            logic = compiled.create_logic(StrategyParameters(compiled.param_defaults))
            logic._owner_id = strategy_name  # type: ignore[attr-defined]

            runtime = StrategyRuntime(logic, StrategyParameters(compiled.param_defaults))
            runtime.run(bars)

            raw = logic.get_chart_series_with_owner()  # type: ignore[attr-defined]
            _log.info(
                "_run_strategy_plots: %s -> %d series, raw keys: %s",
                strategy_name,
                len(raw),
                list(raw.keys())[:4],
            )
            # NOTE: no early return on empty `raw`. Strategies like OBR emit
            # ONLY universal PlotEvents (plot_ray/plot_marker — REF HIGH/LOW,
            # BUY/SELL/EOD/NO-TRADE) and no plot() series at all; returning
            # here would silently drop every live OBR plot from the chart.
            # Fall through: series (if any) then the universal plot events.

            chart_series_list = []
            for (owner, title), vals in raw.items():
                pts = tuple((int(k), float(v)) for k, v in sorted(vals.items()) if v is not None)
                if not pts:
                    continue
                chart_series_list.append(
                    ChartSeries(
                        title=title,
                        values=pts,
                        style="line",
                        extend="session",
                        strategy=str(owner),
                    )
                )

            if chart_series_list and hasattr(self, "_plot_overlay") and self._plot_overlay:
                self._plot_overlay.add_from_chart_series(tuple(chart_series_list))
                _log.info(
                    "_run_strategy_plots: added %d chart series to overlay", len(chart_series_list)
                )
            # Universal strategy-owned plots (same contract as backtest path).
            try:
                getter = getattr(logic, "get_plot_events", None)
                plots = getter() if callable(getter) else ()
                if plots and hasattr(self, "_plot_overlay") and self._plot_overlay:
                    self._plot_overlay.ingest_plot_events(tuple(plots))  # type: ignore[attr-defined]
            except Exception:
                pass
        except Exception:
            _log.exception("_run_strategy_plots(%s) failed", strategy_name)

    def _on_lab_reset(
        self, performance_panel: Any, trade_overlay: Any, event_log: Any, widget: Any
    ) -> None:  # type: ignore[no-untyped-def]
        performance_panel.clear()
        trade_overlay.clear()
        try:
            if hasattr(self, "_plot_overlay") and self._plot_overlay is not None:
                self._plot_overlay.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            ctrl = getattr(self, "_trade_chart_controller", None)
            if ctrl is not None and hasattr(ctrl, "clear"):
                ctrl.clear()  # type: ignore[attr-defined]
            multi = getattr(self, "_multi_symbol_coordinator", None)
            if multi is not None:
                multi.cancel()  # type: ignore[attr-defined]
            panel = getattr(self, "_trade_context_panel", None)
            if panel is not None and hasattr(panel, "clear"):
                panel.clear()  # type: ignore[attr-defined]
            # also clear focused overlay if any
            if hasattr(trade_overlay, "clear_focused"):
                trade_overlay.clear_focused()  # type: ignore[attr-defined]
        except Exception:
            pass
        widget.update()
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.clear()
        except Exception:
            pass
        event_log.add_entry("INFO", "Lab reset")

    def start(self) -> None:
        """Show the main window and publish AppStarted; the flow continues event-driven."""
        data_window: HistoricalDownloadPanel = self._services.get("data_window")
        data_engine: HistoricalDownloadEngine = self._services.get("data_engine")
        ready, reason = data_engine.provider_available()
        data_window.set_provider(ready, reason)
        # data-status provider row
        provider_label = "Zerodha" if ready else "Not Configured"
        self._market_status.set_data_status(provider=provider_label, latency="--")
        self._system_health.set_engine_state("Data Engine", "Ready" if ready else "Unavailable")
        if not ready and reason:
            self._event_log.add_entry("WARN", f"Data provider: {reason}")
        # Startup policy: ALWAYS open maximized (native work-area maximize —
        # title bar + min/max/close preserved, DPI/taskbar aware, no hardcoded
        # dimensions). This runs BEFORE AppStarted so the layout calculates
        # against the final maximized space. No geometry is ever restored.
        self._services.get("chart_window").showMaximized()
        self._event_log.add_entry("INFO", "VAYREN started — Strategy Lab ready")
        self._bus.publish(AppStarted())

    def stop(self) -> None:
        """Shut down every worker thread this Bootstrap owns and stop the bus.

        Bootstrap has no other exit path: without this, each constructed
        instance leaks its QThread workers and Qt widget tree, which
        accumulates without bound in long-lived processes (notably the test
        suite, where GC is intentionally disabled). Idempotent — safe to call
        more than once, and safe to call on a Bootstrap that never started.

        Order matters: detach the bus first so a worker cannot enqueue new
        work while it is being joined, then join workers, then close windows.
        """
        worker_names = ("data_worker", "backtest_worker")
        stopped: list[str] = []
        for name in worker_names:
            if name not in self._services:
                continue
            worker = self._services.get(name)
            shutdown = getattr(worker, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    logger.exception("Failed to shut down %s", name)
                stopped.append(name)
        # BrokerManager owns its own worker thread, outside the services
        # registry; its stop_worker() is idempotent.
        broker_manager = getattr(self, "_broker_manager", None)
        stop_worker = getattr(broker_manager, "stop_worker", None)
        if callable(stop_worker):
            try:
                stop_worker()
            except Exception:
                logger.exception("Failed to shut down the broker manager worker")
            stopped.append("broker_manager")
        window = self._services.get("chart_window") if "chart_window" in self._services else None
        if window is not None:
            try:
                window.close()
            except Exception:
                logger.exception("Failed to close the chart window")
        self._bus.clear()
        # Release this Bootstrap's own strong references to the UI tree. Several
        # panels are constructed with no parent and only held by ``self._*`` and
        # by the services registry; while those references live, the panels stay
        # alive as top-level widgets and are never garbage-collected. The suite
        # runs with GC disabled (``conftest``), so nothing else can break the
        # cycle — this is the only place that can.
        self._release_widget_references()
        # ``close()`` schedules the window tree for deletion; flush the deferred
        # deletions now so the widgets are actually released rather than
        # lingering until the next event-loop turn (which may never come).
        app = QApplication.instance()
        if isinstance(app, QApplication):
            with contextlib.suppress(Exception):
                app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if stopped:
            logger.info("Bootstrap stopped; workers shut down: %s", ", ".join(stopped))

    def _release_widget_references(self) -> None:
        """Drop strong references to widgets so their trees can be reclaimed.

        Idempotent and defensive: every attribute is optional and any failure
        is swallowed, because this runs during teardown where raising would be
        worse than leaking.
        """
        from PySide6.QtWidgets import QWidget

        seen: set[int] = set()

        def _consider(value: Any) -> None:
            if value is None or id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, QWidget):
                with contextlib.suppress(Exception):
                    value.setParent(None)
                with contextlib.suppress(Exception):
                    value.deleteLater()

        # Service registry entries (the primary holder of the orphan panels).
        for _name, service in list(self._services):
            if isinstance(service, QWidget):
                _consider(service)
        # Instance attributes (``self._lab_control`` and friends).
        for attr in (
            "_lab_control",
            "_lab_list",
            "_performance_panel",
            "_lab_workspace",
            "_market_status",
            "_event_log",
            "_system_health",
            "_nav_bar",
            "_widget",
            "_trade_context_panel",
            "_trade_chart_controller",
            "_multi_symbol_coordinator",
            "_plot_overlay",
            "_trade_overlay",
        ):
            if not hasattr(self, attr):
                continue
            _consider(getattr(self, attr))
            with contextlib.suppress(Exception):
                setattr(self, attr, None)
        self._drop_gui_service_entries()

    def _drop_gui_service_entries(self) -> None:
        """Remove widget entries from the services registry (keeps non-widgets).

        ``Registry`` has no ``clear`` and refuses duplicate ``register`` calls,
        so the underlying dict is rebuilt in place rather than re-registered.
        """
        from PySide6.QtWidgets import QWidget

        items = getattr(self._services, "_items", None)
        if not isinstance(items, dict):
            return
        for name in [n for n, v in items.items() if isinstance(v, QWidget)]:
            items.pop(name, None)

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def services(self) -> Registry[Any]:
        return self._services

    @property
    def components(self) -> ComponentRegistry:
        """The registered components with their capabilities (new lookup)."""
        return self._components

    @property
    def system_model(self) -> SystemModel:
        """The in-memory architecture model of the running system."""
        return self._system_model
