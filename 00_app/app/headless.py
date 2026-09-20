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
from datetime import datetime
from logging import getLogger
from pathlib import Path

from core.logger import configure_logging
from market.repository.symbol_repository import SymbolRepository

logger = getLogger(__name__)


def _logs_to_stderr(level: str) -> None:
    """Route all logs to stderr — stdout carries ONLY protocol JSON.

    The shared ``configure_logging`` writes to stdout, which would corrupt
    the newline-delimited JSON protocol. Keep using it (single format
    source), then move its stdout handlers to stderr.
    """
    configure_logging(level)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
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


def _market_snapshot(repository: SymbolRepository, command: dict) -> dict:
    """Build the native Market snapshot from real SQLite data.

    Single round-trip feeding Rust ``apply_snapshot_json``: watchlist rows
    with live quotes, the selected symbol's bars (base or aggregated
    timeframe), and the detected timeframe ladder. Honest emptiness when
    the store has nothing — never invented bars.
    """
    symbols = repository.list_symbols()
    symbol = command.get("symbol") or (symbols[0] if symbols else "")
    timeframe = command.get("timeframe") or ""
    limit = command.get("limit")
    if limit is not None:
        limit = int(limit)
    quotes = repository.get_quotes(symbols)
    timeframes: tuple = repository.detect_timeframes(symbol) if symbol else ()
    if timeframe:
        bars = repository.get_candles_timeframe(symbol, timeframe, limit)
    elif symbol:
        bars = repository.get_candles(symbol, limit)
    else:
        bars = []
    return {
        "symbols": [
            {"symbol": quote.symbol, "price": quote.price, "change_pct": quote.change_pct}
            for quote in quotes
        ],
        "selected_symbol": symbol,
        "timeframes": list(timeframes),
        "timeframe": timeframe,
        "exchange": "",
        "bars": [_bar_to_dict(bar) for bar in bars],
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


def _lab_snapshot(strategy_dir: str) -> dict:
    """Build the native Strategy Lab snapshot from the strategy library.

    No legacy workspace exists headless: rows come from real library files
    (names + file mtimes). Nothing has been run, so config uses the legacy
    empty vocabulary and results stay None. RUN interactions arrive in
    later slices.
    """
    try:
        from strategy.language.storage import list_strategies
    except Exception as exc:  # noqa: BLE001
        return {"strategies": [], "error": f"lab backend unavailable: {exc}"}
    try:
        names = [str(n) for n in (list_strategies(strategy_dir) or [])]
    except Exception:  # noqa: BLE001
        names = []
    rows = []
    for name in names:
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
    return {
        "strategies": rows,
        "selected_name": names[0] if names else "",
        "mode": "buy",
        "run": "ready",
        "engine_wired": True,
        "outdated": False,
        "config": {"universe": "NO UNIVERSE", "timeframe": "—", "dates": "—", "capital": "—"},
        "results": None,
    }


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
            repository=SymbolRepository(data_dir),
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

    # Initialize core services (no UI toolkit)
    repository = SymbolRepository(data_dir)

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
                    symbols = repository.list_symbols()
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

                elif cmd_type == "get_lab_snapshot":
                    snapshot = _lab_snapshot(str(strategy_dir))
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
