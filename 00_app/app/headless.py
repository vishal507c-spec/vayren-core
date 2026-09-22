"""Headless VAYREN backend — native service mode for the Rust UI.

Entry point for running VAYREN business logic (EventBus, market data,
strategies, backtest, execution) without any UI toolkit. Communicates
with the Rust native UI via JSON over stdin/stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import suppress
from datetime import datetime
from logging import getLogger
from pathlib import Path
from typing import Any


def _bootstrap_chapter_path() -> None:
    """Ensure sibling chapter packages resolve regardless of cwd.

    ``python -m app.headless`` only guarantees ``app`` itself is importable
    (cwd or an editable install). The composition root needs every chapter,
    so the repo's chapter folders join ``sys.path`` when they are missing.
    Fail-closed: an unresolvable layout keeps the original path untouched.
    """
    try:
        anchor = Path(__file__).resolve()
        repo_root = anchor.parents[2]  # 00_app/app/headless.py → repo root
        chapters = (
            "00_app",
            "01_core",
            "02_data",
            "03_market",
            "05_strategy",
            "06_backtest",
            "07_risk",
            "08_execution",
            "09_broker",
        )
        for chapter in chapters:
            candidate = str(repo_root / chapter)
            if (repo_root / chapter).is_dir() and candidate not in sys.path:
                sys.path.insert(0, candidate)
    except Exception:  # noqa: BLE001
        pass


_bootstrap_chapter_path()

logger = getLogger(__name__)


def _logs_to_stderr(level: str) -> None:
    """Route all logs to stderr — stdout carries ONLY protocol JSON.

    ``core.logger`` was removed in the Rust-owned core cleanup, so fall back
    to stdlib ``basicConfig`` when the shared helper is gone. Either way, any
    stdout handler is then moved to stderr — stdout carries ONLY protocol
    JSON, never log text.
    """
    try:
        from core.logger import configure_logging

        configure_logging(level)
    except ImportError:
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
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
    root.addHandler(handler)


def _emit(payload: dict) -> bool:
    """Write one JSON response line. False when the pipe is gone."""
    try:
        print(json.dumps(payload), flush=True)
    except OSError:
        return False
    return True


def _bar_to_dict(bar) -> dict:
    """One backend Bar as bridge JSON (mirrors Rust MarketBar fields)."""
    return {
        "time": bar.timestamp,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
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


def _empty_market_snapshot(notice: str = "") -> dict:
    """Honest-empty market snapshot (never invented bars)."""
    return {
        "symbols": [],
        "selected_symbol": "",
        "timeframes": [],
        "timeframe": "",
        "exchange": "",
        "bars": [],
        "notice": notice,
    }


def _market_snapshot(repository: Any, command: dict) -> dict:
    """Build the native Market snapshot from real SQLite data.

    Single round-trip feeding Rust ``apply_snapshot_json``: watchlist rows
    with live quotes, the selected symbol's bars (base or kernel-aggregated
    timeframe), and the detected timeframe ladder. Honest emptiness with an
    actionable ``notice`` when the store has nothing — never invented bars.
    """
    if repository is None:
        return _empty_market_snapshot("Data directory not found or unreadable")
    symbols = repository.list_symbols()
    if not symbols:
        return _empty_market_snapshot("No symbols discovered")
    requested = (command.get("symbol") or "").strip().upper()
    if requested and requested not in symbols:
        notice = f"Unknown symbol {requested} ({len(symbols)} symbols available)"
        return {
            **_empty_market_snapshot(notice),
            "selected_symbol": requested,
        }
    symbol = requested or symbols[0]
    limit = command.get("limit")
    if limit is not None:
        limit = int(limit)
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
        "notice": "",
    }


def _await_broker_settled(manager, timeout_s: float = 10.0) -> None:
    """Wait until every broker stamped last_sync (or the deadline hits).

    The worker mutates manager state directly and this backend only reads
    snapshots (no signal handlers), so no event pumping is required.
    """
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            states = [manager.state(broker_id) for broker_id in manager.broker_ids()]
        except Exception:  # noqa: BLE001
            return
        if states and all(state.get("last_sync") for state in states):
            return
        time.sleep(0.25)


def _credential_field_rows(record: dict) -> list:
    """Venue credential shapes for the form (shapes only, never values).

    Same hint-only placeholders the legacy bootstrap used; the Rust side also
    falls back to "Enter {label}" when a placeholder is absent.
    """
    schema = record.get("credential_schema")
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
            }
        )
    return rows


def _system_snapshot(data_dir: str) -> dict:
    """Build the native System workspace snapshot from the broker manager.

    Mirrors the legacy bootstrap provider: authoritative selection + manager
    snapshot + read-only startup session checks. Fail-closed honest-empty
    when the broker stack cannot load.
    """
    try:
        import data.provider.factory  # noqa: F401 (seeds zerodha+fyers specs)

        from app.services.broker_manager import BrokerManager
        from app.services.broker_selection_service import (
            BrokerSelectionService,
            app_selection_store,
        )
    except Exception as exc:  # noqa: BLE001
        return {"brokers": [], "error": f"system backend unavailable: {exc}"}
    try:
        selection_service = BrokerSelectionService(app_selection_store(data_dir))
        selection = selection_service.current()
    except Exception:  # noqa: BLE001
        selection = None
    manager = BrokerManager(data_dir=data_dir)
    try:
        for broker_id in manager.broker_ids():
            manager.submit_check(broker_id)
        _await_broker_settled(manager)
        snap = manager.snapshot()
    finally:
        manager.stop_worker()
    selected_id = getattr(selection, "name", "") or ""
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
        "credential_fields": _credential_field_rows(full),
    }


def _trading_service_snapshot(data_dir: str, strategy_dir: str) -> dict:
    """One idle-service snapshot feeding Portfolio + Live screens.

    A fresh service (never auto-starts): honest not-running book — empty
    positions/orders, PAPER mode, real blockers. The Rust sides render
    exactly this shape (same dict the legacy hosts consumed). Bar objects are
    converted to bridge dicts (JSON cannot carry them).
    """
    try:
        from app.services.live_trading_service import LiveTradingService
    except Exception as exc:  # noqa: BLE001
        return {"mode": "PAPER", "error": f"trading backend unavailable: {exc}"}
    try:
        service = LiveTradingService(data_dir=data_dir, strategy_dir=strategy_dir)
        snap = service.snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"mode": "PAPER", "error": f"trading snapshot failed: {exc}"}
    bars = snap.get("market_bars")
    if bars:
        snap["market_bars"] = [_bar_to_dict(b) for b in bars]
    return snap


def _portfolio_snapshot(data_dir: str, strategy_dir: str) -> dict:
    """Build the native Portfolio snapshot from the trading service."""
    return _trading_service_snapshot(data_dir, strategy_dir)


def _lab_library_rows(strategy_dir: str) -> list[dict]:
    """Strategy library rows: real files first, then marked built-ins."""
    try:
        from strategy import builtins
        from strategy.language.storage import list_strategies
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"lab backend unavailable: {exc}") from exc
    try:
        file_names = [str(n) for n in (list_strategies(strategy_dir) or [])]
    except Exception:  # noqa: BLE001
        file_names = []
    rows: list[dict] = []
    for name in file_names:
        modified = ""
        try:
            stamp = Path(strategy_dir, f"{name}.py").stat().st_mtime
            if stamp:
                modified = datetime.fromtimestamp(stamp).strftime("%d %b %y")
        except Exception:  # noqa: BLE001
            modified = ""
        rows.append(
            {
                "name": name,
                "description": "",
                "tags": [],
                "version": "",
                "modified": modified,
                "last_backtest": "",
                "favorite": False,
            }
        )
    known = {name.lower() for name in file_names}
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
                "last_backtest": "",
                "favorite": False,
            }
        )
    return rows


def _lab_universe(repository: Any) -> tuple[list[str], str]:
    """Canonical universe symbols (same market-data service as Chart)."""
    if repository is None:
        return [], "Data directory not found or unreadable"
    try:
        return repository.list_symbols(), ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


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
    requested = (command.get("strategy") or command.get("selected_name") or "").strip()
    selected = requested if requested in names else (names[0] if names else "")
    if requested and requested not in names:
        selected = ""
    universe_symbols, universe_error = _lab_universe(repository)
    requested_symbols = command.get("symbols") or []
    if isinstance(requested_symbols, str):
        requested_symbols = [s.strip() for s in requested_symbols.split(",") if s.strip()]
    selected_symbols = [s for s in requested_symbols if s in universe_symbols]
    if universe_symbols and repository is not None:
        try:
            anchor = selected_symbols[0] if selected_symbols else universe_symbols[0]
            timeframes = list(repository.available_timeframes(anchor))
            first_date, last_date = repository.date_range(anchor)
        except Exception:  # noqa: BLE001
            timeframes, first_date, last_date = [], "", ""
    else:
        timeframes, first_date, last_date = [], "", ""
    timeframe = (command.get("timeframe") or "").strip()
    if timeframe not in timeframes:
        timeframe = "15m" if "15m" in timeframes else (timeframes[0] if timeframes else "")
    dates_start = (command.get("start") or command.get("dates_start") or "").strip()
    dates_end = (command.get("end") or command.get("dates_end") or "").strip()
    if not dates_start:
        dates_start = first_date
    if not dates_end:
        dates_end = last_date
    capital_raw = command.get("capital", 1000000)
    try:
        capital_value = float(capital_raw)
    except (TypeError, ValueError):
        capital_value = 1000000.0
    mode = (command.get("mode") or "buy").strip().lower()
    if mode not in ("buy", "sell", "compare"):
        mode = "buy"
    snapshot: dict = {
        "selected_name": selected,
        "mode": mode,
        "run": "ready",
        "engine_wired": True,
        "outdated": False,
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
            "config_error": universe_error,
        },
        "universe": {"symbols": universe_symbols, "selected": selected_symbols},
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
    try:
        detail = describe_strategy(selected, strategy_dir)
    except Exception as exc:  # noqa: BLE001
        snapshot["cfg_edit"]["config_error"] = str(exc)
        return snapshot
    snapshot["code"] = detail["code"]
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


def _lab_run(strategy_dir: str, data_dir: str, command: dict, repository: Any = None) -> dict:
    """Execute a real historical backtest and return the Lab snapshot."""
    try:
        rows = _lab_library_rows(strategy_dir)
    except Exception as exc:  # noqa: BLE001
        return {"strategies": [], "error": str(exc)}
    workspace = _lab_workspace(strategy_dir, command, rows, repository)
    workspace["strategies"] = rows
    strategy_name = (command.get("strategy") or workspace.get("selected_name") or "").strip()
    if not strategy_name:
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = "NO STRATEGY SELECTED"
        return workspace
    symbols = command.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbols:
        csv = workspace.get("cfg_edit", {}).get("universe_csv", "")
        symbols = [s.strip() for s in csv.split(",") if s.strip()]
    timeframe = (command.get("timeframe") or "").strip() or None
    if timeframe is None:
        timeframe = workspace.get("cfg_edit", {}).get("timeframe") or None
    start = (command.get("start") or "").strip() or None
    end = (command.get("end") or "").strip() or None
    if start is None:
        start = workspace.get("cfg_edit", {}).get("dates_start") or None
    if end is None:
        end = workspace.get("cfg_edit", {}).get("dates_end") or None
    try:
        capital = float(command.get("capital", 1000000))
    except (TypeError, ValueError):
        capital = 1000000.0
    mode = (command.get("mode") or workspace.get("mode") or "buy").strip().lower()
    try:
        from app.services.backtest_service import BacktestError, run_backtest
    except Exception as exc:  # noqa: BLE001
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = f"lab backend unavailable: {exc}"
        return workspace
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
            )
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
            )
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
            )
    except BacktestError as exc:
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = str(exc)
        return workspace
    except Exception as exc:  # noqa: BLE001
        workspace["run"] = "failed"
        workspace["cfg_edit"]["config_error"] = f"Strategy failed: {exc}"
        return workspace
    workspace["run"] = "complete"
    workspace["config"] = {
        "universe": ", ".join(str(s) for s in symbols),
        "timeframe": timeframe or "—",
        "dates": f"{start} → {end}" if start and end else "—",
        "capital": f"₹{capital:,.0f}",
    }
    return workspace


def _research_snapshot(data_dir: str, strategy_dir: str) -> dict:
    """Build the native Research snapshot from the research service.

    Pure read path (strategies + experiments + selection defaults); the legacy
    host's projection is mirrored without importing any UI toolkit. Bundle
    stays None here (no selection); executed bundles arrive via run and
    selection interactions in later slices.
    """
    try:
        from backtest.execution import list_histories
        from strategy.language.storage import list_strategies

        from app.services.research_service import ResearchService
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

    # Ready signal
    response = {
        "type": "ready",
        "data": {
            "backend": "vayren-headless",
            "version": "1.18.0",
            "data_dir": str(data_dir),
            "strategy_dir": str(strategy_dir),
        },
    }
    if not _emit(response):
        return 1

    # Command loop
    try:
        for line in sys.stdin:
            try:
                command = json.loads(line.strip())
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
                    snapshot = _market_snapshot(repository, command)
                    result = {"type": "market_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_system_snapshot":
                    snapshot = _system_snapshot(str(data_dir))
                    result = {"type": "system_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_portfolio_snapshot":
                    snapshot = _portfolio_snapshot(str(data_dir), str(strategy_dir))
                    result = {"type": "portfolio_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_live_snapshot":
                    snapshot = _trading_service_snapshot(str(data_dir), str(strategy_dir))
                    result = {"type": "live_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "get_research_snapshot":
                    snapshot = _research_snapshot(str(data_dir), str(strategy_dir))
                    result = {"type": "research_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type in ("get_lab_snapshot", "select_lab_strategy"):
                    snapshot = _lab_snapshot(str(strategy_dir), command, repository)
                    result = {"type": "lab_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "run_backtest":
                    snapshot = _lab_run(str(strategy_dir), str(data_dir), command, repository)
                    result = {"type": "lab_snapshot", "data": snapshot}
                    if not _emit(result):
                        break

                elif cmd_type == "shutdown":
                    logger.info("Shutdown requested")
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

    logger.info("Headless backend shutdown")
    return 0


def main_headless(argv: list[str] | None = None) -> int:
    """Headless backend entry point."""
    args = parse_headless_args(argv)
    return run_headless_backend(args)


if __name__ == "__main__":
    sys.exit(main_headless())
