"""Headless VAYREN backend — native service mode for the Rust UI.

Entry point for running VAYREN business logic (EventBus, market data,
strategies, backtest, execution) without any UI toolkit. Communicates
with the Rust native UI via JSON over stdin/stdout.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from broker import BrokerStatus

if TYPE_CHECKING:
    from app.progress import RunProgress


def _bootstrap_chapter_path() -> None:
    """Ensure sibling chapter packages resolve regardless of cwd.

    ``python -m app.headless`` only guarantees ``app`` itself is importable
    (cwd or an editable install). The composition root needs every domain
    package, so ``src/`` is added to ``sys.path`` when it is missing.
    Fail-closed: an unresolvable layout keeps the original path untouched.
    """
    try:
        anchor = Path(__file__).resolve()
        repo_root = anchor.parents[2]  # src/app/headless.py → repo root
        src = repo_root / "src"
        if src.is_dir() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
    except Exception as exc:  # noqa: BLE001
        # logging-only: logger is defined below, so use the module directly.
        logging.getLogger(__name__).warning("src path bootstrap failed: %s", exc)


_bootstrap_chapter_path()

logger = getLogger(__name__)

#: ids of the log handlers this process installed itself. Only these are
#: recycled on reconfigure — an imported SDK's handlers are left alone.
_OWNED_HANDLER_IDS: set[int] = set()


def _logs_to_stderr(level: str) -> None:
    """Route all logs to stderr — stdout carries ONLY protocol JSON.

    ``core.logger`` was removed in the Rust-owned core cleanup, so fall back
    to stdlib ``basicConfig`` when the shared helper is gone. Either way, any
    stdout handler is then moved to stderr — stdout carries ONLY protocol
    JSON, never log text.
    """
    try:
        from core.logger import configure_logging  # pyright: ignore[reportMissingImports]

        configure_logging(level)
    except ImportError:
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    root = logging.getLogger()
    for handler in list(root.handlers):
        # Only our own stderr handler is recycled; SDK-installed handlers
        # (venue clients, drivers) are left exactly as imported code left them.
        if id(handler) in _OWNED_HANDLER_IDS:
            root.removeHandler(handler)
            _OWNED_HANDLER_IDS.discard(id(handler))
            with suppress(Exception):
                handler.close()
    # Windowless launches (pythonw / CREATE_NO_WINDOW) have no console: a
    # broken stderr must degrade to silence, never kill the backend — the
    # JSON protocol on stdout stays the single source of truth either way.
    try:
        sys.stderr.write("")
        sys.stderr.flush()
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    _OWNED_HANDLER_IDS.add(id(handler))
    root.addHandler(handler)


def _emit(payload: dict) -> bool:
    """Write one JSON response line. False when the pipe is gone.

    UTF-8 is forced on BOTH ends so the rupee sign, the bullets and the
    glyphs the UI draws survive the pipe as themselves instead of arriving
    as escape soup a downstream reader has to guess about (``ensure_ascii``
    alone would be safe, but a reconfigured stdout encoding could still
    transcode the line to cp1252 on the way out, which is exactly how
    ``₹`` becomes ``?`` in the footer). ``ensure_ascii=False`` keeps the
    payload human-readable on the wire; the Rust bridge decodes UTF-8.
    """
    try:
        line = json.dumps(payload, ensure_ascii=False)
        reconfig = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfig):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                reconfig(encoding="utf-8", errors="replace")
        print(line, flush=True)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _bar_to_dict(bar) -> dict:
    """One backend Bar as bridge JSON (mirrors Rust MarketBar fields)."""

    def _get(name: str):
        if isinstance(bar, dict) and name in bar:
            return bar[name]
        return getattr(bar, name)

    return {
        "time": _get("timestamp"),
        "open": _get("open"),
        "high": _get("high"),
        "low": _get("low"),
        "close": _get("close"),
        "volume": _get("volume"),
    }


def _market_repository(data_dir: str | Path) -> Any | None:
    """Resolve the canonical market-data service, or None when unavailable.

    The single OHLCV read path for Chart and Strategy Lab
    (``app.services.market_data_service``). A missing/unreadable store
    fails closed to honest-empty snapshots — never invented bars.
    """
    try:
        from app.services.market_data_service import MarketDataService
    except ImportError as exc:
        logger.error("Market backend unavailable: %s", exc)
        return None
    try:
        return MarketDataService(data_dir)
    except Exception as exc:  # noqa: BLE001
        logger.error("Market backend unavailable: %s", exc)
        return None


def _empty_market_snapshot(notice: str = "", strategies: list[str] | None = None) -> dict:
    """Honest-empty market snapshot (never invented bars)."""
    return {
        "symbols": [],
        "selected_symbol": "",
        "timeframes": [],
        "timeframe": "",
        "exchange": "",
        "bars": [],
        "strategies": list(strategies or []),
        "notice": notice,
    }


def _market_snapshot(repository: Any, command: dict, strategy_dir: str) -> dict:
    """Build the native Market snapshot from real SQLite data.

    Single round-trip feeding Rust ``apply_snapshot_json``: watchlist rows
    with live quotes, the selected symbol's bars (base or kernel-aggregated
    timeframe), the detected timeframe ladder, and the strategy library names
    the chart's INDICATORS popup lists under STRATEGIES. Honest emptiness with
    an actionable ``notice`` when the store has nothing — never invented bars.
    """
    strategies = _strategy_names(strategy_dir)
    if repository is None:
        return _empty_market_snapshot("Data directory not found or unreadable", strategies)
    symbols = repository.list_symbols()
    if not symbols:
        return _empty_market_snapshot("No symbols discovered", strategies)
    requested = (command.get("symbol") or "").strip().upper()
    if requested and requested not in symbols:
        notice = f"Unknown symbol {requested} ({len(symbols)} symbols available)"
        return {
            **_empty_market_snapshot(notice, strategies),
            "selected_symbol": requested,
        }
    symbol = requested or symbols[0]
    limit = command.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit < 0:
            limit = None
    try:
        timeframes = list(repository.available_timeframes(symbol))
    except Exception as exc:  # noqa: BLE001
        return {
            **_empty_market_snapshot(str(exc)),
            "selected_symbol": symbol,
        }
    timeframe = (command.get("timeframe") or "").strip() or repository.base_timeframe(symbol)
    if timeframe not in timeframes:
        notice = (
            f"Unsupported timeframe {timeframe} for {symbol} (available: {', '.join(timeframes)})"
        )
        quotes = repository.get_quotes(symbols)
        return {
            "symbols": [
                {"symbol": q.symbol, "price": q.price, "change_pct": q.change_pct} for q in quotes
            ],
            "selected_symbol": symbol,
            "timeframes": timeframes,
            "timeframe": timeframe,
            "exchange": "",
            "bars": [],
            "strategies": strategies,
            "notice": notice,
        }
    quotes = repository.get_quotes(symbols)
    try:
        bars = repository.get_bars(symbol, timeframe, limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "symbols": [
                {"symbol": q.symbol, "price": q.price, "change_pct": q.change_pct} for q in quotes
            ],
            "selected_symbol": symbol,
            "timeframes": timeframes,
            "timeframe": timeframe,
            "exchange": "",
            "bars": [],
            "strategies": strategies,
            "notice": str(exc),
        }
    return {
        "symbols": [
            {"symbol": q.symbol, "price": q.price, "change_pct": q.change_pct} for q in quotes
        ],
        "selected_symbol": symbol,
        "timeframes": timeframes,
        "timeframe": timeframe,
        "exchange": "",
        "bars": [_bar_to_dict(bar) for bar in bars],
        "strategies": strategies,
        "notice": "",
    }


def _credential_field_rows(record: dict, saved_keys: set | None = None) -> list:
    """Venue credential shapes for the form (shapes only, never values).

    Same hint-only placeholders the legacy bootstrap used; the Rust side also
    falls back to "Enter {label}" when a placeholder is absent.

    ``saved_keys`` is the set of credential keys that already have a persisted
    value in the OS vault.  A field marked ``saved: True`` tells the UI to show
    a "saved" indicator so the user knows they do not need to re-enter.  Actual
    secret values are never included.
    """
    schema = record.get("credential_schema")
    saved = saved_keys or set()
    rows = []
    if not isinstance(schema, (list, tuple)):
        return rows
    display = str(record.get("name", "") or "").upper()
    for field in schema:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key", "") or "")
        if not key:
            continue
        label = str(field.get("label", "") or key)
        if key == "pin":
            placeholder = "Enter 4-digit PIN"
        elif key == "app_id" and display:
            placeholder = f"Enter {display} App ID"
        elif key == "secret" and display:
            placeholder = f"Enter {display} Secret ID"
        elif key == "client_id":
            placeholder = "Enter your Client ID"
        else:
            placeholder = f"Enter {label}"
        rows.append(
            {
                "key": key,
                "label": label,
                "placeholder": placeholder,
                "secret": bool(field.get("secret", False)),
                "required": bool(field.get("required", False)),
                "saved": key in saved,
            }
        )
    return rows


def _system_snapshot(
    data_dir: str,
    selected_broker: str | None = None,
    manager: Any | None = None,
) -> dict:
    """Build the native System workspace snapshot from the broker manager.

    Mirrors the legacy bootstrap provider: authoritative selection + manager
    snapshot + read-only startup session checks. Fail-closed honest-empty
    when the broker stack cannot load.
    """
    try:
        import broker.providers  # noqa: F401
        from app.services.broker_manager import BrokerManager
        from app.services.broker_selection_service import (
            BrokerSelectionService,
            app_selection_store,
        )
    except Exception as exc:  # noqa: BLE001
        return {"brokers": [], "error": f"system backend unavailable: {exc}"}
    try:
        selection_service = BrokerSelectionService(app_selection_store(data_dir))
        if selected_broker:
            from broker import default_registry

            known = {record.name for record in default_registry().list()}
            current_name = ""
            try:
                current = selection_service.current_or_none()
                current_name = current.name if current is not None else ""
            except Exception:  # noqa: BLE001
                current_name = ""
            if selected_broker in known and selected_broker != current_name:
                with contextlib.suppress(Exception):
                    selection_service.select(selected_broker)
        selection = selection_service.current()
    except Exception:  # noqa: BLE001
        selection = None

    if manager is None:
        manager = BrokerManager(data_dir=data_dir)
        try:
            snap = manager.snapshot()
        finally:
            manager.stop_worker()
    else:
        snap = manager.snapshot()

    selected_id = selected_broker or getattr(selection, "name", "") or ""
    brokers = []
    for entry in snap.get("brokers", []):
        if not isinstance(entry, dict):
            continue
        broker_id = str(entry.get("id", "") or "")
        if not broker_id:
            continue
        brokers.append(
            {
                "id": broker_id,
                "display_name": str(entry.get("name", "") or broker_id),
                "venue_subtitle": str(entry.get("venue_subtitle", "") or ""),
                "status": str(entry.get("status", "") or ""),
                "selected": broker_id == selected_id,
            }
        )
    full = next(
        (e for e in snap.get("brokers", []) if isinstance(e, dict) and e.get("id") == selected_id),
        {},
    )
    record = next((b for b in brokers if b["id"] == selected_id), None)
    environment = ""
    if selection is not None:
        try:
            environment = selection.environment.value
        except Exception:  # noqa: BLE001
            environment = ""
    # Detect which credential keys are already saved in the OS vault so the
    # UI can show a "saved" indicator without ever echoing the actual values.
    saved_keys: set = set()
    try:
        from broker.common.credentials_store import default_store, provider_service

        vault_store = default_store(data_dir)
        stored = vault_store.load(provider_service(selected_id)) if selected_id else None
        if isinstance(stored, dict):
            saved_keys = {k for k, v in stored.items() if isinstance(v, str) and v.strip()}
    except Exception:  # noqa: BLE001
        pass
    return {
        "brokers": brokers,
        "selected_id": selected_id,
        "display_name": record["display_name"] if record else "",
        "venue_subtitle": record["venue_subtitle"] if record else "",
        "environment": environment,
        "status_raw": str(full.get("status", "") or ""),
        "configured": bool(full.get("configured", False)),
        "can_login": bool(full.get("can_login", False)),
        "can_disconnect": bool(full.get("can_disconnect", False)),
        "reason": str(full.get("reason", "") or ""),
        "credential_fields": _credential_field_rows(full, saved_keys),
    }


_LIVE_ACTION_ERRORS: dict[str, str] = {}


def _live_action(
    data_dir: str, strategy_dir: str, action: dict, broker_manager: Any = None
) -> dict:
    """Apply one UI action to the live service and return the fresh snapshot.

    The native Live view is HOST-MODE: the buttons never mutate state in Rust,
    they queue an action (`crates/vayren-domain/src/live.rs::take_action`) that
    the shell forwards here. This is the only place those actions land, so each
    one is applied to the real service and the resulting snapshot comes straight
    back — the screen never shows an action as taken until the service agrees.

    A refused action is NOT an error: `start` returns `(ok, reasons)` and the
    reasons are carried in the snapshot, so a blocked START explains itself
    instead of failing silently. An unexpected fault is reported the same way,
    never raised into the command loop.
    """
    name = str(action.get("action") or "").strip().lower()
    try:
        service = _live_service(data_dir, strategy_dir, broker_manager)
        if service is None:
            return _live_unavailable("trading backend unavailable")
        if name in ("setup", "mode") and getattr(service, "status", "") == "RUNNING":
            # The service freezes setup while a session runs (configure()
            # raises there); refusing here keeps the honest RUNNING snapshot
            # instead of turning the page into an error over one edit.
            snapshot = _trading_service_snapshot(data_dir, strategy_dir, broker_manager)
            snapshot["action_note"] = "setup is frozen while a session runs — stop first"
            return snapshot
        note = ""
        if name == "setup":
            symbols = action.get("symbols")
            # Rust sends `strategy_name` (live.rs::push_setup_action); accept
            # the legacy `strategy` key too — reading only one silently
            # dropped every strategy pick while symbols/timeframe applied.
            service.configure(
                strategy_name=_opt_str(action.get("strategy_name", action.get("strategy"))),
                symbols=tuple(str(s) for s in symbols) if isinstance(symbols, list) else None,
                timeframe=_opt_str(action.get("timeframe")),
                mode=_opt_str(action.get("mode")),
                quantity=_opt_float(action.get("quantity")),
            )
            note = "setup updated — readiness re-evaluated"
            # Consent binds to the reviewed setup: any setup edit voids it.
            _ARMED[0] = False
        elif name == "mode":
            requested = _opt_str(action.get("mode"))
            service.configure(mode=requested)
            # A mode switch voids any earlier consent (fresh ARM per setup).
            _ARMED[0] = False
            if requested is not None and requested not in ("PAPER", "LIVE"):
                # configure() only accepts PAPER/LIVE; saying "mode set to
                # PAPER" here posed as an accepted SANDBOX switch.
                note = f"mode {requested} not supported — mode stays {service.config.mode}"
            else:
                note = f"mode set to {service.config.mode} — see Live Readiness"
        elif name == "start":
            confirmed = bool(action.get("confirmed")) or _ARMED[0]
            ok, reasons = service.start(confirmed=confirmed)
            if ok:
                _ARMED[0] = False
                _LIVE_ACTION_ERRORS.pop("start", None)
            else:
                _LIVE_ACTION_ERRORS["start"] = "; ".join(reasons)
        elif name == "stop":
            service.stop()
            _LIVE_ACTION_ERRORS.pop("start", None)
            # A finished run voids consent: the next START needs a fresh ARM.
            _ARMED[0] = False
        elif name == "halt":
            # HALT is the emergency stop: same teardown as STOP, but the reason
            # is recorded so the journal and the UI say a halt happened rather
            # than an ordinary stop.
            service.stop(reason="operator halt")
            _LIVE_ACTION_ERRORS.pop("start", None)
            _ARMED[0] = False
        elif name == "arm":
            # Arming is the operator's LIVE consent; it is carried on the next
            # START rather than being a session of its own.
            _ARMED[0] = True
        elif name == "select_symbol":
            sym = str(action.get("symbol") or "")
            if hasattr(service, "select_symbol"):
                service.select_symbol(sym)
        elif name == "tick":
            service.tick()
        elif name == "navigate":
            # Host-owned navigation intent (CONFIGURE BROKER -> BROKERS). It
            # changes no trading state, so the honest answer is the CURRENT
            # live snapshot. Falling through to `unknown live action` returned
            # `_live_unavailable(...)`, whose blank degraded snapshot wiped
            # every panel on the Live screen on one click.
            return _trading_service_snapshot(data_dir, strategy_dir, broker_manager)
        else:
            return _live_unavailable(f"unknown live action {name!r}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live action %r failed: %s", name, exc)
        snapshot = _live_unavailable(f"{name} failed: {exc}")
        snapshot["session_status"] = "STOPPED"
        return snapshot
    snapshot = _trading_service_snapshot(data_dir, strategy_dir, broker_manager)
    reason = _LIVE_ACTION_ERRORS.get("start")
    if reason and snapshot.get("session_status") != "RUNNING":
        snapshot["status_reason"] = reason
    if _ARMED[0] and snapshot.get("session_status") != "RUNNING":
        snapshot["action_note"] = "armed — confirm LIVE consent on START"
        # The service cannot see the headless ARM flag, so its validate()
        # still lists the consent line. The ARM ceremony satisfied it, so the
        # snapshot drops exactly those lines — START enables and the next
        # start() carries confirmed=True (see service.start).
        snapshot["start_blockers"] = [
            b
            for b in snapshot.get("start_blockers", [])
            if "confirmation" not in b and "consent" not in b
        ]
    elif note:
        # The backend confirms what it just accepted, so a request note
        # ("mode requested — awaiting backend") never sticks around.
        snapshot["action_note"] = note
    return snapshot


#: Operator LIVE consent, consumed by the next START.
_ARMED: list[bool] = [False]


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _live_service(data_dir: str, strategy_dir: str, broker_manager: Any = None) -> Any:
    """The cached idle-or-running live service, or ``None`` if unavailable.

    The SYSTEM workspace owns broker auth; LIVE only consumes it, so the
    loop's manager (plus a fresh selection read) is attached on EVERY call —
    connects/disconnects in SYSTEM → BROKERS show up on the LIVE tab within
    one poll instead of freezing at construction time.
    """
    try:
        from app.services.live_trading_service import LiveTradingService
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live service unavailable: %s", exc)
        return None
    key = (str(data_dir), str(strategy_dir))
    service = _TRADING_SERVICES.get(key)
    if service is None:
        try:
            service = LiveTradingService(data_dir=data_dir, strategy_dir=strategy_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live service construction failed: %s", exc)
            return None
        _TRADING_SERVICES[key] = service
        if len(_TRADING_SERVICES) > 4:
            _TRADING_SERVICES.pop(next(iter(_TRADING_SERVICES)))
    if broker_manager is not None:
        with contextlib.suppress(Exception):
            service.attach_broker_view(broker_manager, _live_selection(data_dir))
    return service


def _live_selection(data_dir: str) -> Any:
    """Fresh broker selection read (tiny store file; always current)."""
    try:
        from app.services.broker_selection_service import (
            BrokerSelectionService,
            app_selection_store,
        )

        return BrokerSelectionService(app_selection_store(data_dir))
    except Exception:  # noqa: BLE001
        return None


def _trading_service_snapshot(data_dir: str, strategy_dir: str, broker_manager: Any = None) -> dict:
    """One idle-service snapshot feeding Portfolio + Live screens.

    A fresh service (never auto-starts): honest not-running book — empty
    positions/orders, PAPER mode, real blockers. The Rust sides render
    exactly this shape (same dict the legacy hosts consumed). Bar objects are
    converted to bridge dicts (JSON cannot carry them).

    The service object itself is CACHED per (data_dir, strategy_dir): the
    constructor resolves the strategy registry and lists the strategy dir on
    every call, and an idle service holds no live state, so rebuilding it per
    snapshot command was pure cost with zero information gain.     ``snapshot()``
    still re-reads its session store each call, so state changes are visible.
    The loop's broker manager rides along (SYSTEM owns auth; LIVE consumes),
    so connects/disconnects surface within one poll.
    """
    service = _live_service(data_dir, strategy_dir, broker_manager)
    if service is None:
        return _live_unavailable("trading backend unavailable")
    try:
        snap = service.snapshot()
    except Exception as exc:  # noqa: BLE001
        return _live_unavailable(f"trading snapshot failed: {exc}")
    bars = snap.get("market_bars")
    if bars:
        # The LIVE view has no chart, so shipping the whole 500-bar series
        # every second was pure cost: JSON encode, pipe, parse and a Slint
        # model per candle, all to draw nothing. The one fact the screen still
        # shows is the last close, so that is what travels — plus the real
        # total in `market_bar_count`, so "N bars" stays truthful instead of
        # becoming "1 bar".
        snap["market_bar_count"] = len(bars)
        snap["market_bars"] = [_bar_to_dict(bars[-1])]
    else:
        snap["market_bars"] = []
        snap["market_bar_count"] = 0
    return snap


def _live_unavailable(reason: str) -> dict:
    """The honest shape when the live trading backend cannot be built.

    A dead LIVE page that shows only "NO DATA" is the worst outcome: the
    screen says something is wrong and refuses to say what. The reason is
    repeated into `status_reason`, which the native Live view already renders
    as its own visible line (see `vayren-domain::live` `status_reason`).
    Never a fabricated book, never a silently empty page.
    """
    return {
        "mode": "PAPER",
        "error": reason,
        "status_reason": reason,
        "status": "STOPPED",
        "lifecycle": "unavailable",
        "broker": {},
        "positions": [],
        "orders": [],
        "fills": [],
        "events": [],
        "watchlist": [],
        "market_bars": [],
        "configured": False,
    }


_TRADING_SERVICES: dict[tuple[str, str], Any] = {}


def _portfolio_snapshot(data_dir: str, strategy_dir: str) -> dict:
    """Build the native Portfolio snapshot from the trading service."""
    return _trading_service_snapshot(data_dir, strategy_dir)


_LAB_ROWS_CACHE: dict[str, tuple[tuple, list[dict]]] = {}
_DESCRIBE_CACHE: dict[tuple, dict] = {}
_LAST_SENT_CODE: dict[tuple, str] = {}


def _library_signature(strategy_dir: str) -> tuple:
    """Cheap on-disk signature of the strategy library (one scandir total).

    Directory mtime alone does not change on file EDITS, and row `modified`
    stamps come from file mtimes, so the signature is the sorted
    (name, mtime) pairs of one directory scan plus the legacy OBR file.
    Rebuilding `_lab_library_rows` per snapshot command used to glob + stat
    every file + walk the registry every time; now the rebuild happens only
    when the on-disk facts changed.
    """
    legacy_dir = Path(r"D:\VAYREN_STRATEGIES")
    target_dir = strategy_dir
    if (not target_dir or not Path(target_dir).is_dir()) and legacy_dir.is_dir():
        target_dir = str(legacy_dir)
    entries: list[tuple[str, float]] = []
    try:
        if target_dir:
            with os.scandir(target_dir) as it:
                for entry in it:
                    try:
                        if entry.name.endswith(".py"):
                            entries.append((entry.name, entry.stat().st_mtime))
                    except OSError:
                        entries.append((entry.name, -1.0))
    except OSError:
        entries.append(("<?unreadable>", -1.0))
    try:
        legacy_obr = (legacy_dir / "OBR.py").stat().st_mtime if legacy_dir.is_dir() else -1.0
    except OSError:
        legacy_obr = -1.0
    return (target_dir, tuple(sorted(entries)), legacy_obr)


def _lab_library_rows(strategy_dir: str) -> list[dict]:
    """Cached accessor for `_build_lab_library_rows` (see its docstring)."""
    signature = _library_signature(strategy_dir)
    cached = _LAB_ROWS_CACHE.get(strategy_dir)
    if cached is not None and cached[0] == signature:
        return [dict(row) for row in cached[1]]
    rows = _build_lab_library_rows(strategy_dir)
    _LAB_ROWS_CACHE[strategy_dir] = (signature, rows)
    if len(_LAB_ROWS_CACHE) > 4:
        _LAB_ROWS_CACHE.pop(next(iter(_LAB_ROWS_CACHE)))
    return [dict(row) for row in rows]


def _build_lab_library_rows(strategy_dir: str) -> list[dict]:
    """Strategy library rows: authoritative registry, library files, marked built-ins."""
    try:
        from strategy import builtins
        from strategy.language.storage import list_strategies
        from strategy.registry import get_strategy_registry
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"lab backend unavailable: {exc}") from exc
    registry = get_strategy_registry()
    rows: list[dict] = []
    known: set[str] = set()

    # 1. Registered strategies (single authority, e.g. OBR C1C4)
    for defn in registry.list():
        sym_count_str = f"{len(defn.symbols)} symbols" if defn.symbols else ""
        tags = [t for t in (defn.timeframe, defn.direction, sym_count_str) if t]
        rows.append(
            {
                "name": defn.name,
                "description": defn.description
                or (f"{defn.timeframe} • {defn.direction}" if defn.timeframe else ""),
                "tags": tags,
                "version": defn.version,
                "modified": "registered",
                "last_backtest": "",
                "favorite": False,
                "status": defn.status,
                "timeframe": defn.timeframe,
                "direction": defn.direction,
                "symbol_count": sym_count_str,
                "runtime_state": defn.runtime_state,
                "configured_symbols": list(defn.symbols),
                "metadata": defn.metadata_dict,
            }
        )
        known.add(defn.name.lower())

    # 2. File-backed library strategies (e.g. OBR)
    target_dir = strategy_dir
    legacy_dir = Path(r"D:\VAYREN_STRATEGIES")
    if (not target_dir or not Path(target_dir).is_dir()) and legacy_dir.is_dir():
        target_dir = str(legacy_dir)

    try:
        file_names = [str(n) for n in (list_strategies(target_dir) or [])]
    except Exception:  # noqa: BLE001
        file_names = []

    if "OBR" not in file_names and legacy_dir.is_dir() and (legacy_dir / "OBR.py").is_file():
        file_names.append("OBR")

    for name in file_names:
        if name.lower() in known:
            continue
        modified = ""
        try:
            p = Path(target_dir, f"{name}.py")
            if not p.is_file() and legacy_dir.is_dir():
                p = legacy_dir / f"{name}.py"
            stamp = p.stat().st_mtime if p.is_file() else None
            if stamp:
                from datetime import UTC, datetime

                modified = datetime.fromtimestamp(stamp, tz=UTC).strftime("%d %b %y")
        except Exception:  # noqa: BLE001
            modified = ""
        is_obr = name.upper() == "OBR"
        desc = "Opening Range Breakout" if is_obr else ""
        tags = ["BREAKOUT", "INTRADAY"] if is_obr else []
        rows.append(
            {
                "name": name,
                "description": desc,
                "tags": tags,
                "version": "1.0" if is_obr else "",
                "modified": modified or ("11 Sep 26" if is_obr else ""),
                "last_backtest": "—",
                "favorite": is_obr,
                "status": "ACTIVE",
                "timeframe": "15m" if is_obr else "",
                "direction": "SHORT" if is_obr else "",
                "symbol_count": "",
                "runtime_state": "IDLE",
                "configured_symbols": [],
                "metadata": {},
            }
        )
        known.add(name.lower())

    # 3. Built-in strategies (SMA Crossover, EMA Crossover, RSI Strategy)
    for entry in builtins.list_builtins():
        if entry.name.lower() in known:
            continue
        rows.append(
            {
                "name": entry.name,
                "description": entry.description,
                "tags": list(entry.tags),
                "version": entry.version,
                "modified": "built-in",
                "last_backtest": "—",
                "favorite": False,
                "status": "ACTIVE",
                "timeframe": "",
                "direction": "",
                "symbol_count": "",
                "runtime_state": "IDLE",
                "configured_symbols": [],
                "metadata": {},
            }
        )
        known.add(entry.name.lower())

    return rows


_LAB_DEFAULT_CAPITAL = 10000.0
_BACKEND_VERSION = "1.18.0"
_STRATEGY_NAMES_LAST_WARN = 0.0


def _strategy_names(strategy_dir: str) -> list[str]:
    """Strategy library names shared by the Lab library and the chart popup.

    One source for both workspaces, so the INDICATORS popup's STRATEGIES
    section names exactly the strategies the Lab lists. Honest-empty on
    failure — never invented names. Failures warn at most once a minute so
    a broken backend does not spam one line per snapshot.
    """
    try:
        return [str(row["name"]) for row in _lab_library_rows(strategy_dir)]
    except Exception as exc:  # noqa: BLE001
        global _STRATEGY_NAMES_LAST_WARN
        now = time.monotonic()
        if now - _STRATEGY_NAMES_LAST_WARN >= 60.0:
            _STRATEGY_NAMES_LAST_WARN = now
            logger.warning("Strategy names unavailable: %s", exc)
        return []


def _lab_universe(repository: Any) -> tuple[list[str], str]:
    """Canonical universe symbols (same market-data service as Chart)."""
    if repository is None:
        return [], "Data directory not found or unreadable"
    try:
        return repository.list_symbols(), ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


# ── live run control ───────────────────────────────────────────────────────
# A 500+ symbol run blocks the command loop for minutes, so CANCEL and the
# progress stream need their own channel. The flag is a plain Event: setting it
# can only ever make the run stop EARLIER, never change a result.
#
# stdin has exactly ONE reader for the process lifetime (a daemon feeding
# _LAB_INBOX). Two iterators over sys.stdin split buffered chunks
# unpredictably, so the run executes in a worker thread while the main loop
# keeps pumping the same inbox: cancel sets the flag, anything else is parked
# in _LAB_DEFERRED and re-queued in order when the run finishes.
_LAB_CANCEL = threading.Event()
_LAB_DEFERRED: deque[dict] = deque(maxlen=200)
_LAB_DEFERRED_DROPPED = 0
_LAB_INBOX: queue.Queue = queue.Queue()
_LAB_READER_STARTED = False
_LAB_EOF: Any = object()
_LAB_MISS: Any = object()


def _progress_emitter():
    """Write one `lab_progress` line per real event, straight to stdout."""

    def emit(event: dict) -> None:
        payload = {"type": "lab_progress", "data": event}
        try:
            reconfig = getattr(sys.stdout, "reconfigure", None)
            if callable(reconfig):
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    reconfig(encoding="utf-8", errors="replace")
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except (OSError, ValueError, TypeError):
            pass

    return emit


def _watch_for_cancel(_stdin) -> threading.Thread:
    """Legacy entry point kept for import compatibility; the inbox reader
    supersedes it (single stdin reader, no second iterator)."""
    logger.warning("_watch_for_cancel is deprecated; stdin uses the shared inbox reader")
    thread = threading.Thread(target=lambda: None, name="lab-cancel-watch", daemon=True)
    thread.start()
    return thread


def _ensure_stdin_reader() -> None:
    """Start the single lifetime stdin reader feeding _LAB_INBOX."""
    global _LAB_READER_STARTED
    if _LAB_READER_STARTED:
        return
    _LAB_READER_STARTED = True

    def _read() -> None:
        try:
            for raw in sys.stdin:
                _LAB_INBOX.put(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stdin reader ended: %s", exc)
        finally:
            _LAB_INBOX.put(_LAB_EOF)

    thread = threading.Thread(target=_read, name="stdin-reader", daemon=True)
    thread.start()


def _next_line(timeout: float | None = None) -> Any:
    """Next stdin line from the shared inbox (None timeout = block forever)."""
    try:
        item = _LAB_INBOX.get(timeout=timeout)
    except queue.Empty:
        return _LAB_MISS
    return item


def _pump_while_running(done: threading.Event) -> None:
    """Park inbox traffic while a run owns the worker: cancel sets the flag,
    everything else waits its turn in _LAB_DEFERRED."""
    while not done.is_set():
        line = _next_line(timeout=0.1)
        if line is _LAB_MISS:
            continue
        if line is _LAB_EOF:
            _LAB_INBOX.put(_LAB_EOF)
            return
        text = line.strip() if isinstance(line, str) else ""
        if not text:
            continue
        try:
            cmd = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(cmd, dict) and cmd.get("type") == "cancel_backtest":
            _LAB_CANCEL.set()
            continue
        if isinstance(cmd, dict):
            global _LAB_DEFERRED_DROPPED
            maxlen = _LAB_DEFERRED.maxlen
            if maxlen is not None and len(_LAB_DEFERRED) >= maxlen:
                _LAB_DEFERRED_DROPPED += 1
                logger.warning(
                    "lab deferred queue full (200); dropping oldest (%d dropped)",
                    _LAB_DEFERRED_DROPPED,
                )
            _LAB_DEFERRED.append(cmd)


def _requeue_deferred() -> None:
    """Return parked commands to the inbox in arrival order."""
    while _LAB_DEFERRED:
        _LAB_INBOX.put(json.dumps(_LAB_DEFERRED.popleft()))


# Coverage probes the ANCHOR symbol's bar count plus the selected symbols'
# history bounds. Counting every bar of a 527-symbol universe is a full scan of
# the store, so the default probe is bounded and says so; a full scan only
# happens on an explicit request. The percentage itself is computed by the
# Rust kernel (``lab_coverage``) — this side only counts real rows.
_LAB_COVERAGE_SAMPLE = 24


def _lab_coverage(
    repository: Any,
    symbols: list[str],
    start: str,
    end: str,
    timeframe: str,
    full: bool = False,
) -> dict | None:
    """Measured data-completeness counts for the Lab config strip.

    Returns only counts this process actually read from the store: how many
    selected symbols reach back to ``start`` and how many bars the anchor
    symbol holds inside the window. No percentage, no expectation — the Rust
    kernel owns that math, and an unmeasurable probe reports
    ``bars_expected = 0`` so the UI hides the strip instead of painting a 0%.
    """
    if repository is None or not start or not end:
        return None
    selected = [s for s in symbols if s]
    if not selected:
        return None
    probed = selected if full else selected[:_LAB_COVERAGE_SAMPLE]
    covering = 0
    for symbol in probed:
        try:
            first, _last = repository.date_range(symbol)
        except Exception:  # noqa: BLE001
            continue
        # "Covers the window" means it has AT LEAST ONE bar inside it. A stock
        # listed in 2021 is not a defect when the window starts in 2019, so
        # requiring the full history here would warn about nothing.
        if first and first <= end:
            covering += 1
    try:
        present = len(repository.get_bars(probed[0], timeframe, start=start, end=end))
    except Exception:  # noqa: BLE001
        present = 0
    # The window, the timeframe and the ANCHOR travel with the counts: the Rust
    # kernel derives the expected bar count from the window, and the screen can
    # only name the symbol the bar count actually belongs to.
    return {
        "symbols_total": len(selected),
        "symbols_covering": covering,
        "symbols_probed": len(probed),
        "anchor": probed[0],
        "bars_present": present,
        "start": start,
        "end": end,
        "timeframe": timeframe,
        "sampled": len(probed) < len(selected),
    }


def _lab_workspace(
    strategy_dir: str,
    command: dict,
    rows: list[dict] | None = None,
    repository: Any = None,
) -> dict:
    """Workspace detail for the selected strategy (parity keys for LabState).

    Selection, universe, timeframe, dates and capital resolve here so the
    native workspace opens populated; failures carry ``config_error``
    instead of silent defaults.
    """
    rows = rows if rows is not None else _lab_library_rows(strategy_dir)
    names = [row["name"] for row in rows]
    requested = str(command.get("strategy") or command.get("selected_name") or "").strip()
    if requested in names:
        selected = requested
    elif requested:
        selected = next((n for n in names if n.lower() == requested.lower()), "")
    else:
        selected = names[0] if names else ""

    from strategy.registry import get_strategy_registry

    registry = get_strategy_registry()
    strat_def = registry.get(selected) if (selected and registry.contains(selected)) else None

    # Universe: the selected strategy's configured symbols are the authority
    if strat_def and strat_def.symbols:
        universe_symbols = list(strat_def.symbols)
        universe_error = ""
    else:
        universe_symbols, universe_error = _lab_universe(repository)

    universe_upper = {str(s).strip().upper() for s in universe_symbols}
    universe_clean = {s.split(":")[-1] for s in universe_upper}
    requested_symbols = command.get("symbols") or []
    if isinstance(requested_symbols, str):
        requested_symbols = [s.strip() for s in requested_symbols.split(",") if s.strip()]
    normalized = [str(s).strip().upper() for s in requested_symbols if str(s).strip()]

    def _in_universe(sym: str) -> bool:
        return sym in universe_upper or sym.split(":")[-1] in universe_clean

    dropped = sorted({s for s in normalized if not _in_universe(s)})
    selected_symbols = [s for s in normalized if _in_universe(s)]
    # No silent fallback to the full universe: an explicitly empty (or
    # fully stale) selection fails actionably downstream ("No universe")
    # instead of running hundreds of unasked symbols. Pinned by
    # test_lab_run_applies_full_command_config.

    if universe_symbols and repository is not None:
        try:
            anchor_raw = selected_symbols[0] if selected_symbols else universe_symbols[0]
            anchor = anchor_raw.split(":")[-1]
            timeframes = list(repository.available_timeframes(anchor))
            first_date, last_date = repository.date_range(anchor)
        except Exception:  # noqa: BLE001
            timeframes, first_date, last_date = [], "", ""
    else:
        timeframes, first_date, last_date = [], "", ""
    timeframe = str(command.get("timeframe") or "").strip()
    strat_tf = strat_def.timeframe if strat_def and strat_def.timeframe else ""
    if not timeframe and strat_tf:
        timeframe = strat_tf
    asked_timeframe = timeframe
    if timeframe not in timeframes:
        if strat_tf:
            timeframe = strat_tf
        else:
            timeframe = "15m" if "15m" in timeframes else (timeframes[0] if timeframes else "")
    dates_start = str(command.get("start") or command.get("dates_start") or "").strip()
    dates_end = str(command.get("end") or command.get("dates_end") or "").strip()
    if not dates_start:
        dates_start = first_date
    if not dates_end:
        dates_end = last_date
    capital_raw = command.get("capital", _LAB_DEFAULT_CAPITAL)
    try:
        capital_value = float(capital_raw)
    except (TypeError, ValueError):
        capital_value = float("nan")
    capital_error = ""
    if (
        capital_value != capital_value
        or capital_value in (float("inf"), float("-inf"))
        or capital_value <= 0
    ):
        capital_error = f"Invalid capital {capital_raw!r} — must be a positive number"
        capital_value = _LAB_DEFAULT_CAPITAL
    strat_dir = strat_def.direction.lower() if (strat_def and strat_def.direction) else ""
    default_mode = "sell" if strat_dir == "short" else "buy"
    mode = str(command.get("mode") or default_mode).strip().lower()
    if mode not in ("buy", "sell", "compare"):
        mode = default_mode
    # Real store bounds of the ANCHOR symbol. The Lab's MAX date-range preset
    # is exactly this pair, so it needs the store's own facts, not the user's
    # current selection (which would collapse MAX onto whatever was picked).
    data_bounds: dict = {"first": first_date, "last": last_date} if first_date and last_date else {}
    notes = [universe_error] if universe_error else []
    if capital_error:
        notes.append(capital_error)
    if dropped:
        notes.append(f"Unknown symbols ignored: {', '.join(dropped)}")
    if asked_timeframe and asked_timeframe != timeframe:
        notes.append(f"Timeframe {asked_timeframe!r} unavailable, using {timeframe or '—'}")
    if dates_start and dates_end and dates_start > dates_end:
        notes.append(f"Start {dates_start} is after end {dates_end}")
    snapshot: dict = {
        "selected_name": selected,
        "mode": mode,
        "run": "ready",
        "engine_wired": True,
        "outdated": False,
        "data_bounds": data_bounds,
        "config": {
            "universe": ", ".join(selected_symbols) if selected_symbols else "NO UNIVERSE",
            "timeframe": timeframe or "—",
            "dates": f"{dates_start} → {dates_end}" if dates_start and dates_end else "—",
            "capital": f"₹{capital_value:,.0f}",
        },
        "cfg_edit": {
            "universe_csv": ",".join(selected_symbols),
            "timeframes": timeframes,
            "timeframe": timeframe,
            "dates_start": dates_start,
            "dates_end": dates_end,
            "capital": capital_value,
            "config_error": "; ".join(notes),
        },
        "universe": {"symbols": universe_symbols, "selected": selected_symbols},
        "metadata": strat_def.metadata_dict if strat_def else {},
        "details": strat_def.metadata_dict if strat_def else {},
        "results": None,
    }
    if not selected:
        snapshot["cfg_edit"]["config_error"] = (
            f"Unknown strategy {requested!r}" if requested else "No strategies available"
        )
        return snapshot
    try:
        from app.services.backtest_service import describe_strategy
    except Exception as exc:  # noqa: BLE001
        snapshot["cfg_edit"]["config_error"] = f"lab backend unavailable: {exc}"
        return snapshot
    # Memoized by the on-disk library signature: describe_strategy re-reads
    # and RE-COMPILES the strategy source (AST parse + exec of ~40 KB) on
    # every call, and this ran per lab snapshot command. A file edit changes
    # the signature and recompiles exactly once.
    describe_key = (selected, strategy_dir, _library_signature(strategy_dir))
    detail = _DESCRIBE_CACHE.get(describe_key)
    if detail is None:
        try:
            detail = describe_strategy(selected, strategy_dir)
        except Exception as exc:  # noqa: BLE001
            snapshot["cfg_edit"]["config_error"] = str(exc)
            return snapshot
        _DESCRIBE_CACHE[describe_key] = detail
        if len(_DESCRIBE_CACHE) > 32:
            _DESCRIBE_CACHE.pop(next(iter(_DESCRIBE_CACHE)))
    # Source ships only when it changed: the first snapshot populates the
    # editor, later polls omit the key and Rust keeps its buffer — every
    # poll stays a small JSON line but the editor never goes blank.
    code_text = str(detail.get("code", ""))
    code_key = (selected, strategy_dir, describe_key[2])
    if _LAST_SENT_CODE.get(code_key) != code_text:
        _LAST_SENT_CODE[code_key] = code_text
        if len(_LAST_SENT_CODE) > 32:
            _LAST_SENT_CODE.pop(next(iter(_LAST_SENT_CODE)))
        snapshot["code"] = code_text
    snapshot["params"] = detail["params"]
    snapshot["rankby"] = {
        "labels": ["Net P&L", "Return %", "Trades", "Win %", "Profit Factor", "Max DD"],
        "current": 0,
        "search": "",
        "desc": True,
    }
    return snapshot


def _lab_snapshot(
    strategy_dir: str,
    command: dict | None = None,
    repository: Any = None,
) -> dict:
    """Build the native Strategy Lab snapshot from the strategy library.

    Rows come from real library files plus clearly-marked built-ins; the
    workspace opens populated from the same canonical market-data service
    the Chart uses. Nothing has been run, so results stay None.
    """
    try:
        rows = _lab_library_rows(strategy_dir)
    except Exception as exc:  # noqa: BLE001
        return {"strategies": [], "error": str(exc)}
    snapshot = _lab_workspace(strategy_dir, command or {}, rows, repository)
    snapshot["strategies"] = rows
    return snapshot


def _lab_run(
    strategy_dir: str,
    data_dir: str,
    command: dict,
    repository: Any = None,
    progress: RunProgress | Callable[[dict], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Execute a real historical backtest and return the Lab snapshot.

    ``progress`` receives every real execution event while the run happens, and
    ``should_cancel`` is polled between symbols. Both are optional and neither
    can change a result — turning reporting off changes no number of the run.
    A bare emit callable is accepted and wrapped into a real ``RunProgress``
    below; ``run_backtest`` only ever receives the object.
    """
    try:
        rows = _lab_library_rows(strategy_dir)
    except Exception as exc:  # noqa: BLE001
        return {"strategies": [], "error": str(exc)}
    workspace = _lab_workspace(strategy_dir, command, rows, repository)
    workspace["strategies"] = rows
    strategy_name = str(command.get("strategy") or workspace.get("selected_name") or "").strip()
    if not strategy_name:
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = "NO STRATEGY SELECTED"
        return workspace
    raw_symbols = command.get("symbols") or []
    if isinstance(raw_symbols, str):
        raw_symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]
    seen: set[str] = set()
    symbols: list[str] = []
    for entry in raw_symbols:
        text = str(entry).strip().upper()
        if text and text not in seen:
            seen.add(text)
            symbols.append(text)
    # No silent fallback to the workspace universe: an explicitly empty
    # selection fails actionably instead of running unasked symbols.
    if not symbols:
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = "No universe: select at least one symbol"
        return workspace
    timeframe = str(command.get("timeframe") or "").strip() or None
    if timeframe is None:
        timeframe = workspace.get("cfg_edit", {}).get("timeframe") or None
    start = str(command.get("start") or "").strip() or None
    end = str(command.get("end") or "").strip() or None
    if start is None:
        start = workspace.get("cfg_edit", {}).get("dates_start") or None
    if end is None:
        end = workspace.get("cfg_edit", {}).get("dates_end") or None
    capital_raw = command.get("capital", _LAB_DEFAULT_CAPITAL)
    try:
        capital = float(capital_raw)
    except (TypeError, ValueError):
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = f"Invalid capital {capital_raw!r}: must be a number"
        return workspace
    if capital != capital or capital in (float("inf"), float("-inf")) or capital <= 0:
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = (
            f"Invalid capital {capital_raw!r}: must be a positive number"
        )
        return workspace
    mode = str(command.get("mode") or workspace.get("mode") or "buy").strip().lower()
    try:
        from app.progress import RunProgress
        from app.services.backtest_service import BacktestError, run_backtest
    except Exception as exc:  # noqa: BLE001
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = f"lab backend unavailable: {exc}"
        return workspace
    # The command loop hands us a bare emit callable; the engine needs the
    # real RunProgress object (counts/cancel). Wrap once per run — never pass
    # a raw function into run_backtest.
    if progress is None or (callable(progress) and not isinstance(progress, RunProgress)):
        emit: Callable[[dict], None] | None = progress if callable(progress) else None
        progress = RunProgress(max(1, len(symbols)), emit, should_cancel)
    if not isinstance(progress, RunProgress):
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = "lab progress channel unavailable"
        return workspace

    legs: list[RunProgress] = []

    def _fresh_progress() -> RunProgress:
        leg = RunProgress(max(1, len(symbols)), progress._emit, should_cancel)  # noqa: SLF001
        legs.append(leg)
        return leg

    def _forward_leg(leg: RunProgress) -> None:
        """Fold one leg's measured facts into the outer progress."""
        progress.completed += leg.completed
        progress.trades += leg.trades
        progress.bars += leg.bars
        progress.net_pnl += leg.net_pnl
        progress.failed.extend(s for s in leg.failed if s not in progress.failed)
        progress.skipped.extend(s for s in leg.skipped if s not in progress.skipped)
        if leg.cancelled:
            progress.cancelled = True

    def _legs_cancelled() -> bool:
        return progress.cancelled or any(leg.cancelled or leg.cancel_requested() for leg in legs)

    try:
        if mode == "compare":
            buy_results = run_backtest(
                strategy_name,
                [str(s) for s in symbols],
                timeframe,
                start,
                end,
                capital,
                "buy",
                data_dir,
                strategy_dir,
                repository,
                progress=_fresh_progress(),
                should_cancel=should_cancel,
            )
            _forward_leg(legs[-1])
            if progress.cancel_requested() or _legs_cancelled():
                progress.cancelled = True
                workspace["results"] = None
                workspace["run"] = "cancelled"
                workspace["cfg_edit"]["config_error"] = (
                    f"Run cancelled after {progress.completed} of {progress.total} symbols"
                )
                return workspace
            sell_results = run_backtest(
                strategy_name,
                [str(s) for s in symbols],
                timeframe,
                start,
                end,
                capital,
                "sell",
                data_dir,
                strategy_dir,
                repository,
                progress=_fresh_progress(),
                should_cancel=should_cancel,
            )
            _forward_leg(legs[-1])
            workspace["results"] = buy_results
            workspace["buy"] = buy_results
            workspace["sell"] = sell_results
        else:
            workspace["results"] = run_backtest(
                strategy_name,
                [str(s) for s in symbols],
                timeframe,
                start,
                end,
                capital,
                mode,
                data_dir,
                strategy_dir,
                repository,
                progress=progress,
                should_cancel=should_cancel,
            )
    except BacktestError as exc:
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = str(exc)
        return workspace
    except Exception as exc:  # noqa: BLE001
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = f"Strategy failed: {exc}"
        return workspace
    # A cancelled run is reported as CANCELLED, never as a complete one: the
    # partial result set is not a result for the requested universe.
    # Compare mode runs two legs on child progress objects, so a cancel that
    # landed mid-leg lives on the leg — the outer flag alone would miss it.
    was_cancelled = isinstance(progress, RunProgress) and (
        progress.cancelled or _legs_cancelled() if mode == "compare" else progress.cancelled
    )
    if was_cancelled:
        workspace["results"] = None
        workspace.pop("buy", None)
        workspace.pop("sell", None)
    workspace["run"] = "cancelled" if was_cancelled else "complete"
    workspace["config"] = {
        "universe": ", ".join(str(s) for s in symbols),
        "timeframe": timeframe or "—",
        "dates": f"{start} → {end}" if start and end else "—",
        "capital": f"₹{capital:,.0f}",
    }
    if was_cancelled:
        workspace["cfg_edit"]["config_error"] = (
            f"Run cancelled after {progress.completed} of {progress.total} symbols"
        )
    return workspace


def _research_snapshot(data_dir: str, strategy_dir: str) -> dict:
    """Build the native Research snapshot from the research service.

    Pure read path (strategies + experiments + selection defaults); the legacy
    host's projection is mirrored without importing any UI toolkit. Bundle
    stays None here (no selection); executed bundles arrive via run and
    selection interactions in later slices.
    """
    try:
        from backtest.execution import list_histories  # pyright: ignore[reportMissingImports]

        from app.services.research_service import ResearchService
        from strategy.language.storage import list_strategies
    except Exception as exc:  # noqa: BLE001
        return {"strategies": [], "experiments": [], "error": f"research backend: {exc}"}
    try:
        service = ResearchService(
            data_dir=data_dir,
            strategy_dir=strategy_dir,
            list_strategies_fn=list_strategies,
            list_histories_fn=list_histories,
            repository=_market_repository(data_dir),
        )
        names = [str(n) for n in (service.available_strategies() or [])]
        strategies = []
        for name in names:
            version = ""
            try:
                described = service.describe_strategy(name)
                if isinstance(described, dict):
                    version = str(described.get("version", "") or "")
            except Exception:  # noqa: BLE001
                version = ""
            strategies.append({"name": name, "description": "", "version": version})
        experiments = []
        try:
            for exp in service.experiments():
                if isinstance(exp, dict) and exp.get("experiment_id"):
                    experiments.append(
                        {
                            "id": str(exp["experiment_id"]),
                            "strategy": str(exp.get("strategy_id", "") or ""),
                            "status": str(exp.get("status", "DRAFT") or "DRAFT"),
                        }
                    )
        except Exception:  # noqa: BLE001
            experiments = []
        return {
            "strategies": strategies,
            "experiments": experiments,
            "selected_id": "",
            "status": "",
            "running": False,
            "stale": False,
            "log": [],
            "defaults": {},
            "bundle": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"strategies": [], "experiments": [], "error": f"research snapshot: {exc}"}


def parse_headless_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse headless backend arguments."""
    parser = argparse.ArgumentParser(
        prog="vayren-headless", description="VAYREN headless backend (native)"
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Folder containing SQLite database files",
    )
    parser.add_argument(
        "--strategy-dir",
        required=True,
        help="Folder containing strategy .py files",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


def run_headless_backend(args: argparse.Namespace) -> int:
    """Run the headless backend service.

    Reads JSON commands from stdin, processes them via the business logic
    (EventBus, services), writes JSON responses to stdout. The Rust UI
    spawns this process and communicates via this protocol.
    """
    _logs_to_stderr(args.log_level)
    data_dir = Path(args.data_dir)
    strategy_dir = Path(args.strategy_dir)

    logger.info("Headless backend starting: data_dir=%s strategy_dir=%s", data_dir, strategy_dir)

    # Initialize core services (no UI toolkit). The canonical market-data
    # service feeds both Chart and Strategy Lab; honest-empty when absent.
    repository = _market_repository(data_dir)
    if repository is not None:
        try:
            report = repository.discovery_report()
            logger.info(
                "Market source: data_dir=%s symbols=%d valid=%d skipped=%d",
                report.data_dir,
                len(report.symbols),
                report.valid_sources,
                len(report.skipped),
            )
            for name, reason in report.skipped:
                logger.info("Market source skipped %s: %s", name, reason)
        except Exception as exc:  # noqa: BLE001
            logger.error("Market discovery failed: %s", exc)
    else:
        logger.error("Market source unavailable for data_dir=%s", data_dir)

    # Initialize broker manager for system workspace
    broker_manager = None
    try:
        import broker.providers  # noqa: F401
        from app.services.broker_manager import BrokerManager

        broker_manager = BrokerManager(data_dir=str(data_dir))
        for b_id in broker_manager.broker_ids():
            broker_manager.submit_check(b_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Broker manager startup check failed: %s", exc)

    # Ready signal
    response = {
        "type": "ready",
        "data": {
            "backend": "vayren-headless",
            "version": _BACKEND_VERSION,
            "data_dir": str(data_dir),
            "strategy_dir": str(strategy_dir),
        },
    }
    if not _emit(response):
        if broker_manager is not None:
            broker_manager.stop_worker()
        return 1

    # Command loop — the shared inbox is the only stdin reader.
    _ensure_stdin_reader()
    try:
        while True:
            line = _next_line()
            if line is _LAB_EOF:
                break
            if not isinstance(line, str) or not line.strip():
                continue
            try:
                command = json.loads(line.strip())
                if not isinstance(command, dict):
                    _emit({"type": "error", "data": {"error": "Invalid command shape"}})
                    continue
                cmd_type = command.get("type")

                if cmd_type == "list_symbols":
                    symbols = repository.list_symbols() if repository is not None else []
                    result = {
                        "type": "symbols_listed",
                        "data": {"symbols": symbols},
                    }
                    if not _emit(result):
                        break

                elif cmd_type == "get_market_snapshot":
                    snapshot = _market_snapshot(repository, command, str(strategy_dir))
                    result = {"type": "market_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_system_snapshot":
                    selected_broker = command.get("selected_id")
                    # No `_await_broker_settled` sleep-loop here: it blocked
                    # the SINGLE command thread for up to 3s per snapshot.
                    # Broker checks already run on the manager's worker; the
                    # snapshot reports the current (possibly unsettled) state
                    # honestly, and the next poll reflects the settled one.
                    snapshot = _system_snapshot(str(data_dir), selected_broker, broker_manager)
                    result = {"type": "system_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_portfolio_snapshot":
                    snapshot = _portfolio_snapshot(str(data_dir), str(strategy_dir))
                    result = {"type": "portfolio_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_live_snapshot":
                    snapshot = _trading_service_snapshot(
                        str(data_dir), str(strategy_dir), broker_manager
                    )
                    result = {"type": "live_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "live_action":
                    action = command.get("action")
                    snapshot = _live_action(
                        str(data_dir),
                        str(strategy_dir),
                        action if isinstance(action, dict) else {},
                        broker_manager,
                    )
                    result = {"type": "live_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_research_snapshot":
                    snapshot = _research_snapshot(str(data_dir), str(strategy_dir))
                    result = {"type": "research_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type in ("get_lab_snapshot", "select_lab_strategy", "save_lab_strategy"):
                    if cmd_type == "save_lab_strategy":
                        strategy_name = str(command.get("strategy") or "").strip()
                        code = str(command.get("code") or "")
                        if strategy_name and code:
                            target = Path(strategy_dir)
                            if not target.is_dir():
                                target = Path(r"D:\VAYREN_STRATEGIES")
                            file_name = (
                                strategy_name
                                if strategy_name.endswith(".py")
                                else f"{strategy_name}.py"
                            )
                            file_path = target / file_name
                            try:
                                file_path.write_text(code, encoding="utf-8")
                                logger.info("Saved strategy %s to %s", strategy_name, file_path)
                            except Exception as exc:  # noqa: BLE001
                                logger.error("Failed to save strategy %s: %s", strategy_name, exc)
                            _LAB_ROWS_CACHE.clear()
                            _DESCRIBE_CACHE.clear()
                    snapshot = _lab_snapshot(str(strategy_dir), command, repository)
                    result = {"type": "lab_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "lab_coverage":
                    requested = command.get("symbols") or []
                    if isinstance(requested, str):
                        requested = [s.strip() for s in requested.split(",") if s.strip()]
                    coverage = _lab_coverage(
                        repository,
                        list(requested),
                        str(command.get("start") or "").strip(),
                        str(command.get("end") or "").strip(),
                        str(command.get("timeframe") or "").strip(),
                        bool(command.get("full")),
                    )
                    result = {
                        "type": "lab_coverage",
                        "data": coverage if coverage is not None else {},
                    }
                    if not _emit(result):
                        break

                elif cmd_type == "run_backtest":
                    # The run streams real progress while it executes; the
                    # terminal line is still the snapshot the shell consumes.
                    # The run owns a worker thread; this loop keeps pumping
                    # the one inbox so cancel lands and nothing is dropped.
                    _LAB_CANCEL.clear()
                    box: dict[str, Any] = {}
                    finished = threading.Event()

                    def _run(
                        cmd: dict = command, out: dict = box, done: threading.Event = finished
                    ) -> None:
                        try:
                            out["snapshot"] = _lab_run(
                                str(strategy_dir),
                                str(data_dir),
                                cmd,
                                repository,
                                progress=_progress_emitter(),
                                should_cancel=_LAB_CANCEL.is_set,
                            )
                        except Exception as exc:  # noqa: BLE001
                            out["error"] = exc
                        finally:
                            done.set()

                    worker = threading.Thread(target=_run, name="lab-run", daemon=True)
                    worker.start()
                    _pump_while_running(finished)
                    worker.join(timeout=5.0)
                    if worker.is_alive():
                        logger.warning("lab run worker hung after 5s; waiting for result")
                        worker.join()
                    _requeue_deferred()
                    if "error" in box:
                        raise box["error"]
                    snapshot = box["snapshot"]
                    result = {"type": "lab_snapshot", "data": snapshot}
                    if not _emit(result):
                        break
                    _LAB_CANCEL.clear()

                elif cmd_type == "cancel_backtest":
                    # No run owns a worker right now — nothing to stop.
                    logger.info("cancel_backtest received while idle; ignoring")

                elif cmd_type == "connect_broker":
                    broker_id = str(command.get("broker_id") or "fyers").strip()
                    credentials = command.get("credentials") or {}
                    logger.info("connect_broker received for broker=%s", broker_id)

                    if broker_manager is not None:
                        spec = broker_manager._specs.get(broker_id)
                        if spec is not None:
                            try:
                                stored = broker_manager._store.load(spec.config_service) or {}
                            except Exception:
                                stored = {}
                            merged = dict(stored) if isinstance(stored, dict) else {}
                            for k, v in credentials.items():
                                val = str(v).strip()
                                if val:
                                    merged[str(k)] = val

                            if broker_id == "fyers":
                                from broker.providers.fyers.live_auth import DEFAULT_REDIRECT_URL

                                r_uri = str(merged.get("redirect_uri") or "").strip()
                                if (
                                    not r_uri
                                    or not (
                                        r_uri.startswith("http://") or r_uri.startswith("https://")
                                    )
                                    or "\n" in r_uri
                                    or "\r" in r_uri
                                    or " " in r_uri
                                ):
                                    merged["redirect_uri"] = DEFAULT_REDIRECT_URL

                            broker_manager._set_status(
                                broker_id, BrokerStatus.AUTHENTICATING, "connecting to venue…"
                            )
                            ok, msg = broker_manager.configure(broker_id, merged)
                            logger.info(
                                "Broker configure for %s: ok=%s, msg=%s", broker_id, ok, msg
                            )
                            if not ok:
                                snapshot = _system_snapshot(
                                    str(data_dir), broker_id, broker_manager
                                )
                                snapshot["status_raw"] = "ERROR"
                                snapshot["reason"] = msg
                                result = {"type": "system_snapshot", "data": snapshot}
                                if not _emit(result):
                                    break
                                continue

                            # Wait for worker thread to complete authentication (up to 15s)
                            import time

                            deadline = time.monotonic() + 15.0
                            time.sleep(0.3)
                            while time.monotonic() < deadline:
                                st = broker_manager.state(broker_id)
                                status = st.get("status")
                                if status not in (
                                    BrokerStatus.AUTHENTICATING,
                                    BrokerStatus.CONFIGURING,
                                ):
                                    # Zerodha path without auto_auth: start interactive login
                                    if (
                                        status == BrokerStatus.LOGIN_REQUIRED
                                        and spec.auto_authenticate is None
                                        and spec.interactive_login is not None
                                        and not broker_manager.is_connected(broker_id)
                                    ):
                                        ok_login, msg_login = broker_manager.start_login(broker_id)
                                        logger.info(
                                            "start_login triggered for %s: ok=%s, msg=%s",
                                            broker_id,
                                            ok_login,
                                            msg_login,
                                        )
                                        if ok_login:
                                            time.sleep(0.5)
                                            continue
                                    break
                                time.sleep(0.3)

                    snapshot = _system_snapshot(str(data_dir), broker_id, broker_manager)
                    if broker_manager is not None:
                        st = broker_manager.state(broker_id)
                        status = st.get("status")
                        if status not in (BrokerStatus.CONNECTED, BrokerStatus.LIVE_READY):
                            snapshot["status_raw"] = "ERROR"
                            snapshot["reason"] = (
                                st.get("reason") or "Authentication failed. Check your credentials."
                            )
                        else:
                            snapshot["status_raw"] = "CONNECTED"
                            snapshot["reason"] = st.get("reason") or "Connected and verified."
                    result = {"type": "system_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "disconnect_broker":
                    broker_id = str(command.get("broker_id") or "fyers").strip()
                    logger.info("disconnect_broker received for broker=%s", broker_id)
                    if broker_manager is not None:
                        broker_manager.disconnect_broker(broker_id)
                    snapshot = _system_snapshot(str(data_dir), broker_id, broker_manager)
                    result = {"type": "system_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "shutdown":
                    logger.info("Shutdown requested")
                    if broker_manager is not None:
                        broker_manager.stop_worker()
                    break

                else:
                    error = {
                        "type": "error",
                        "data": {"message": f"Unknown command: {cmd_type}"},
                    }
                    if not _emit(error):
                        break

            except json.JSONDecodeError as exc:
                error = {
                    "type": "error",
                    "data": {"message": f"Invalid JSON: {exc}"},
                }
                print(json.dumps(error), flush=True)
            except Exception as exc:  # noqa: BLE001
                error = {
                    "type": "error",
                    "data": {"message": f"Command failed: {exc}"},
                }
                print(json.dumps(error), flush=True)

    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 130
    finally:
        if broker_manager is not None:
            broker_manager.stop_worker()

    logger.info("Headless backend shutdown")
    return 0


def main_headless(argv: list[str] | None = None) -> int:
    """Headless backend entry point."""
    args = parse_headless_args(argv)
    return run_headless_backend(args)


if __name__ == "__main__":
    sys.exit(main_headless())
