"""LiveTradingService — Strategy Lab → LIVE bridge (00_app composition).

Owns UI-driven live/paper trading over the EXISTING execution stack, one
:class:`LiveSession` per symbol (independent OBR state per stock by
construction). The strategy is NEVER copied: the same
``StrategyRecord.code`` that Strategy Lab compiles is compiled here and
driven through the same ``LiveStrategyDriver.on_candle → logic.on_bar``
path the backtest uses (``execute_bars`` calls the identical method).

Safety posture (mirrors the repo's fail-closed layers):
- PAPER is the default; LIVE needs explicit confirmation + session arming.
- START validates everything first and returns exact blockers; the UI
  disables START while any blocker exists.
- STOP halts ticking immediately (no new signals), disarms, stops every
  session, then persists. Already-submitted orders stay tracked in the
  persisted engine snapshot and reconcile before any restart.
- PAPER restarts are clean (paper venues are in-memory; DB history rebuilds
  windows). LIVE checkpoints are NEVER auto-dropped — a mismatch blocks
  with reasons until the operator resolves it.
- Secrets are never touched here (broker resolution stays inside the
  session); nothing credential-like is logged or exposed in snapshots.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from execution import (
    ExecutionMode,
    LiveSession,
    SessionConfig,
    SqliteTailProvider,
)
from execution.modes import LiveArm
from market import Bar, SymbolRepository
from PySide6.QtCore import QObject, QTimer, Signal
from risk import RiskPolicy
from strategy import StrategyDefinition, StrategyParameters
from strategy.language.compiler import compile_strategy
from strategy.language.storage import (
    list_strategies,
    load_strategy_record,
    strategy_dir as resolve_strategy_dir,
)

# Backward-compatible default: the shared resolver picks env var → data_dir →
# per-user folder. Kept as a module constant because callers and the public
# ``__all__`` reference it; it is no longer a hardcoded drive letter.
DEFAULT_STRATEGY_DIR = resolve_strategy_dir(None)
_WARMUP_BARS = 120
_ACTIVITY_CAP = 500
_TICK_MS = 1000


class LiveConfigError(RuntimeError):
    """START rejected — UI shows the reasons, nothing was started."""


@dataclass
class LiveTradeConfig:
    """UI-owned session setup. Plain data; restored on reopen, never auto-started."""

    strategy_name: str = ""
    symbols: tuple[str, ...] = ()
    timeframe: str = ""
    mode: str = "PAPER"
    quantity: float = 1.0
    capital: float = 1_000_000.0


class LiveSessionStore:
    """Atomic JSON persistence for live config + checkpoints (no secrets)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self._path)


def _utcnow_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()


class LiveTradingService(QObject):
    """Owns UI-driven sessions. Pure composition — no strategy math here."""

    state_changed = Signal()

    def __init__(
        self,
        data_dir: str | Path,
        strategy_dir: str | Path | None = None,
        selection_service: Any | None = None,
        broker_manager: Any | None = None,
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._data_dir = Path(data_dir)
        self._strategy_dir = Path(strategy_dir) if strategy_dir else Path(DEFAULT_STRATEGY_DIR)
        self._selection = selection_service
        self._broker_manager = broker_manager
        self._repository = SymbolRepository(self._data_dir)
        self._store = LiveSessionStore(self._data_dir / "live" / "live_session.json")
        self._config = LiveTradeConfig()
        self._sessions: dict[str, LiveSession] = {}
        self._session_dirs: dict[str, Path] = {}
        self._journal_cursors: dict[str, int] = {}
        self._activity: deque[dict[str, Any]] = deque(maxlen=_ACTIVITY_CAP)
        self._marks: dict[str, float] = {}
        self._open_since: dict[str, str] = {}
        self._last_signal: dict[str, str] = {}
        self._status = "STOPPED"
        self._status_reason = ""
        self._confirmed_live_at = ""
        self._strategy_version = ""
        self._feed_kind = "none"
        self._account_state: dict[str, Any] = {"ready": False, "reason": "session not running"}
        self._live_venue_id = ""
        self._validate_key: tuple | None = None
        self._validate_cache: tuple[str, ...] | None = None
        self._chart_cache_key: tuple | None = None
        self._chart_cache: tuple[Bar, ...] | None = None
        self._compiled_cache: dict[str, tuple[str, Any]] = {}
        self._tick_count = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self.tick)
        self._restore_config()

    # ── catalog (Strategy Lab store + watchlist universe, never copied) ──

    def available_strategies(self) -> tuple[str, ...]:
        try:
            return tuple(sorted(list_strategies(self._strategy_dir)))
        except Exception:
            return ()

    def available_symbols(self) -> tuple[str, ...]:
        try:
            return tuple(self._repository.list_symbols())
        except Exception:
            return ()

    def available_timeframes(self, symbol: str) -> tuple[str, ...]:
        try:
            return tuple(self._repository.detect_timeframes(symbol))
        except Exception:
            return ()

    # ── configuration (UI writes here; nothing starts implicitly) ──────

    @property
    def config(self) -> LiveTradeConfig:
        return self._config

    @property
    def status(self) -> str:
        return self._status

    def configure(
        self,
        strategy_name: str | None = None,
        symbols: tuple[str, ...] | None = None,
        timeframe: str | None = None,
        mode: str | None = None,
        quantity: float | None = None,
    ) -> None:
        if self._status == "RUNNING":
            return  # setup is frozen while a session runs
        if strategy_name is not None:
            self._config.strategy_name = str(strategy_name)
        if symbols is not None:
            self._config.symbols = tuple(symbols)
        if timeframe is not None:
            self._config.timeframe = str(timeframe)
        if mode is not None and mode in ("PAPER", "LIVE"):
            if mode != self._config.mode:
                self._confirmed_live_at = ""
            self._config.mode = mode
        if quantity is not None:
            with contextlib.suppress(TypeError, ValueError):
                self._config.quantity = max(0.0, float(quantity))
        self.invalidate_validation_cache()
        self._chart_cache_key = None
        self._chart_cache = None
        self._persist_config()
        self.state_changed.emit()

    def broker_name(self) -> str:
        """User-selected broker (display only; venue resolves inside sessions)."""
        selection = None
        try:
            current = getattr(self._selection, "current", None)
            if callable(current):
                selection = current()
            else:
                getter = getattr(self._selection, "current_or_none", None)
                if callable(getter):
                    selection = getter()
        except Exception:
            selection = None
        name = str(getattr(selection, "name", "") or "") if selection is not None else ""
        return name or "paper"

    # ── validation (START gate; exact reasons, never raises) ────────────

    def validate(self) -> tuple[str, ...]:
        """Blockers for START. Empty = ready (LIVE still needs confirmation).

        Cached on the config tuple: the UI polls this every second while
        idle, but a re-check is only needed when the setup actually
        changes (or after a run). ``force=True`` bypasses the cache.
        """
        key = (
            self._config.strategy_name,
            self._config.symbols,
            self._config.timeframe,
            self._config.mode,
            self._config.quantity,
        )
        if key == self._validate_key and self._validate_cache is not None:
            return self._validate_cache
        blockers = self._validate_uncached()
        self._validate_key = key
        self._validate_cache = blockers
        return blockers

    def _validate_uncached(self) -> tuple[str, ...]:
        blockers: list[str] = []
        record = None
        if not self._config.strategy_name:
            blockers.append("no strategy selected")
        else:
            try:
                record = load_strategy_record(self._config.strategy_name, self._strategy_dir)
            except Exception as exc:
                record = None
                blockers.append(f"strategy load failed: {exc}")
            if record is None:
                blockers.append(f"strategy not found: {self._config.strategy_name}")
            else:
                try:
                    self._compiled(record)
                except Exception as exc:
                    blockers.append(f"strategy does not compile: {exc}")
        if not self._config.symbols:
            blockers.append("no symbols selected (pick from Market Watchlist)")
        else:
            try:
                universe = set(self._repository.list_symbols())
            except Exception as exc:
                universe = set()
                blockers.append(f"market store unreadable: {exc}")
            for symbol in self._config.symbols:
                if symbol not in universe:
                    blockers.append(f"invalid symbol: {symbol}")
        if not self._config.timeframe:
            blockers.append("no timeframe selected")
        if self._config.quantity <= 0:
            blockers.append("quantity must be positive")
        if record is not None and self._config.symbols and self._config.timeframe:
            for symbol in self._config.symbols:
                try:
                    frames = self._repository.detect_timeframes(symbol)
                except Exception:
                    frames = ()
                if frames and self._config.timeframe not in frames:
                    blockers.append(
                        f"timeframe {self._config.timeframe} not available for {symbol}"
                    )
                try:
                    bars = self._repository.get_candles_timeframe(symbol, self._config.timeframe, 1)
                except Exception as exc:
                    bars = []
                    blockers.append(f"market data unreadable for {symbol}: {exc}")
                if not bars:
                    blockers.append(f"no market data for {symbol} {self._config.timeframe}")
        if self._config.mode == "LIVE":
            blockers.extend(self._live_blockers())
        return tuple(blockers)

    def invalidate_validation_cache(self) -> None:
        """Force the next validate() to re-check (after venue/run changes)."""
        self._validate_key = None
        self._validate_cache = None

    def _live_blockers(self) -> list[str]:
        """Venue + gate truth for real-money mode (fail-closed, exact reasons)."""
        blockers: list[str] = []
        self._ensure_live_venues()
        name = self.broker_name()
        if name in ("", "paper"):
            blockers.append("no live broker selected (configure in SYSTEM → BROKERS)")
            return blockers
        # Centralized broker state: SYSTEM → BROKERS owns authentication;
        # LIVE only consumes it. LOGIN_REQUIRED/ERROR here means NO start.
        manager = self._broker_manager
        if manager is not None:
            with contextlib.suppress(Exception):
                state = manager.state(name) or {}
                status = str(getattr(state.get("status"), "value", "") or "")
                if status and status not in ("CONNECTED", "LIVE_READY"):
                    blockers.append(
                        f"{name} is not authenticated (broker status: {status}) — "
                        "open SYSTEM → BROKERS to log in"
                    )
        venue_id = self._resolve_live_venue_id(name)
        if venue_id is None:
            blockers.append(
                f"no live trading venue for broker {name!r} "
                "(broker is data-only or live activation incomplete)"
            )
            return blockers
        try:
            from execution.modes import gates_from_env

            missing = gates_from_env().missing()
        except Exception:
            missing = ("LIVE_GATES_UNREADABLE",)
        blockers.extend(f"live gate off: {gate}" for gate in missing)
        if not self._confirmed_live_at:
            blockers.append("live confirmation required (explicit operator consent)")
        return blockers

    @staticmethod
    def _ensure_live_venues() -> None:
        """Best-effort explicit activation (idempotent, no network, no orders).

        Venue registration lives in the data provider factory (the only
        layer allowed to reference concrete venues); unconfigured venues
        simply stay unregistered (fail-closed).
        """
        with contextlib.suppress(Exception):
            from data.provider.factory import ensure_live_venues

            ensure_live_venues()

    @staticmethod
    def _resolve_live_venue_id(name: str) -> str | None:
        """Registered venue id serving TRADING for a broker selection."""
        try:
            from broker.registry import default_registry
            from broker.vocab import Domain
        except ImportError:
            return None
        for candidate in (f"{name}-live", name):
            try:
                record = default_registry().get(candidate)
            except Exception:
                continue
            try:
                record.plugin.face(Domain.TRADING)
            except Exception:
                continue
            return candidate
        return None

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self, confirmed: bool = False) -> tuple[bool, tuple[str, ...]]:
        """Validate → build per-symbol sessions → RUNNING. Never auto-starts.

        Validation is forced fresh at START (config may have gone stale
        since the last UI poll); tick cadences reset for a clean cycle.
        """
        if self._status == "RUNNING":
            return False, ("already running",)
        self.invalidate_validation_cache()
        blockers = self.validate()
        if self._config.mode == "LIVE" and not confirmed:
            blockers = (*blockers, "live confirmation required (explicit operator consent)")
        if blockers:
            self._status = "STOPPED"
            self._status_reason = "; ".join(blockers)
            self.state_changed.emit()
            return False, blockers
        if self._config.mode == "LIVE" and confirmed:
            self._confirmed_live_at = _utcnow_iso()
        try:
            self._build_sessions()
        except LiveConfigError as exc:
            self._teardown_sessions()
            self._status = "ERROR"
            self._status_reason = str(exc)
            self.state_changed.emit()
            return False, (str(exc),)
        self._status = "RUNNING"
        self._status_reason = ""
        self._timer.start()
        self._record_activity("session", "", "START", "ok")
        self._persist()
        self.state_changed.emit()
        return True, ()

    def stop(self, reason: str = "operator stop") -> None:
        """Halt immediately: no new signals; open orders stay tracked."""
        self._timer.stop()
        for session in self._sessions.values():
            with contextlib.suppress(Exception):
                session.disarm(f"stop: {reason}")
            with contextlib.suppress(Exception):
                session.stop()
        self._drain_activity()
        self._status = "STOPPED"
        self._status_reason = reason if reason != "operator stop" else ""
        self._tick_count = 0
        self._persist()
        self._record_activity("session", "", "STOP", "info")
        self.state_changed.emit()

    def tick(self) -> None:
        """One polling step across sessions (QTimer; never raises).

        Marks are refreshed from the session's own provider (no extra DB
        reads); the activity drain and persistence stay on their own
        cadences so the per-tick cost is one cheap poll per session.
        """
        if self._status != "RUNNING":
            return
        now = time.time()
        try:
            for symbol, session in self._sessions.items():
                with contextlib.suppress(Exception):
                    session.step(now)
                with contextlib.suppress(Exception):
                    self._refresh_mark(symbol)
            self._tick_count += 1
            if self._tick_count % 2 == 0:
                self._drain_activity()
            if self._tick_count % 30 == 0:
                self._persist()
        except Exception as exc:
            self._status = "ERROR"
            self._status_reason = str(exc) or "tick failed"
        self.state_changed.emit()

    # ── snapshot (the ONLY data the LIVE tab reads) ─────────────────────

    def snapshot(self) -> dict[str, Any]:
        blockers = self.validate() if self._status != "RUNNING" else ()
        broker_name = self.broker_name()
        connected = False
        broker_reason = "not started"
        positions: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        realized = 0.0
        unrealized = 0.0
        wins = 0
        losses = 0
        open_count = 0
        fill_count = 0
        recon_status = "NOT CONFIGURED"
        recon_blocks = False
        kill_halted = False
        lifecycle = "STOPPED"
        strategy_state: dict[str, Any] | None = None
        if self._sessions:
            first_key = next(iter(self._sessions))
            first = self._sessions[first_key]
            with contextlib.suppress(Exception):
                broker = getattr(first, "_broker", None)
                if broker is not None:
                    connected, broker_reason = broker.health()
            with contextlib.suppress(Exception):
                kill_halted = bool(first._risk.kill_switch.is_halted())
            with contextlib.suppress(Exception):
                lifecycle = first._lifecycle.state.value
            for symbol, session in self._sessions.items():
                mark = self._marks.get(symbol, 0.0)
                with contextlib.suppress(Exception):
                    for pos in session.ledger.all_positions():
                        px = mark or pos.avg_price
                        pnl = pos.unrealized(px) if mark else 0.0
                        unrealized += pnl
                        realized += pos.realized_pnl
                        if pnl >= 0:
                            wins += 1
                        else:
                            losses += 1
                        positions.append(
                            {
                                "symbol": pos.symbol,
                                "side": "LONG" if pos.quantity > 0 else "SHORT",
                                "quantity": abs(pos.quantity),
                                "entry_price": pos.avg_price,
                                "current_price": px,
                                "pnl": pnl + pos.realized_pnl,
                                "status": "OPEN",
                                "entry_time": self._open_since.get(symbol, ""),
                            }
                        )
                with contextlib.suppress(Exception):
                    snap = session.engine.snapshot()
                    for item in snap.get("orders", ()):
                        orders.append(
                            {
                                "order_id": str(item.get("client_order_id", "")),
                                "strategy": self._config.strategy_name,
                                "symbol": str(item.get("symbol", "")),
                                "side": str(item.get("side", "")),
                                "quantity": float(item.get("quantity", 0.0) or 0.0),
                                "type": str(item.get("order_type", "")),
                                "price": float(item.get("avg_fill_price") or 0.0),
                                "status": str(item.get("state", "")),
                                "time": self._history_time(item),
                                "broker": broker_name,
                            }
                        )
                    open_count += len(session.engine.open_orders())
                with contextlib.suppress(Exception):
                    broker = getattr(session, "_broker", None)
                    for fill in tuple(getattr(broker, "fills", ()) or ()):
                        fill_count += 1
                        fills.append(
                            {
                                "time": str(getattr(fill, "timestamp", "")),
                                "symbol": str(getattr(fill, "symbol", "")),
                                "side": str(getattr(fill, "side", "")),
                                "quantity": float(getattr(fill, "fill_qty", 0.0) or 0.0),
                                "price": float(getattr(fill, "fill_price", 0.0) or 0.0),
                                "order_id": str(getattr(fill, "client_order_id", "")),
                                "strategy": self._config.strategy_name,
                                "slippage": 0.0,
                            }
                        )
                with contextlib.suppress(Exception):
                    verdict = session.reconciliation.verdict()
                    recon_status = verdict.status.value
                    recon_blocks = bool(session.reconciliation.blocks_live)
            strategy_state = {
                "id": self._config.strategy_name,
                "version": self._strategy_version,
                "status": self._status,
                "mode": self._config.mode,
                "instrument": ", ".join(self._config.symbols),
                "timeframe": self._config.timeframe,
                "live_supported": True,
                "warmup": _WARMUP_BARS,
                "state": "; ".join(
                    f"{s}: {self._last_signal.get(s, 'no signal yet')}"
                    for s in self._config.symbols
                ),
            }
            self._track_open_since()
        total = realized + unrealized
        exposure = sum(abs(p["quantity"]) * (p["current_price"] or 0.0) for p in positions)
        can_halt = self._status == "RUNNING"
        running = self._status == "RUNNING"
        account = (
            dict(self._account_state)
            if running
            else {"ready": False, "reason": "session not running"}
        )
        execution_ready = running and not recon_blocks and not kill_halted
        execution_reason = ""
        if not running:
            execution_reason = self._status_reason or "session not running"
        elif recon_blocks:
            execution_reason = "reconciliation blocks live"
        elif kill_halted:
            execution_reason = "kill switch halted"
        feed = self._feed_kind if running else "none"
        # Service readiness rows only exist around a real session; idle state
        # stays clean (the UBL venue gates already describe static readiness).
        service_gates = (
            [
                {
                    "name": "ACCOUNT",
                    "status": "READY" if account.get("ready") else "NOT READY",
                    "reason": str(account.get("reason", "")),
                },
                {
                    "name": "ORDER EXECUTION",
                    "status": "READY" if execution_ready else "BLOCKED",
                    "reason": execution_reason,
                },
                {
                    "name": "MARKET DATA FEED",
                    "status": "READY" if running and feed != "none" else "NOT READY",
                    "reason": {
                        "live": "broker feed streaming",
                        "local": "local SQLite tail (delayed, not a broker feed)",
                        "none": "session not running",
                    }[feed],
                },
            ]
            if self._sessions
            else []
        )
        arm_blockers = list(blockers)
        if self._status == "RUNNING" and self._config.mode == "LIVE":
            armed_states = set()
            for session in self._sessions.values():
                with contextlib.suppress(Exception):
                    armed_states.add(session.armed)
            if LiveArm.ARMED not in armed_states and LiveArm.RUNNING not in armed_states:
                arm_blockers.append("sessions not armed")
        return {
            "mode": self._config.mode,
            "session_status": self._status,
            "status_reason": self._status_reason,
            "broker": {
                "name": broker_name,
                "environment": self._config.mode,
                "connected": connected,
                "reason": broker_reason,
            },
            "strategy": strategy_state,
            "positions": positions,
            "position": positions[0] if len(positions) == 1 else None,
            "orders": orders[-50:],
            "fills": fills[-50:],
            "pnl": {
                "realized": realized,
                "unrealized": unrealized,
                "total": total,
                "exposure": exposure,
                "orders": len(orders),
                "fills": fill_count,
                "wins": wins,
                "losses": losses,
            },
            "risk": {
                "status": "HALTED" if kill_halted else ("BLOCKED" if recon_blocks else "READY"),
                "limits": [("max_order_qty", self._config.quantity, "ok")],
                "decisions": [],
            },
            "reconciliation": {
                "status": recon_status,
                "positions": len(positions),
                "orders": open_count,
                "last_check": "",
                "mismatches": [],
                "blocks_live": recon_blocks,
            },
            "kill": {"halted": kill_halted, "level": ""},
            "gates": service_gates,
            "account": account,
            "execution": {"ready": execution_ready, "reason": execution_reason},
            "feed": feed,
            "can_arm": False,
            "arm_blockers": arm_blockers,
            "can_halt": can_halt,
            "lifecycle": lifecycle,
            "events": list(self._activity)[-100:],
            "market_symbol": self._config.symbols[0] if self._config.symbols else "",
            "market_timeframe": self._config.timeframe,
            "market_bars": self._chart_bars(),
            "available_strategies": self.available_strategies(),
            "available_symbols": self.available_symbols(),
            "selected_symbols": self._config.symbols,
            "available_timeframes": self._setup_timeframes(),
            "selected_timeframe": self._config.timeframe,
            "quantity": self._config.quantity,
            "start_blockers": blockers,
            "active_positions": len(positions),
            "open_orders": open_count,
        }

    # ── session construction (same record, same engine, per-symbol) ─────

    def _compiled(self, record: Any) -> Any:
        key = f"{record.id}:{record.version}"
        code = record.code
        cached = self._compiled_cache.get(key)
        if cached is not None and cached[0] == code:
            return cached[1]
        compiled = compile_strategy(code)
        self._compiled_cache[key] = (code, compiled)
        return compiled

    def _build_sessions(self) -> None:
        assert self._config.strategy_name and self._config.symbols and self._config.timeframe
        record = load_strategy_record(self._config.strategy_name, self._strategy_dir)
        if record is None:
            raise LiveConfigError(f"strategy not found: {self._config.strategy_name}")
        try:
            compiled = self._compiled(record)
        except Exception as exc:
            raise LiveConfigError(f"strategy does not compile: {exc}") from exc
        mode = ExecutionMode.LIVE if self._config.mode == "LIVE" else ExecutionMode.PAPER
        self._ensure_live_venues()
        md_faces: dict[str, Any] = {}
        self._feed_kind = "local"
        self._live_venue_id = ""
        self._account_state = {"ready": False, "reason": "session not running"}
        if mode == ExecutionMode.LIVE:
            self._live_venue_id = self._resolve_live_venue_id(self.broker_name()) or ""
            if not self._live_venue_id:
                raise LiveConfigError(f"no live trading venue for broker {self.broker_name()!r}")
            md_faces = self._probe_live_venue(self._live_venue_id)
            if md_faces:
                self._feed_kind = "live"
        base_dir = self._data_dir / "live" / "sessions"
        sessions: dict[str, LiveSession] = {}
        try:
            for symbol in self._config.symbols:
                history = self._load_history(symbol, self._config.timeframe)
                if not history:
                    raise LiveConfigError(f"no market data for {symbol}")
                params = StrategyParameters(compiled.param_defaults)

                def _factory(compiled=compiled, params=params) -> Any:
                    logic = compiled.create_logic(params, owner_id=record.name)
                    # Composition-layer live declaration: library strategies
                    # (e.g. Pine-parity OBR) predate the SUPPORTS_LIVE
                    # convention; the operator/service asserts live-capability
                    # here. Metadata ONLY — entry/exit math untouched, same
                    # object the backtest drives through logic.on_bar.
                    with contextlib.suppress(Exception):
                        logic.SUPPORTS_LIVE = True
                    return logic

                definition = StrategyDefinition(
                    id=record.id,
                    name=record.name,
                    version=record.version,
                    kind="live",
                    params=params,
                )
                session_dir = base_dir / symbol
                if symbol in md_faces:
                    from execution import BrokerFeedProvider

                    provider = BrokerFeedProvider(md_faces[symbol], self._config.timeframe)
                else:
                    provider = SqliteTailProvider(self._data_dir, self._config.timeframe)
                policy = RiskPolicy(
                    max_order_qty=self._config.quantity,
                    max_position_qty=self._config.quantity,
                    allowed_symbols=(symbol,),
                )
                adapter = self._live_venue_id if mode == ExecutionMode.LIVE else ""
                session = LiveSession(
                    SessionConfig(
                        mode=mode,
                        paper_capital=self._config.capital,
                        journal_path=str(session_dir / "journal.jsonl"),
                        memory_path=str(session_dir / "memory.json"),
                        kill_switch_path=str(session_dir / "kill.json"),
                        adapter_name=adapter,
                    ),
                    provider,
                    policy,
                    request_id=f"live-{symbol}",
                )
                contract = session.register_strategy(
                    record.id, record.version, _factory, params, definition
                )
                if contract.missing:
                    raise LiveConfigError(
                        f"strategy requirements missing: {', '.join(contract.missing)}"
                    )
                stored = self._stored_checkpoint(symbol)
                if stored and mode == ExecutionMode.LIVE:
                    with contextlib.suppress(Exception):
                        session.recover(stored)
                warmup = tuple(history[-_WARMUP_BARS:])
                report = session.start((symbol,), self._config.timeframe, {record.id: warmup})
                if not report.ready:
                    raise LiveConfigError("; ".join(report.reasons))
                if session.mode != mode:
                    # NEVER silently fall back: a downgraded session must not
                    # trade under the wrong mode label (fail-closed here).
                    raise LiveConfigError(
                        f"venue refused {mode.value}: running {session.mode.value} "
                        "instead — refusing to start"
                    )
                with contextlib.suppress(Exception):
                    session.reconcile_now()
                if mode == ExecutionMode.LIVE:
                    session.arm(f"operator confirmed at {self._confirmed_live_at}")
                sessions[symbol] = session
                self._session_dirs[symbol] = session_dir
                self._journal_cursors[symbol] = len(session.journal.entries)
                self._last_signal[symbol] = "no signal yet"
                self._strategy_version = record.version
        except Exception:
            for session in sessions.values():
                with contextlib.suppress(Exception):
                    session.stop()
            raise
        self._teardown_sessions()
        self._sessions = sessions

    def _probe_live_venue(self, venue_id: str) -> dict[str, Any]:
        """START-time venue truth: connect + health + account, read-only.

        Raises LiveConfigError with the exact reason on any failure. One
        market-data face per configured symbol is returned when the venue
        serves MARKET_DATA, else {} (local SQLite tail applies). Probing
        places no orders and changes no venue state.
        """
        from broker.registry import default_registry
        from broker.vocab import Domain

        try:
            record = default_registry().get(venue_id)
        except Exception:
            raise LiveConfigError(f"live venue {venue_id!r} not registered") from None
        try:
            face = record.plugin.face(Domain.TRADING)
        except Exception:
            raise LiveConfigError(f"venue {venue_id!r} has no trading face") from None
        try:
            from execution import BrokerAdapter

            if not isinstance(face, BrokerAdapter):
                raise LiveConfigError(f"venue {venue_id!r} does not satisfy the order interface")
        except LiveConfigError:
            raise
        except ImportError:
            pass
        try:
            connect_fn = getattr(face, "connect", None)
            health_fn = getattr(face, "health", None)
            if not callable(connect_fn) or not callable(health_fn):
                raise LiveConfigError(f"venue {venue_id!r} face is not connectable")
            connect_fn()
            status = health_fn()
        except LiveConfigError:
            raise
        except Exception as exc:
            raise LiveConfigError(f"broker connection failed: {exc}") from None
        if isinstance(status, (tuple, list)) and len(status) >= 2:
            healthy, reason = bool(status[0]), str(status[1])
        else:
            raise LiveConfigError("broker health unreadable")
        if not healthy:
            raise LiveConfigError(f"broker not connected: {reason}")
        try:
            account_fn = getattr(face, "account", None)
            funds_fn = getattr(face, "funds", None)
            account = account_fn() if callable(account_fn) else {}
            funds = funds_fn() if callable(funds_fn) else {}
        except Exception as exc:
            raise LiveConfigError(f"account not ready: {exc}") from None
        if not isinstance(account, dict):
            raise LiveConfigError("account not ready: unreadable venue response")
        self._account_state = {
            "ready": True,
            "reason": f"{account.get('account_id', venue_id)} ready",
            "account_id": str(account.get("account_id", "")),
            "funds": dict(funds) if isinstance(funds, dict) else {},
        }
        md_faces: dict[str, Any] = {}
        try:
            for symbol in self._config.symbols:
                md_faces[symbol] = record.plugin.face(Domain.MARKET_DATA)
        except Exception:
            md_faces = {}
        return md_faces

    def _teardown_sessions(self) -> None:
        for session in self._sessions.values():
            with contextlib.suppress(Exception):
                session.stop()
        self._sessions = {}

    def _load_history(self, symbol: str, timeframe: str) -> tuple[Bar, ...]:
        try:
            return tuple(self._repository.get_candles_timeframe(symbol, timeframe, None))
        except Exception:
            return ()

    # ── activity / marks / chart (UI derivation, never invented) ────────

    def _drain_activity(self) -> None:
        """Fold new journal entries once (cursor-based, no full scans).

        Journals are append-only, so the ``SIGNAL_GENERATED`` bookkeeping
        rides the same new-entry slice instead of rescanning all entries
        every tick.
        """
        for symbol, session in self._sessions.items():
            entries = session.journal.entries
            cursor = self._journal_cursors.get(symbol, 0)
            new = entries[cursor:]
            for entry in new:
                self._append_activity(symbol, entry)
                if entry.kind == "SIGNAL_GENERATED":
                    payload = entry.payload or {}
                    self._last_signal[symbol] = (
                        f"{payload.get('signal_id', 'signal')} @ {entry.timestamp}"
                    )
            self._journal_cursors[symbol] = len(entries)

    def _append_activity(self, symbol: str, entry: Any) -> None:
        kind = str(getattr(entry, "kind", ""))
        payload = getattr(entry, "payload", {}) or {}
        status = "info"
        if kind in ("ORDER_REJECTED", "RISK_DENIED", "STRATEGY_ERROR", "NOT_LIVE_READY"):
            status = "error"
        elif kind in ("ORDER_FILL", "LIVE_READY", "RECONCILED"):
            status = "ok"
        self._activity.append(
            {
                "timestamp": str(getattr(entry, "timestamp", "")),
                "strategy": str(payload.get("strategy_id", self._config.strategy_name)),
                "symbol": str(payload.get("symbol", symbol)),
                "event": self._describe_entry(kind, payload),
                "status": status,
            }
        )

    @staticmethod
    def _describe_entry(kind: str, payload: dict[str, Any]) -> str:
        if kind == "SIGNAL_GENERATED":
            return f"signal {payload.get('signal_id', '')}"
        if kind == "ORDER_SUBMITTED":
            return f"order submitted {payload.get('client_order_id', '')}"
        if kind == "ORDER_FILL":
            return (
                f"fill {payload.get('client_order_id', '')} "
                f"{payload.get('fill_qty', '')} @ {payload.get('fill_price', '')}"
            )
        if kind == "ORDER_REJECTED":
            return f"rejected {payload.get('client_order_id', '')}: {payload.get('reason', '')}"
        if kind == "RISK_DENIED":
            reasons = payload.get("reasons", "")
            return f"risk denied: {reasons}"
        if kind == "ORDER_BLOCKED_UNARMED":
            return "order blocked: live not armed"
        if kind == "STALE_DATA":
            return f"stale data (seq {payload.get('seq', '')})"
        if kind == "STRATEGY_ERROR":
            return f"strategy error: {payload.get('reason', '')}"
        return kind.lower().replace("_", " ")

    def _record_activity(self, strategy: str, symbol: str, event: str, status: str) -> None:
        self._activity.append(
            {
                "timestamp": _utcnow_iso(),
                "strategy": strategy,
                "symbol": symbol,
                "event": event,
                "status": status,
            }
        )

    def _refresh_mark(self, symbol: str) -> None:
        """Update the mark from the session's own provider (zero extra reads).

        Providers track the latest close they produced; a DB read here
        would duplicate that work every tick on the UI thread.
        """
        session = self._sessions.get(symbol)
        if session is None:
            return
        provider = getattr(session, "_provider", None)
        last_close = getattr(provider, "last_close", None)
        if isinstance(last_close, dict):
            price = last_close.get(symbol)
            if price and float(price) > 0:
                self._marks[symbol] = float(price)

    def _track_open_since(self) -> None:
        for symbol, session in self._sessions.items():
            try:
                flat = session.ledger.position(symbol).flat
            except Exception:
                continue
            if flat:
                self._open_since.pop(symbol, None)
            elif symbol not in self._open_since:
                self._open_since[symbol] = _utcnow_iso()

    def _chart_bars(self) -> tuple[Bar, ...] | None:
        """Chart backdrop for the LIVE tab, cached on (symbol, timeframe).

        The UI calls snapshot() every second; the backdrop only changes
        when the setup changes or a new candle lands — both bump the cache
        key (setup) or refresh marks separately. Chart history itself is
        not the strategy input (sessions consume provider events), so a
        per-second reload here was pure waste.
        """
        if not self._config.symbols or not self._config.timeframe:
            return None
        key = (self._config.symbols[0], self._config.timeframe)
        if key == self._chart_cache_key and self._chart_cache is not None:
            return self._chart_cache
        try:
            self._chart_cache = tuple(
                self._repository.get_candles_timeframe(
                    self._config.symbols[0], self._config.timeframe, 500
                )
            )
            self._chart_cache_key = key
        except Exception:
            return None
        return self._chart_cache

    def _setup_timeframes(self) -> tuple[str, ...]:
        if not self._config.symbols:
            return ()
        return self.available_timeframes(self._config.symbols[0])

    @staticmethod
    def _history_time(item: dict[str, Any]) -> str:
        history = item.get("history", ())
        if history:
            return str(history[-1][1]) if len(history[-1]) > 1 else ""
        return ""

    # ── persistence (config always; checkpoints per symbol) ─────────────

    def _restore_config(self) -> None:
        data = self._store.load()
        config = data.get("config", {})
        if not isinstance(config, dict):
            return
        try:
            self._config = LiveTradeConfig(
                strategy_name=str(config.get("strategy_name", "")),
                symbols=tuple(config.get("symbols", ())),
                timeframe=str(config.get("timeframe", "")),
                mode=str(config.get("mode", "PAPER")),
                quantity=float(config.get("quantity", 1.0) or 0.0),
                capital=float(config.get("capital", 1_000_000.0) or 1_000_000.0),
            )
        except (TypeError, ValueError):
            self._config = LiveTradeConfig()
        if self._config.mode not in ("PAPER", "LIVE"):
            self._config.mode = "PAPER"
        # Restored config NEVER auto-starts (real-money safety + paper hygiene).
        self._status = "STOPPED"

    def _persist_config(self) -> None:
        data = self._store.load()
        data["config"] = {
            "strategy_name": self._config.strategy_name,
            "symbols": list(self._config.symbols),
            "timeframe": self._config.timeframe,
            "mode": self._config.mode,
            "quantity": self._config.quantity,
            "capital": self._config.capital,
        }
        with contextlib.suppress(Exception):
            self._store.save(data)

    def _stored_checkpoint(self, symbol: str) -> dict[str, Any]:
        data = self._store.load()
        checkpoints = data.get("checkpoints", {})
        if isinstance(checkpoints, dict):
            stored = checkpoints.get(symbol, {})
            if isinstance(stored, dict):
                return stored
        return {}

    def _persist(self) -> None:
        data = self._store.load()
        data["config"] = {
            "strategy_name": self._config.strategy_name,
            "symbols": list(self._config.symbols),
            "timeframe": self._config.timeframe,
            "mode": self._config.mode,
            "quantity": self._config.quantity,
            "capital": self._config.capital,
        }
        checkpoints: dict[str, Any] = {}
        for symbol, session in self._sessions.items():
            with contextlib.suppress(Exception):
                checkpoints[symbol] = session.checkpoint()
        data["checkpoints"] = checkpoints
        data["open_since"] = dict(self._open_since)
        data["activity"] = list(self._activity)[-200:]
        with contextlib.suppress(Exception):
            self._store.save(data)


__all__ = [
    "LiveTradeConfig",
    "LiveConfigError",
    "LiveSessionStore",
    "LiveTradingService",
    "DEFAULT_STRATEGY_DIR",
]
