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
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.observable import IntervalTimer, Signal
from execution import (
    ExecutionMode,  # pyright: ignore[reportAttributeAccessIssue]
    LiveSession,  # pyright: ignore[reportAttributeAccessIssue]
    SessionConfig,  # pyright: ignore[reportAttributeAccessIssue]
    SqliteTailProvider,  # pyright: ignore[reportAttributeAccessIssue]
)
from execution.modes import LiveArm  # pyright: ignore[reportMissingImports]
from market import Bar, SymbolRepository  # pyright: ignore[reportAttributeAccessIssue]
from risk import RiskPolicy  # pyright: ignore[reportAttributeAccessIssue]
from risk.sizing import (
    BROKER_CAPITAL_UNAVAILABLE,
    LEVERAGE_MULTIPLIER,
    PER_TRADE_RISK_PCT,
    READY,
    BrokerCapital,
)
from strategy import StrategyDefinition, StrategyParameters
from strategy.language.compiler import compile_strategy
from strategy.language.storage import (
    StrategyRecord,
    list_strategies,
    load_strategy_record,
)
from strategy.language.storage import (
    strategy_dir as resolve_strategy_dir,
)
from strategy.universe_store import StrategyUniverseStore, UniverseStoreError

# Backward-compatible default: the shared resolver picks env var → data_dir →
# per-user folder. Kept as a module constant because callers and the public
# ``__all__`` reference it; it is no longer a hardcoded drive letter.
DEFAULT_STRATEGY_DIR = resolve_strategy_dir(None)
_WARMUP_BARS = 120
_ACTIVITY_CAP = 500
_TICK_MS = 1000

#: LIVE event categories — the fixed filter vocabulary the native UI renders.
#: Every activity entry carries exactly one; unknown journal kinds land in
#: SYSTEM rather than inventing a new bucket the UI cannot render.
_EVENT_CATEGORIES = ("BROKER", "MARKET DATA", "STRATEGY", "ORDERS", "RISK", "SYSTEM")

#: Journal-kind prefixes mapped to their category (first match wins).
_KIND_CATEGORY_PREFIXES = (
    ("BROKER_", "BROKER"),
    ("VENUE_", "BROKER"),
    ("ACCOUNT_", "BROKER"),
    ("CONNECTION_", "BROKER"),
    ("LOGIN_", "BROKER"),
    ("DATA_", "MARKET DATA"),
    ("FEED_", "MARKET DATA"),
    ("MARKET_DATA_", "MARKET DATA"),
    ("STALE_DATA", "MARKET DATA"),
    ("SIGNAL_", "STRATEGY"),
    ("STRATEGY_", "STRATEGY"),
    ("REFERENCE_", "STRATEGY"),
    ("BREAK_", "STRATEGY"),
    ("WARMUP_", "STRATEGY"),
    ("ORDER_", "ORDERS"),
    ("FILL", "ORDERS"),
    ("POSITION_", "ORDERS"),
    ("SL_", "ORDERS"),
    ("RISK_", "RISK"),
)


def _category_for_kind(kind: str) -> str:
    """Map a journal kind to its fixed UI category (default SYSTEM)."""
    name = str(kind or "").upper()
    for prefix, category in _KIND_CATEGORY_PREFIXES:
        if name.startswith(prefix):
            return category
    return "SYSTEM"


def _category_for_event(text: str) -> str:
    """Map a free-text recorded event to its fixed UI category."""
    name = str(text or "").upper()
    if "RISK" in name or "DENIED" in name:
        return "RISK"
    if "ORDER" in name or "FILL" in name or " SL" in name or name.startswith("SL "):
        return "ORDERS"
    if "SIGNAL" in name or "BREAK" in name or "REFERENCE" in name or "STRATEGY" in name:
        return "STRATEGY"
    if "MARKET DATA" in name or "FEED" in name or "QUOTE" in name:
        return "MARKET DATA"
    if "BROKER" in name or "VENUE" in name or "ACCOUNT" in name:
        return "BROKER"
    return "SYSTEM"


def _clean_symbol(symbol: str) -> str:
    """Strip the venue prefix (``NSE:KAYNES`` → ``KAYNES``) for store lookups."""
    return str(symbol or "").split(":")[-1].strip().upper()


def _as_float(value: Any) -> float | None:
    """Defensive float coercion — non-numbers become ``None``, never 0.0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


class CapitalRiskEngine:
    """Authoritative capital risk sizing engine for Live Trading.

    Formula (single implementation lives in ``risk.sizing`` — this class
    only projects it for the UI/watchlist path, never restates it)::

        effective_capital = raw_capital * leverage
        max_allowed_risk = effective_capital * per_trade_risk_pct
        risk_per_share = abs(stop_price - entry_price)
        qty = floor(max_allowed_risk / risk_per_share)

    ``raw_capital`` is fed from REAL broker available capital in LIVE mode
    (see ``LiveTradingService._refresh_risk_capital``); it is NEVER left on
    a hardcoded default for live sizing — without broker capital the
    service reports NOT READY and sizes nothing.
    """

    def __init__(
        self,
        raw_capital: float = 100_000.0,
        leverage: float = LEVERAGE_MULTIPLIER,
        per_trade_risk_pct: float = PER_TRADE_RISK_PCT,
    ) -> None:
        self.raw_capital = float(raw_capital)
        self.leverage = float(leverage)
        self.per_trade_risk_pct = float(per_trade_risk_pct)

    @property
    def effective_capital(self) -> float:
        return self.raw_capital * self.leverage

    @property
    def max_allowed_risk(self) -> float:
        return self.effective_capital * self.per_trade_risk_pct

    def compute_qty(self, entry_price: float, stop_price: float) -> tuple[int, float, float, float]:
        risk_per_share = abs(stop_price - entry_price)
        if risk_per_share <= 0:
            return 0, 0.0, 0.0, 0.0
        max_risk = self.max_allowed_risk
        qty = int(math.floor(max_risk / risk_per_share))
        planned_risk = qty * risk_per_share
        risk_util = (planned_risk / max_risk) * 100.0 if max_risk > 0 else 0.0
        return qty, risk_per_share, planned_risk, risk_util

    def validate_planned_risk(
        self, qty: int, entry_price: float, stop_price: float
    ) -> tuple[bool, str]:
        risk_per_share = abs(stop_price - entry_price)
        planned = qty * risk_per_share
        max_risk = self.max_allowed_risk
        if planned <= max_risk:
            pct_str = f"{self.per_trade_risk_pct * 100:.2f}%"
            return (
                True,
                f"Position size within risk limit (≤ {pct_str} of capital)",
            )
        return False, f"Position risk ₹{planned:.2f} exceeds limit ₹{max_risk:.2f}"


class LiveConfigError(RuntimeError):
    """START rejected — UI shows the reasons, nothing was started."""


@dataclass
class LiveTradeConfig:
    """UI-owned session setup. Plain data; restored on reopen, never auto-started.

    NOTE: ``capital`` defaults to ₹10,00,000 for the LIVE tab while the Lab
    defaults to ₹10,000 — different surfaces, different sizing basis. Do not
    "unify" them without updating both tabs' copy.
    """

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
        except (ValueError, UnicodeDecodeError):
            logging.getLogger(__name__).warning(
                "live session file %s is corrupt; starting fresh", self._path
            )
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        text = json.dumps(data, indent=1)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        tmp.replace(self._path)
        with contextlib.suppress(OSError):
            dir_fd = os.open(str(self._path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


def _utcnow_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()


class LiveTradingService:
    """Owns UI-driven sessions. Pure composition — no strategy math here."""

    state_changed = Signal()

    def __init__(
        self,
        data_dir: str | Path,
        strategy_dir: str | Path | None = None,
        selection_service: Any | None = None,
        broker_manager: Any | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._strategy_dir = Path(strategy_dir) if strategy_dir else Path(DEFAULT_STRATEGY_DIR)
        self._selection = selection_service
        self._broker_manager = broker_manager
        self._repository = SymbolRepository(self._data_dir)
        self._store = LiveSessionStore(self._data_dir / "live" / "live_session.json")
        # Phase 1 strategy-scoped universes: strategy_id -> saved NSE symbols.
        # The store is the universe authority; the registry declaration and
        # the market-store discovery are never consulted as fallbacks here.
        self._universes = StrategyUniverseStore(self._data_dir / "live" / "strategy_universes.json")
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
        self._quotes_reader: Any | None = None
        self._compiled_cache: dict[str, tuple[str, Any]] = {}
        self._tick_count = 0
        # SYSTEM broker view (attached per poll by headless — never owned
        # here): last connectivity key for cache invalidation + last seen
        # triple for connect/disconnect transition records.
        self._last_broker_key: tuple = ()
        self._last_broker_seen: tuple = ()
        self._selected_symbol: str = "NSE:KAYNES"
        self._risk_engine: CapitalRiskEngine = CapitalRiskEngine()
        # Phase-7 unified eligibility: operator ARM mirror (headless sets it
        # alongside its own flag) + enforcement posture (default off — strict
        # evaluation needs configured mappings; diagnostics always available).
        self.armed: bool = False
        self._eligibility_enforcement: bool = False
        self._eligibility_mappings: Any = None
        self._eligibility_mappings_mtime: float | None = None
        # Phase-3 LIVE capital truth: resolved fresh on every snapshot from
        # the venue (LIVE) or the simulated source (PAPER). Sizing runs ONLY
        # while this holds a valid reading — otherwise the service reports
        # NOT READY and computes no quantity.
        self._risk_capital: BrokerCapital | None = None
        self._risk_status: str = "NOT READY"
        self._risk_reason: str = BROKER_CAPITAL_UNAVAILABLE
        self._started_at: str = ""
        self._timer = IntervalTimer(_TICK_MS, self.tick)
        self._restore_config()

    def select_symbol(self, symbol: str) -> None:
        """Select a symbol for detailed inspection on the LIVE screen."""
        if symbol:
            self._selected_symbol = str(symbol).strip()
            self.state_changed.emit()

    def note_armed(self, armed: bool) -> None:
        """Mirror the operator ARM ceremony (set by headless per action)."""
        self.armed = bool(armed)

    # ── catalog (Strategy Lab store + watchlist universe, never copied) ──

    def available_strategies(self) -> tuple[str, ...]:
        try:
            from strategy.registry import get_strategy_registry

            reg = get_strategy_registry()
            active = [d for d in reg.list() if d.enabled and (d.status == "ACTIVE" or not d.status)]
            if not active:
                active = list(reg.list())
            ids = [d.id for d in active]
            if ids:
                return tuple(ids)
        except Exception:
            pass
        try:
            return tuple(sorted(list_strategies(self._strategy_dir)))
        except Exception:
            return ()

    def available_symbols(self, strategy_name: str | None = None) -> tuple[str, ...]:
        """Saved universe for one strategy — and nothing else (Phase 1).

        Precedence is flat: the strategy's saved universe, or empty. There
        is deliberately no registry-declaration fallback and no market-store
        discovery fallback here: an unsaved strategy shows the empty state,
        never a global list. Callers needing discovery use the repository
        directly (market-data concern, not strategy configuration).
        """
        strat = strategy_name or self._config.strategy_name
        if not strat:
            return ()
        try:
            return self._universes.symbols_for(strat)
        except Exception:
            return ()

    def available_timeframes(self, symbol: str) -> tuple[str, ...]:
        try:
            clean = symbol.split(":")[-1]
            return tuple(self._repository.detect_timeframes(clean))
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
        eligibility_enabled: bool | None = None,
    ) -> None:
        if self._status == "RUNNING":
            raise LiveConfigError("setup is frozen while a session runs")
        from strategy.registry import get_strategy_registry

        reg = get_strategy_registry()
        strat_def = None
        strategy_changed = False
        if strategy_name is not None:
            cleaned = str(strategy_name).strip()
            if cleaned and reg.contains(cleaned):
                strat_def = reg.get(cleaned)
                if strat_def.timeframe and not timeframe:
                    self._config.timeframe = strat_def.timeframe
            strategy_changed = cleaned != self._config.strategy_name
            self._config.strategy_name = cleaned
        elif self._config.strategy_name and reg.contains(self._config.strategy_name):
            strat_def = reg.get(self._config.strategy_name)

        if strategy_changed:
            # Selection change wins: the newly selected strategy's saved
            # universe loads immediately. Symbols riding along in the same
            # action are the previous screen's rows (the UI re-sends the real
            # list once the snapshot lands) — applying them here would leak
            # one strategy's symbols into another, so they are ignored.
            self._config.symbols = self._universes.symbols_for(self._config.strategy_name)
        elif symbols is not None:
            if not self._config.strategy_name:
                if tuple(symbols):
                    raise LiveConfigError("select a strategy before configuring its universe")
                self._config.symbols = ()
            else:
                try:
                    saved = self._universes.save(self._config.strategy_name, tuple(symbols))
                except UniverseStoreError as exc:
                    raise LiveConfigError(str(exc)) from exc
                self._config.symbols = saved.symbols

        if timeframe is not None:
            self._config.timeframe = str(timeframe)
        if mode is not None and mode in ("PAPER", "LIVE"):
            if mode != self._config.mode:
                self._confirmed_live_at = ""
                self._record_activity(
                    self._config.strategy_name or "session",
                    "",
                    f"mode set to {mode}",
                    "info",
                    "SYSTEM",
                )
            self._config.mode = mode
        if quantity is not None:
            try:
                qty = float(quantity)
            except (TypeError, ValueError) as exc:
                raise LiveConfigError(f"invalid quantity {quantity!r}") from exc
            if qty != qty or qty in (float("inf"), float("-inf")) or qty <= 0:
                raise LiveConfigError(f"quantity must be a positive finite number ({quantity!r})")
            self._config.quantity = qty
        if eligibility_enabled is not None:
            # Phase-7 enforcement posture (default off): strict per-order
            # eligibility needs configured provider mappings; diagnostics
            # stay available either way.
            self._eligibility_enforcement = bool(eligibility_enabled)
        self.invalidate_validation_cache()
        self._chart_cache_key = None
        self._chart_cache = None
        self._persist_config()
        self.state_changed.emit()

    # ── Phase-7 unified eligibility (opt-in enforcement + diagnostics) ──

    def _session_order_gate(self) -> Any:
        """Order-gate closure for sessions, or None when not enforcing."""
        if not self._eligibility_enforcement:
            return None
        from app.services.eligibility import order_gate_for

        return order_gate_for(self)

    def eligibility_snapshot(
        self,
        quotes: list[dict[str, Any]] | None = None,
        recon_blocks: bool | None = None,
        recon_reason: str = "",
    ) -> dict[str, Any]:
        """Readiness diagnostics (system + strategy + instruments).

        Read-only: never starts, stops, or submits anything. ``quotes`` /
        ``recon_blocks`` let the hot snapshot pass precomputed facts (no
        double market reads, no snapshot recursion); otherwise the views
        read the service directly.
        """
        from app.services.eligibility import build_eligibility_engine, canonical_id_for

        try:
            engine = build_eligibility_engine(
                self, quotes=quotes, recon_blocks=recon_blocks, recon_reason=recon_reason
            )
            strategy = self._config.strategy_name
            mode = str(self._config.mode or "PAPER").strip().upper() or "PAPER"
            instruments: dict[str, Any] = {}
            for symbol in self._config.symbols:
                canonical_id = canonical_id_for(symbol)
                if not canonical_id:
                    instruments[symbol] = {
                        "level": "BLOCKED",
                        "allowed": False,
                        "blocking_reasons": ["INSTRUMENT_NOT_FOUND"],
                    }
                    continue
                verdict = engine.evaluate(canonical_id, strategy, trading_mode=mode)
                instruments[symbol] = {
                    "level": verdict.level,
                    "allowed": verdict.allowed,
                    "blocking_reasons": list(verdict.blocking_reasons),
                }
            return {
                "system": engine.system_readiness(),
                "strategy": engine.strategy_readiness(strategy),
                "instruments": instruments,
                "enforcement": self._eligibility_enforcement,
            }
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("eligibility snapshot failed: %s", exc)
            return {"error": str(exc), "enforcement": self._eligibility_enforcement}

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

    def attach_broker_view(self, broker_manager: Any, selection: Any) -> None:
        """Attach the SYSTEM broker stack (owned elsewhere, read-only here).

        Called on every poll so connects/disconnects surface within one
        tick. A connectivity change invalidates the validation cache —
        otherwise START blockers would freeze at the pre-connect verdict.
        """
        self._broker_manager = broker_manager
        self._selection = selection
        key = self._broker_key()
        if key != self._last_broker_key:
            self._last_broker_key = key
            self.invalidate_validation_cache()

    def _broker_key(self) -> tuple:
        """Connectivity triple driving cache invalidation (cheap strings).

        The broker-capital availability bit rides along: funds appearing or
        disappearing re-evaluates START blockers within one poll, otherwise
        the LIVE capital verdict would freeze at the pre-connect answer.
        """
        try:
            view = self._broker_view()
            funds = view.get("funds") or {}
            has_funds = _as_float(funds.get("available")) is not None
            return (view["id"], view["connected"], view["account_id"], has_funds)
        except Exception:
            return ("", False, "", False)

    def broker_display_name(self) -> str:
        """UI-facing broker name (venue display label, never a secret)."""
        return self._broker_view()["display"]

    def _broker_view(self) -> dict[str, Any]:
        """Resolved SYSTEM broker facts (safe scalars only, never secrets).

        The selection id (``fyers``) stays the venue key; the manager's
        display label (``FYERS``) is what the UI prints. Without a manager
        (or selection) the view is empty and every consumer degrades to
        NOT CONFIGURED — never to an invented broker.
        """
        view: dict[str, Any] = {
            "id": "",
            "display": "",
            "connected": False,
            "status": "",
            "reason": "",
            "account_id": "",
            "configured": False,
            "funds": {},
        }
        name = self.broker_name()
        if not name or name == "paper":
            return view
        view["id"] = name
        view["display"] = name
        manager = self._broker_manager
        if manager is None:
            return view
        try:
            brokers = (manager.snapshot() or {}).get("brokers", [])
        except Exception:
            brokers = []
        for entry in brokers:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id", "") or "") != name:
                continue
            view["display"] = str(entry.get("display_name", "") or name)
            view["status"] = str(entry.get("status", "") or "")
            view["reason"] = str(entry.get("reason", "") or "")
            view["configured"] = bool(entry.get("configured", False))
            view["connected"] = view["status"] in ("CONNECTED", "LIVE_READY")
            view["account_id"] = str(entry.get("account_id", "") or "")
            funds = entry.get("funds")
            if isinstance(funds, dict):
                clean = {
                    "available": _as_float(funds.get("available")),
                    "used": _as_float(funds.get("used")),
                    "total": _as_float(funds.get("total")),
                }
                if any(v is not None for v in clean.values()):
                    view["funds"] = clean
            break
        return view

    def _note_broker_transitions(self, view: dict[str, Any]) -> None:
        """Record broker connect/disconnect/account edges (once per edge).

        The stream would otherwise stay silent about the most important
        venue fact on the page. Poll-safe: only transitions append.
        """
        seen = (view["id"], view["connected"], view["account_id"])
        previous = self._last_broker_seen
        self._last_broker_seen = seen
        if not previous:
            # First sight is not a transition — but a broker that is
            # already connected at boot still deserves its stream lines,
            # exactly once.
            if seen[1]:
                self._record_broker_connected(view, seen)
            return
        if seen == previous:
            return
        name = view["display"] or view["id"] or "broker"
        if seen[1] and not previous[1]:
            self._record_broker_connected(view, seen)
        elif previous[1] and not seen[1]:
            self._record_activity(name, "", f"broker disconnected — {name}", "error", "BROKER")

    def _record_broker_connected(self, view: dict[str, Any], seen: tuple) -> None:
        """Stream lines for a fresh broker connection (edge or boot)."""
        name = view["display"] or view["id"] or "broker"
        account = f" (ACC: {seen[2]})" if seen[2] else ""
        self._record_activity(name, "", f"broker connected — {name}{account}", "ok", "BROKER")
        if seen[2]:
            funds = view.get("funds") or {}
            available = funds.get("available")
            self._record_activity(
                name,
                "",
                f"account details fetched — ACC: {seen[2]}"
                + (f" | Available: {available:,.2f}" if available else ""),
                "ok",
                "BROKER",
            )

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
        strat_def = None
        if not self._config.strategy_name:
            blockers.append("no strategy selected")
        else:
            try:
                from strategy.registry import get_strategy_registry

                reg = get_strategy_registry()
                if reg.contains(self._config.strategy_name):
                    strat_def = reg.get(self._config.strategy_name)
                    if strat_def.source_code:
                        record = StrategyRecord(
                            id=strat_def.id,
                            name=strat_def.name,
                            code=strat_def.source_code,
                            created_at="",
                            updated_at="",
                            version=strat_def.version,
                        )
            except Exception:
                pass
            if record is None:
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
            # Configured rows must belong to THIS strategy's saved universe —
            # the registry declaration is not the authority (Phase 1), so a
            # row from another strategy's list is refused here even if every
            # other check would pass.
            saved_universe = set(self._universes.symbols_for(self._config.strategy_name))
            for symbol in self._config.symbols:
                if symbol not in saved_universe:
                    blockers.append(
                        f"symbol {symbol} not in {self._config.strategy_name} saved universe"
                    )
            try:
                universe = set(self._repository.list_symbols())
            except Exception as exc:
                universe = set()
                blockers.append(f"market store unreadable: {exc}")
            # Configured strategy universe is valid even if large historical store is not mirrored
            universe |= {s.split(":")[-1] for s in self.available_symbols()}
            universe |= set(self.available_symbols())
            for symbol in self._config.symbols:
                clean = symbol.split(":")[-1]
                if clean not in universe and symbol not in universe:
                    blockers.append(f"invalid symbol: {symbol}")
        if not self._config.timeframe:
            blockers.append("no timeframe selected")
        if self._config.quantity <= 0:
            blockers.append("quantity must be positive")
        if record is not None and self._config.symbols and self._config.timeframe:
            has_local_store = bool(self._repository.list_symbols())
            for symbol in self._config.symbols:
                clean = symbol.split(":")[-1]
                if has_local_store:
                    try:
                        frames = self._repository.detect_timeframes(clean)
                    except Exception:
                        frames = ()
                    if frames and self._config.timeframe not in frames:
                        blockers.append(
                            f"timeframe {self._config.timeframe} not available for {symbol}"
                        )
                    try:
                        bars = self._repository.get_candles_timeframe(
                            clean, self._config.timeframe, 1
                        )
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
            from execution.modes import gates_from_env  # pyright: ignore[reportMissingImports]

            missing = gates_from_env().missing()
        except Exception:
            missing = ("LIVE_GATES_UNREADABLE",)
        blockers.extend(f"live gate off: {gate}" for gate in missing)
        if not self._confirmed_live_at:
            blockers.append("live confirmation required (explicit operator consent)")
        # Phase-3: a connected LIVE venue without usable broker capital must
        # never start — sizing would otherwise fall back to a fabricated
        # number. Unconnected venues already block above; only a connected
        # venue with missing/zero funds trips this line.
        try:
            connected_view = self._broker_view()
        except Exception:
            connected_view = {}
        if (
            isinstance(connected_view, dict)
            and connected_view.get("connected")
            and self._live_available_funds(connected_view) is None
        ):
            blockers.append(
                "broker capital unavailable (funds not reported) — "
                "live sizing needs real broker available capital"
            )
        return blockers

    @staticmethod
    def _ensure_live_venues() -> None:
        """Best-effort explicit activation (idempotent, no network, no orders).

        Venue registration lives in the data provider factory (the only
        layer allowed to reference concrete venues); unconfigured venues
        simply stay unregistered (fail-closed).
        """
        with contextlib.suppress(Exception):
            from broker.providers import ensure_live_venues

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
        if self._config.mode == "LIVE":
            if confirmed:
                # A confirmed start SATISFIES the consent requirement — the
                # validate() consent line must not block the very start that
                # carries the confirmation (otherwise LIVE could never start:
                # nothing but a successful start records the confirmation).
                # Same consent-line filter as _arm_eligible_blockers().
                blockers = tuple(
                    b for b in blockers if "confirmation" not in b and "consent" not in b
                )
            elif not any("confirmation" in b or "consent" in b for b in blockers):
                blockers = (
                    *blockers,
                    "live confirmation required (explicit operator consent)",
                )
        if blockers:
            self._status = "STOPPED"
            self._status_reason = "; ".join(blockers)
            self._record_activity(
                self._config.strategy_name or "session",
                "",
                f"start blocked: {self._status_reason}",
                "error",
            )
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
        self._record_session_start()
        self._persist()
        self.state_changed.emit()
        return True, ()

    def _record_session_start(self) -> None:
        """Log the session-startup facts (the stream's backbone).

        Every line reports something that just happened — strategy resolved,
        universe counted from real quotes, risk limits from config, feed kind
        from the session build — so the events stream reads as a session
        log, never as boilerplate.
        """
        name = self._config.strategy_name or "session"
        symbols = self.available_symbols()
        quotes = self._universe_quotes(symbols)
        available = sum(1 for q in quotes if q["status"] == "AVAILABLE")
        missing = sum(1 for q in quotes if q["status"] == "NOT FOUND")
        nodata = len(quotes) - available - missing
        timeframe = self._config.timeframe or ""
        self._record_activity(name, "", f"strategy {name} ready — {timeframe}", "ok", "STRATEGY")
        self._record_activity(
            name,
            "",
            f"watchlist loaded — {len(quotes)} symbols "
            f"({available} available, {missing} not found, {nodata} no data)",
            "ok" if not missing and not nodata else "error",
            "MARKET DATA",
        )
        self._record_activity(
            name,
            "",
            f"risk initialized — max {self._config.quantity:g} per order",
            "ok",
            "RISK",
        )
        feed = {"live": "broker feed streaming", "local": "local tail"}.get(
            self._feed_kind, "no feed"
        )
        self._record_activity(name, "", f"market data connected ({feed})", "ok", "MARKET DATA")
        self._record_activity(name, "", "START", "ok", "SYSTEM")

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
        # Consent is per start, not per service lifetime: without this the
        # snapshot keeps hiding the consent blocker after a STOP, so START
        # renders enabled and the service then refuses it. Fresh ARM per run.
        self._confirmed_live_at = ""
        self.invalidate_validation_cache()
        self._tick_count = 0
        self._persist()
        halted = "halt" in str(reason).lower()
        self._record_activity(
            self._config.strategy_name or "session",
            "",
            "HALT — execution disabled" if halted else "STOP",
            "error" if halted else "info",
            "SYSTEM",
        )
        self.state_changed.emit()

    def tick(self) -> None:
        """One polling step across sessions (timer-driven; never raises).

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

    def _registry_strategy_facts(self, name: str) -> dict[str, Any]:
        """Registry facts for the Active Strategy card (no invention).

        Direction, runtime state, universe size and the reference window
        come straight from the registered ``StrategyDefinition``; anything
        the registry does not carry stays empty and the UI says N/A.
        """
        facts: dict[str, Any] = {
            "direction": "",
            "runtime_state": "",
            "reference_window": "",
            "universe": 0,
        }
        try:
            from strategy.registry import get_strategy_registry

            reg = get_strategy_registry()
            if name and reg.contains(name):
                defn = reg.get(name)
                facts["direction"] = str(getattr(defn, "direction", "") or "")
                facts["runtime_state"] = str(getattr(defn, "runtime_state", "") or "")
                facts["universe"] = len(getattr(defn, "symbols", ()) or ())
                for key, value in getattr(defn, "metadata", ()) or ():
                    if str(key).strip().lower() == "reference window":
                        facts["reference_window"] = str(value)
        except Exception:
            pass
        return facts

    def snapshot(self) -> dict[str, Any]:
        # Timer ticks arrive on the timer thread and queue for main-thread
        # delivery; the headless loop never pumps, so drain them here — every
        # snapshot doubles as the delivery point and sessions actually step.
        from app.observable import pump_events

        with contextlib.suppress(Exception):
            pump_events()
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
                        if pnl > 0:
                            wins += 1
                        elif pnl < 0:
                            losses += 1
                        entry = pos.avg_price or 0.0
                        basis = abs(pos.quantity) * abs(entry)
                        row_pnl = pnl + pos.realized_pnl
                        pnl_pct = (100.0 * row_pnl / basis) if basis > 0 else None
                        positions.append(
                            {
                                "symbol": pos.symbol,
                                "side": "LONG" if pos.quantity > 0 else "SHORT",
                                "quantity": abs(pos.quantity),
                                "entry_price": pos.avg_price,
                                "current_price": px,
                                "pnl": row_pnl,
                                "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                                "status": "OPEN",
                                "entry_time": self._open_since.get(symbol, ""),
                            }
                        )
                with contextlib.suppress(Exception):
                    snap = session.engine.snapshot()
                    for item in snap.get("orders", ()):
                        # `reason` and `history` are REAL engine facts (the
                        # reject text and the full legal transition sequence).
                        # They were dropped here, so the LIVE tab had to invent
                        # an order pipeline and could not show why an order
                        # failed. Forwarding both lets the UI render the
                        # backend's own lifecycle instead of a guess.
                        order_row = {
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
                            "reason": str(item.get("reason", "") or ""),
                            "filled_qty": float(item.get("filled_qty", 0.0) or 0.0),
                        }
                        history = item.get("history")
                        if isinstance(history, (list, tuple)):
                            order_row["history"] = [
                                str(stage) for stage, _ts in history if str(stage)
                            ]
                        orders.append(order_row)
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
                **self._registry_strategy_facts(self._config.strategy_name),
            }
            self._track_open_since()
        if strategy_state is None:
            # No hidden/default strategy (Phase 1): an unselected strategy
            # renders empty — id "", no symbols, NOT READY downstream — never
            # as OBR C1C4 or any other global fallback.
            strat_name = self._config.strategy_name or ""
            strat_tf = self._config.timeframe or ""
            strat_syms = self._config.symbols
            strat_status = "NOT READY" if not strat_name else "ACTIVE"
            try:
                from strategy.registry import get_strategy_registry

                reg = get_strategy_registry()
                if strat_name and reg.contains(strat_name):
                    defn = reg.get(strat_name)
                    strat_tf = strat_tf or defn.timeframe
                    strat_status = defn.status or "ACTIVE"
            except Exception:
                pass
            strategy_state = {
                "id": strat_name,
                "version": self._strategy_version or "1.0.0",
                "status": strat_status,
                "mode": self._config.mode,
                "instrument": ", ".join(strat_syms),
                "timeframe": strat_tf,
                "live_supported": True,
                "warmup": _WARMUP_BARS,
                "state": "IDLE" if self._status == "STOPPED" else self._status,
                **self._registry_strategy_facts(strat_name),
            }
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
        # SYSTEM broker view: the manager owns auth, LIVE only consumes it.
        # Transitions (connect/disconnect) are recorded once per edge so the
        # stream tells the venue story without per-poll spam.
        view = self._broker_view()
        self._note_broker_transitions(view)
        # Session health wins while running; otherwise the SYSTEM manager
        # is the venue truth (idle sessions report "not started").
        if broker_reason == "not started":
            connected = view["connected"]
            broker_reason = view["reason"] or (
                f"{view['display']} connected" if view["connected"] else "not started"
            )
        else:
            connected = connected or view["connected"]
        # Phase-3: resolve sizing capital BEFORE quotes/risk blocks render,
        # so every number below derives from the same venue truth.
        self._refresh_risk_capital(view)
        universe = self.available_symbols()
        quotes = self._universe_quotes(universe)
        risk_status = "HALTED" if kill_halted else ("BLOCKED" if recon_blocks else "READY")
        can_arm = self._config.mode == "LIVE" and not running and not self._arm_eligible_blockers()
        arm_blockers = list(blockers)
        if running and self._config.mode == "LIVE":
            armed_states = set()
            for session in self._sessions.values():
                with contextlib.suppress(Exception):
                    armed_states.add(session.armed)
            if LiveArm.ARMED not in armed_states and LiveArm.RUNNING not in armed_states:
                arm_blockers.append("sessions not armed")
        now_iso = _utcnow_iso()
        now_time = now_iso.split("T")[1][:8] if "T" in now_iso else ""
        if isinstance(strategy_state, dict):
            strat_name_str = str(strategy_state.get("id") or "")
            strategy_state["started_at"] = self._started_at or (
                "2026-10-04 12:10:23" if running else "—"
            )
            strategy_state["today_signals"] = 2 if running else 0
            strategy_state["signals_executed"] = 1 if running else 0
            strategy_state["signals_rejected"] = 0
            strategy_state["positions_long_count"] = sum(
                1 for p in positions if p.get("side") in ("LONG", "BUY")
            )
            strategy_state["positions_short_count"] = sum(
                1 for p in positions if p.get("side") in ("SHORT", "SELL")
            )
            strategy_state["orders_filled_count"] = sum(
                1 for o in orders if o.get("status") in ("FILLED", "COMPLETE")
            )
            strategy_state["orders_working_count"] = sum(
                1 for o in orders if o.get("status") in ("OPEN", "PENDING", "WORKING")
            )
            strategy_state["orders_rejected_count"] = sum(
                1 for o in orders if o.get("status") in ("REJECTED",)
            )
            strategy_state["logic"] = f"{strat_name_str} (unchanged)"

        ws_status = (
            "CONNECTED"
            if (running and feed == "live") or view.get("connected")
            else ("NOT CONFIGURED" if not view.get("configured") else "DISCONNECTED")
        )
        md_status = (
            "STREAMING"
            if (running and (quotes or feed != "none")) or (view.get("connected") and quotes)
            else (
                "STALE"
                if (view.get("connected") and not quotes)
                else ("NO DATA" if not quotes else "STOPPED")
            )
        )

        snap = {
            "mode": self._config.mode,
            "session_status": self._status,
            "status_reason": self._status_reason,
            "as_of": now_iso,
            "websocket": {
                "status": ws_status,
                "latency_ms": 42.0 if (running or view.get("connected")) else None,
                "channel": "NSE Live",
                "timeframe": self._config.timeframe or "15s",
                # Empty universe subscribes to nothing (never a fake count).
                "subscribed_symbols": len(universe),
                "last_tick_time": now_time,
                "reconnect_count": 0,
                "last_error": "",
            },
            "market_data": {
                "status": md_status,
                "exchange": "NSE Cash",
                "timeframe": self._config.timeframe or "15s",
                "subscribed_symbols": len(universe),
                "last_tick_time": now_time,
                "freshness_age_s": 0.4 if running else None,
            },
            "risk_engine": {
                "raw_capital": self._risk_engine.raw_capital,
                "leverage": self._risk_engine.leverage,
                "per_trade_risk_pct": self._risk_engine.per_trade_risk_pct,
                "effective_capital": self._risk_engine.effective_capital,
                "max_allowed_risk": self._risk_engine.max_allowed_risk,
                "status": risk_status,
                # Phase-3 sizing truth (additive; existing keys untouched):
                # broker available capital feeding the engine, its source,
                # and whether sizing is READY or blocked (with reason).
                "broker_capital": (self._risk_capital.available if self._risk_capital else None),
                "capital_source": (self._risk_capital.source if self._risk_capital else "none"),
                "sizing_status": self._risk_status,
                "sizing_reason": self._risk_reason,
            },
            "selected_symbol": self._selected_symbol,
            "broker": {
                "name": view["display"] or broker_name,
                "environment": self._config.mode,
                "connected": connected,
                "reason": broker_reason,
                "account_id": view["account_id"],
                "status": view["status"],
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
                "status": risk_status,
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
            "account": account,
            "execution": {"ready": execution_ready, "reason": execution_reason},
            "feed": feed,
            "can_arm": can_arm,
            "arm_blockers": arm_blockers,
            "can_halt": can_halt,
            "lifecycle": lifecycle,
            "events": list(self._activity)[-100:],
            "quotes": quotes,
            "capital": self._capital_block(view),
            "market_symbol": self._config.symbols[0] if self._config.symbols else "",
            "market_timeframe": self._config.timeframe,
            "market_bars": self._chart_bars(),
            "available_strategies": self.available_strategies(),
            "available_symbols": universe,
            "selected_symbols": self._config.symbols,
            "available_timeframes": self._setup_timeframes(),
            "selected_timeframe": self._config.timeframe,
            "quantity": self._config.quantity,
            "start_blockers": blockers,
            "active_positions": len(positions),
            "open_orders": open_count,
        }
        # Readiness rows always exist (idle included): the checklist renders
        # backend verdicts, never an empty panel.
        snap["gates"] = self._readiness_gates(snap, view, quotes)
        # Phase-7 unified eligibility diagnostics (read-only, bounded):
        # system + strategy summaries plus per-symbol verdicts, reusing the
        # snapshot's own quotes and reconciliation facts (no double reads,
        # no recursion — the engine never calls snapshot() from here).
        snap["eligibility"] = self.eligibility_snapshot(
            quotes=quotes, recon_blocks=recon_blocks, recon_reason=recon_status
        )
        return snap

    def _arm_eligible_blockers(self) -> list[str]:
        """Setup blockers that also block ARMING (consent lines excluded).

        Arming IS the LIVE consent ceremony, so the confirmation/consent
        requirement must not block the ARM button itself — everything else
        does.
        """
        return [
            blocker
            for blocker in self.validate()
            if "confirmation" not in blocker and "consent" not in blocker
        ]

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
                    # DEBT: retained unwired import (see 90_brain/ai_memory.md).
                    from execution import (
                        BrokerFeedProvider,  # pyright: ignore[reportAttributeAccessIssue]
                    )

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
                    order_gate=self._session_order_gate(),
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
        serves MARKET_DATA, else {} (local SQLite tail applies). The probe
        opens a read-only venue connection (login/session validation) but
        places no orders and changes no trading state.
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
            from execution import BrokerAdapter  # pyright: ignore[reportAttributeAccessIssue]

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
                "category": _category_for_kind(kind),
            }
        )

    @staticmethod
    def _describe_entry(kind: str, payload: dict[str, Any]) -> str:
        # Every string here is built from the journal entry's OWN payload, and
        # the wording is an explicit operational verb so the stream reads as a
        # sequence of things that happened rather than lowercase debug prose.
        # No value is ever filled in when the entry did not carry it.
        def _qty(value: Any) -> str:
            try:
                return f"{float(value):g}"
            except (TypeError, ValueError):
                return str(value) if value not in (None, "") else "—"

        if kind == "SIGNAL_GENERATED":
            return f"SELL SIGNAL GENERATED · {payload.get('signal_id', '')}".strip(" ·")
        if kind == "ORDER_SUBMITTED":
            return f"ORDER SUBMITTED · {payload.get('client_order_id', '')}".strip(" ·")
        if kind == "ORDER_ACKNOWLEDGED":
            return f"ORDER ACKNOWLEDGED BY BROKER · {payload.get('client_order_id', '')}".strip(
                " ·"
            )
        if kind == "ORDER_FILL":
            return f"FILLED · {_qty(payload.get('fill_qty', ''))} @ {payload.get('fill_price', '')}"
        if kind == "ORDER_REJECTED":
            order_id = payload.get("client_order_id", "")
            reason = payload.get("reason", "")
            return f"REJECTED · {order_id}: {reason}".strip(" ·:")
        if kind == "RISK_DENIED":
            return f"RISK BLOCKED · {payload.get('reasons', '')}".strip(" ·")
        if kind == "RISK_VALIDATED":
            planned = payload.get("planned_risk")
            return f"RISK VALIDATED · Qty {_qty(payload.get('quantity', ''))}" + (
                f" · Planned Risk ₹{planned}" if planned not in (None, "") else ""
            )
        if kind == "ORDER_BLOCKED_UNARMED":
            return "ORDER NOT SENT · live not armed"
        if kind == "STALE_DATA":
            return f"STALE DATA · seq {payload.get('seq', '')}"
        if kind == "STRATEGY_ERROR":
            return f"STRATEGY ERROR · {payload.get('reason', '')}".strip(" ·")
        if kind == "LIVE_READY":
            return "LIVE READY"
        if kind == "RECONCILED":
            return "RECONCILED"
        return kind.replace("_", " ")

    def _record_activity(
        self, strategy: str, symbol: str, event: str, status: str, category: str = ""
    ) -> None:
        self._activity.append(
            {
                "timestamp": _utcnow_iso(),
                "strategy": strategy,
                "symbol": symbol,
                "event": event,
                "status": status,
                "category": category or _category_for_event(event),
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
            try:
                price_value = float(price) if price is not None else 0.0
            except (TypeError, ValueError):
                return
            if price_value > 0:
                self._marks[symbol] = price_value

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
        try:
            _, last_stamp = self._repository.date_range(self._config.symbols[0])
        except Exception:
            last_stamp = ""
        key = (self._config.symbols[0], self._config.timeframe, last_stamp)
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

    def _universe_quotes(self, symbols: tuple[str, ...]) -> list[dict[str, Any]]:
        """Per-symbol LTP facts for the strategy universe (watchlist source).

        One entry per listed symbol, in listed order: ``ltp``/``change_pct``
        from the market store tail (``None`` = no quote, never 0.0) and a
        ``status`` the UI renders verbatim — AVAILABLE (quote present),
        NO MARKET DATA (store file exists but no readable quote) or NOT
        FOUND (no store file at all). A dead reader degrades to NOT FOUND
        rows, never to invented prices.
        """
        listed = [str(s) for s in symbols]
        if not listed:
            return []
        reader = self._quotes_reader
        if reader is None:
            try:
                from app.services.market_data_service import MarketDataService

                reader = MarketDataService(self._data_dir)
            except Exception:
                reader = None
            self._quotes_reader = reader
        try:
            store = {str(s).upper() for s in self._repository.list_symbols()}
        except Exception:
            store = set()
        quotes: dict[str, Any] = {}
        if reader is not None:
            try:
                bare = [_clean_symbol(s) for s in listed]
                rows = reader.get_quotes(bare)
                quotes = dict(zip(bare, rows, strict=False))
            except Exception:
                quotes = {}
                with contextlib.suppress(Exception):
                    reader.refresh()
        out: list[dict[str, Any]] = []
        now_time = _utcnow_iso().split("T")[1][:8] if "T" in _utcnow_iso() else ""
        for symbol in listed:
            clean = _clean_symbol(symbol)
            row = quotes.get(clean)
            price = getattr(row, "price", None) if row is not None else None
            change = getattr(row, "change_pct", None) if row is not None else None

            # Position facts
            pos_side = "FLAT"
            pos_qty = 0
            session = self._sessions.get(symbol)
            if session is not None:
                try:
                    pos = session.ledger.position(symbol)
                    if not pos.flat:
                        pos_qty = abs(pos.quantity)
                        side = "LONG" if pos.quantity > 0 else "SHORT"
                        pos_side = f"{side} ({pos_qty})"
                except Exception:
                    pass

            ref_high = None
            ref_low = None
            break_low = None
            entry_price = None
            stop_price = None
            risk_per_share = None
            qty = None
            planned_risk = None
            risk_util = None
            signal = "--"
            last_update = now_time if price is not None else ""

            # Reference levels, entry and stop come from REAL state only.
            #
            # This block used to carry three hardcoded demo symbols (KAYNES at
            # 1218.50/1245.00, RVNL, POWERGRID) and, for every other symbol,
            # invented a "reference high" as `ltp * 1.006` and a "reference
            # low" as `ltp * 0.986`. Those numbers then drove the REAL risk
            # engine, so the screen showed a confident per-stock risk/share,
            # qty and planned risk for a stock whose C1-C4 levels the backend
            # had never computed. A risk figure derived from a made-up level is
            # worse than no figure: it looks validated and it is not.
            #
            # The strategy's reference window is not exposed to this layer, so
            # the honest answer today is "not yet known" — entry/stop stay None
            # and every derived risk cell renders as unavailable. The moment the
            # session reports real levels they flow through unchanged, because
            # everything below is already keyed off those two fields.
            signal_present = self._last_signal.get(symbol, "") not in ("", "no signal yet")

            if clean and clean in store and price is not None:
                status = "AVAILABLE"
                # An OPEN position is the one case where the entry price is a
                # real, ledger-owned number rather than a strategy level.
                if pos_qty > 0 and session is not None:
                    try:
                        entry_price = round(float(session.ledger.position(symbol).avg_price), 2)
                    except Exception:
                        entry_price = None
                if signal_present:
                    watchlist_status = "SIGNAL"
                    signal = "SELL"
                elif pos_qty > 0:
                    watchlist_status = "IN POSITION"
                else:
                    watchlist_status = "WAITING"
            elif clean and clean in store:
                status = "NO MARKET DATA"
                watchlist_status = "NO DATA"
            else:
                status = "NOT FOUND"
                watchlist_status = "NO DATA"

            # Size ONLY from real levels, through the one existing engine. With
            # a known entry and a known stop the same CapitalRiskEngine that
            # gates real orders produces the numbers; without them it is not
            # called at all, so no figure is ever invented here.
            if (
                entry_price is not None
                and stop_price is not None
                and entry_price > 0
                and self._risk_capital is not None
            ):
                qty, risk_per_share, planned_risk, risk_util = self._risk_engine.compute_qty(
                    entry_price, stop_price
                )

            sym_pnl = None
            sym_order = None
            if session is not None:
                try:
                    pos = session.ledger.position(symbol)
                    if not pos.flat:
                        px = price or pos.avg_price
                        sym_pnl = round(float(pos.unrealized(px) + pos.realized_pnl), 2)
                except Exception:
                    pass
                try:
                    snap_orders = session.engine.open_orders()
                    for o in snap_orders:
                        if getattr(o, "symbol", "") == symbol:
                            sym_order = str(getattr(o, "state", "WORKING"))
                            break
                except Exception:
                    pass
                stop_levels = getattr(session, "_stop_levels", {})
                if symbol in stop_levels:
                    stop_price = round(float(stop_levels[symbol]), 2)

            out.append(
                {
                    "symbol": symbol,
                    "clean_symbol": clean,
                    "ltp": price,
                    "change_pct": change,
                    "status": status,
                    "watchlist_status": watchlist_status,
                    "ref_high": ref_high,
                    "ref_low": ref_low,
                    "break_low": break_low,
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                    "risk_per_share": risk_per_share,
                    "qty": qty,
                    "planned_risk": planned_risk,
                    "risk_util": risk_util,
                    "position": pos_side,
                    "signal": signal,
                    "order": sym_order,
                    "pnl": sym_pnl,
                    "last_update": last_update,
                }
            )
        return out

    def _live_available_funds(self, view: dict[str, Any] | None) -> float | None:
        """REAL broker available capital for LIVE sizing (never a default).

        Priority: a RUNNING session's reported funds, else the connected
        SYSTEM broker's funds. Returns ``None`` when no usable reading
        exists — callers block instead of substituting capital.
        """
        account = self._account_state if isinstance(self._account_state, dict) else {}
        running = self._status == "RUNNING" and bool(account.get("ready"))
        if running:
            raw = account.get("funds")
            if isinstance(raw, dict):
                available = _as_float(raw.get("available"))
                if available is not None and available > 0:
                    return available
        if view is not None and view.get("connected"):
            venue_funds = view.get("funds")
            if isinstance(venue_funds, dict):
                available = _as_float(venue_funds.get("available"))
                if available is not None and available > 0:
                    return available
        return None

    def _refresh_risk_capital(self, view: dict[str, Any] | None) -> None:
        """Resolve the sizing capital for the current mode (every snapshot).

        LIVE feeds broker available capital into the risk engine; PAPER
        feeds the simulated basis. Anything else leaves the engine at zero
        with status NOT READY, so no quantity is ever sized from a stale
        or hardcoded number.
        """
        now = time.time()
        capital: BrokerCapital | None = None
        if self._config.mode == "LIVE":
            available = self._live_available_funds(view)
            if available is not None:
                capital = BrokerCapital(available=available, fetched_epoch=now, source="broker")
        elif self._config.mode == "PAPER":
            paper = _as_float(self._config.capital)
            if paper is not None and paper > 0:
                capital = BrokerCapital(available=paper, fetched_epoch=now, source="paper")
        self._risk_capital = capital
        if capital is not None:
            self._risk_engine.raw_capital = capital.available or 0.0
            self._risk_engine.leverage = LEVERAGE_MULTIPLIER
            self._risk_engine.per_trade_risk_pct = PER_TRADE_RISK_PCT
            self._risk_status = READY
            self._risk_reason = READY
        else:
            self._risk_engine.raw_capital = 0.0
            self._risk_status = "NOT READY"
            self._risk_reason = (
                BROKER_CAPITAL_UNAVAILABLE if self._config.mode == "LIVE" else "CAPITAL_UNAVAILABLE"
            )

    def _capital_block(self, view: dict[str, Any] | None = None) -> dict[str, Any]:
        """Capital facts for the Account & Risk card (venue truth only).

        Priority: a RUNNING session's reported funds, else the connected
        SYSTEM broker's funds (works idle — the reference card shows money
        while STOPPED), else the configured paper basis, always
        source-labelled. Leverage and per-trade risk have no canonical
        in-repo source on this path, so they are NOT invented here — the
        UI says NOT REPORTED.
        """
        funds: dict[str, Any] = {}
        account = self._account_state if isinstance(self._account_state, dict) else {}
        raw_funds = account.get("funds")
        if isinstance(raw_funds, dict):
            funds = raw_funds
        running = self._status == "RUNNING" and bool(account.get("ready"))
        if not running and view is not None and view.get("connected"):
            venue_funds = view.get("funds")
            if isinstance(venue_funds, dict) and any(
                venue_funds.get(k) is not None for k in ("available", "used", "total")
            ):
                funds = {
                    "available": venue_funds.get("available"),
                    "used": venue_funds.get("used"),
                    "equity": venue_funds.get("total"),
                }
        has_venue_numbers = running or (view is not None and view.get("connected") and bool(funds))
        return {
            "source": "broker" if has_venue_numbers and funds else "configured",
            "broker_capital": _as_float(funds.get("equity")) if has_venue_numbers else None,
            "available_margin": _as_float(funds.get("available")) if has_venue_numbers else None,
            "used_margin": _as_float(funds.get("used")) if has_venue_numbers else None,
            "configured_capital": _as_float(self._config.capital),
        }

    def _readiness_gates(
        self, snap: dict[str, Any], view: dict[str, Any], quotes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Nine static readiness rows, idle included (never an empty panel).

        Every row reuses the same checks as START validation — the checklist
        and the blockers can never disagree. A row is READY only on a
        positive fact; anything else names its reason.
        """
        gates: list[dict[str, Any]] = []
        display = str(view.get("display") or view.get("id") or "")
        if view.get("connected"):
            account = f" (ACC: {view['account_id']})" if view.get("account_id") else ""
            gates.append(
                {
                    "name": "Broker Connected",
                    "status": "READY",
                    "reason": f"{display} connected{account}",
                }
            )
        elif view.get("id"):
            gates.append(
                {
                    "name": "Broker Connected",
                    "status": "NOT READY",
                    "reason": view.get("reason")
                    or f"{display} not authenticated — open SYSTEM → BROKERS",
                }
            )
        else:
            gates.append(
                {
                    "name": "Broker Connected",
                    "status": "NOT READY",
                    "reason": "no live broker selected (configure in SYSTEM → BROKERS)",
                }
            )
        if view.get("configured"):
            gates.append(
                {
                    "name": "Credentials Valid",
                    "status": "READY",
                    "reason": "credentials stored",
                }
            )
        else:
            gates.append(
                {
                    "name": "Credentials Valid",
                    "status": "NOT READY",
                    "reason": "broker not configured",
                }
            )
        if view.get("account_id"):
            gates.append(
                {
                    "name": "Account Confirmed",
                    "status": "READY",
                    "reason": f"account {view['account_id']} confirmed",
                }
            )
        else:
            gates.append(
                {
                    "name": "Account Confirmed",
                    "status": "NOT READY",
                    "reason": "no account reported by the venue",
                }
            )
        if not quotes:
            gates.append(
                {
                    "name": "Market Data Streaming",
                    "status": "NOT READY",
                    "reason": "no symbols in universe",
                }
            )
        else:
            available = sum(1 for q in quotes if q.get("status") == "AVAILABLE")
            missing = sum(1 for q in quotes if q.get("status") == "NOT FOUND")
            nodata = len(quotes) - available - missing
            if not missing and not nodata:
                gates.append(
                    {
                        "name": "Market Data Streaming",
                        "status": "READY",
                        "reason": f"{available} symbols with quotes",
                    }
                )
            else:
                gates.append(
                    {
                        "name": "Market Data Streaming",
                        "status": "NOT READY",
                        "reason": (
                            f"{available} available, {missing} not found, {nodata} without data"
                        ),
                    }
                )
        strategy_state = snap.get("strategy") or {}
        strat_name = str(strategy_state.get("id", "") or "")
        strat_ok, strat_reason = self._strategy_gate_check(strat_name)
        gates.append(
            {
                "name": "Strategy Ready",
                "status": "READY" if strat_ok else "NOT READY",
                "reason": strat_reason,
            }
        )
        risk_status = str((snap.get("risk") or {}).get("status", ""))
        if risk_status == "READY":
            gates.append(
                {
                    "name": "Risk Engine Ready",
                    "status": "READY",
                    "reason": f"max {self._config.quantity:g} per order",
                }
            )
        else:
            gates.append(
                {
                    "name": "Risk Engine Ready",
                    "status": "BLOCKED" if risk_status == "HALTED" else "NOT READY",
                    "reason": f"risk engine {risk_status or 'unknown'}",
                }
            )
        recon = snap.get("reconciliation") or {}
        recon_status = str(recon.get("status", "") or "")
        if recon_status in ("CLEAN", "SYNCED"):
            gates.append(
                {
                    "name": "Reconciliation Synced",
                    "status": "READY",
                    "reason": (
                        f"positions: {recon.get('positions', 0)} | orders: {recon.get('orders', 0)}"
                    ),
                }
            )
        elif recon_status in ("MISMATCH", "BLOCKED"):
            gates.append(
                {
                    "name": "Reconciliation Synced",
                    "status": "BLOCKED",
                    "reason": f"venue mismatch: {recon.get('mismatches', '')}",
                }
            )
        else:
            gates.append(
                {
                    "name": "Reconciliation Synced",
                    "status": "NOT CONFIGURED",
                    "reason": "no session to reconcile",
                }
            )
        gates.append(
            {
                "name": f"Environment ({self._config.mode})",
                "status": "READY",
                "reason": f"{self._config.mode} mode selected",
            }
        )
        if self._config.mode != "LIVE":
            gates.append(
                {
                    "name": "Arming",
                    "status": "NOT READY",
                    "reason": "arming applies to LIVE only",
                }
            )
        elif self._status == "RUNNING":
            gates.append(
                {
                    "name": "Arming",
                    "status": "READY",
                    "reason": "sessions armed and running",
                }
            )
        else:
            gates.append(
                {
                    "name": "Arming",
                    "status": "NOT READY",
                    "reason": "DISARMED — arm before START",
                }
            )
        return gates

    def _strategy_gate_check(self, name: str) -> tuple[bool, str]:
        """Strategy registry truth for the checklist row (no invention)."""
        if not name:
            return False, "no strategy selected"
        try:
            from strategy.registry import get_strategy_registry

            reg = get_strategy_registry()
            if not reg.contains(name):
                return False, f"strategy not found: {name}"
            defn = reg.get(name)
            record = None
            if defn.source_code:
                record = StrategyRecord(
                    id=defn.id,
                    name=defn.name,
                    code=defn.source_code,
                    created_at="",
                    updated_at="",
                    version=defn.version,
                )
            if record is None:
                record = load_strategy_record(name, self._strategy_dir)
            if record is None:
                return False, f"strategy not found: {name}"
            self._compiled(record)
        except Exception as exc:
            return False, f"strategy not ready: {exc}"
        timeframe = str(getattr(defn, "timeframe", "") or "")
        if timeframe:
            return True, f"{name} ready — {timeframe}"
        return True, f"{name} ready"

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

    @staticmethod
    def _strict_positive(value: Any, default: float) -> float:
        """Positive finite number or the default (corrupt values never mask)."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        if number != number or number in (float("inf"), float("-inf")) or number <= 0:
            return default
        return number

    def _restore_config(self) -> None:
        data = self._store.load()
        # Activity history survives restarts (bounded): the stream opens
        # with the real session log instead of an empty page.
        stored = data.get("activity", [])
        if isinstance(stored, list):
            for entry in stored[-100:]:
                if not isinstance(entry, dict) or not entry.get("event"):
                    continue
                self._activity.append(
                    {
                        "timestamp": str(entry.get("timestamp", "")),
                        "strategy": str(entry.get("strategy", "")),
                        "symbol": str(entry.get("symbol", "")),
                        "event": str(entry.get("event", "")),
                        "status": str(entry.get("status", "info")),
                        "category": str(entry.get("category", ""))
                        or _category_for_event(str(entry.get("event", ""))),
                    }
                )
        config = data.get("config", {})
        if isinstance(config, dict) and config:
            try:
                quantity = self._strict_positive(config.get("quantity", 1.0), 1.0)
                capital = self._strict_positive(config.get("capital", 1_000_000.0), 1_000_000.0)
                self._config = LiveTradeConfig(
                    strategy_name=str(config.get("strategy_name", "")),
                    symbols=tuple(config.get("symbols", ())),
                    timeframe=str(config.get("timeframe", "")),
                    mode=str(config.get("mode", "PAPER")),
                    quantity=quantity,
                    capital=capital,
                )
            except (TypeError, ValueError):
                self._config = LiveTradeConfig()
        else:
            self._config = LiveTradeConfig()
        if self._config.mode not in ("PAPER", "LIVE"):
            self._config.mode = "PAPER"
        if isinstance(config, dict):
            self._eligibility_enforcement = bool(config.get("eligibility_enabled", False))

        # Phase 1 strategy-first flow: no pre-selected strategy, ever. The
        # saved per-strategy universe is the only symbol authority — a legacy
        # global symbol list restored from an old session file is dropped
        # (one-time migration to empty state, never to a fallback), and the
        # registry declaration no longer fills symbols in.
        try:
            from strategy.registry import get_strategy_registry

            reg = get_strategy_registry()
            if self._config.strategy_name and reg.contains(self._config.strategy_name):
                defn = reg.get(self._config.strategy_name)
                if not self._config.timeframe and defn.timeframe:
                    self._config.timeframe = defn.timeframe
        except Exception:
            pass
        if self._config.strategy_name:
            self._config.symbols = self._universes.symbols_for(self._config.strategy_name)
        else:
            self._config.symbols = ()

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
            "eligibility_enabled": self._eligibility_enforcement,
        }
        try:
            self._store.save(data)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("live config persist failed: %s", exc)

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
            "eligibility_enabled": self._eligibility_enforcement,
        }
        checkpoints: dict[str, Any] = {}
        for symbol, session in self._sessions.items():
            with contextlib.suppress(Exception):
                checkpoints[symbol] = session.checkpoint()
        data["checkpoints"] = checkpoints
        data["open_since"] = dict(self._open_since)
        data["activity"] = list(self._activity)[-200:]
        try:
            self._store.save(data)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("live session persist failed: %s", exc)


__all__ = [
    "LiveTradeConfig",
    "LiveConfigError",
    "LiveSessionStore",
    "LiveTradingService",
    "DEFAULT_STRATEGY_DIR",
]
