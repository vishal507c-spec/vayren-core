"""Slint Strategy Lab host — dumb viewport for the native view (no UI here).

The native Strategy Lab UI (layout, components, styling, state, interaction)
lives 100% in Rust+Slint (`rust/vayren-shell/ui/lab.slint` +
`rust/vayren-strategy-lab-view`). This module only:

- loads the `vayren_strategy_lab_view` cdylib (fail-closed, same handshake
  discipline as the Portfolio/LIVE hosts),
- blits its RGB frames into a plain QWidget (no Strategy Lab painting, no
  layout, no business logic),
- forwards input events (mouse/hover/wheel/keys/resize) 1:1 in logical units,
- pushes backend snapshots produced by :func:`strategy_lab_snapshot_dict` and
  drains queued user actions into :func:`apply_lab_strategy_action`.

Bridge snapshot schema (owned by the Python backend; the Rust side parses it
defensively via `lab::apply_snapshot_json` — missing/mistyped values degrade
to honest absence, never invented data): ``strategies[]{name,description,
tags[],version,modified,last_backtest,favorite}``, ``selected_name``,
``mode`` (buy|sell|compare), ``run`` (ready|running|complete|failed),
``engine_wired``, ``outdated``, ``config{universe,timeframe,dates,capital,
cost}``, ``results`` (null | {metrics{...raw numbers}, ranking[], trades[],
equity_curve[], risk_notes[]}). All display formatting happens in Rust.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import platform
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

ABI_VERSION = 1
VIEW_LIB_ENV_OVERRIDE = "VAYREN_STRATEGY_LAB_VIEW_LIB"
PUMP_INTERVAL_MS = 33
PROVIDER_POLL_TICKS = 30
WHEEL_PX_PER_NOTCH = 50.0
MAX_ACTIONS_PER_TICK = 8
# Queued actions carry editor buffers (save:/compile:), so the drain buffer
# must fit real strategies — the Rust side returns -5 when it does not fit.
ACTION_BUFFER_BYTES = 65536


class NativeViewError(RuntimeError):
    """The native Strategy Lab view library is unavailable or incompatible."""


def _lib_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_strategy_lab_view.dll",)
    if system == "darwin":
        return ("libvayren_strategy_lab_view.dylib",)
    return ("libvayren_strategy_lab_view.so",)


def find_view_library() -> Path:
    """Locate the view cdylib: env override, then repo release/debug targets."""
    override = os.environ.get(VIEW_LIB_ENV_OVERRIDE, "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise NativeViewError(f"{VIEW_LIB_ENV_OVERRIDE} points at a missing file: {override!r}")
    root = Path(__file__).resolve().parent.parent.parent.parent
    for profile in ("release", "debug"):
        for name in _lib_names():
            candidate = root / "rust" / "target" / profile / name
            if candidate.is_file():
                return candidate
    raise NativeViewError(
        "Native Strategy Lab view library not found. Build it first: "
        "`python scripts/build_rust.py` (requires a Rust toolchain)."
    )


def _bind(lib: Any) -> Any:
    """Declare the C ABI surface used below (names mirror the Rust exports)."""
    u32, i32, f32 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_float
    void_p = ctypes.c_void_p
    cstr = ctypes.c_char_p
    u8_p = ctypes.POINTER(ctypes.c_uint8)
    size = ctypes.c_size_t
    spec = {
        "vayren_strategy_lab_abi_version": (u32, []),
        "vayren_strategy_lab_view_create": (void_p, [u32, u32, f32]),
        "vayren_strategy_lab_view_destroy": (None, [void_p]),
        "vayren_strategy_lab_view_resize": (i32, [void_p, u32, u32, f32]),
        "vayren_strategy_lab_view_set_snapshot": (i32, [void_p, cstr]),
        "vayren_strategy_lab_view_tick": (i32, [void_p]),
        "vayren_strategy_lab_view_next_action": (i32, [void_p, u8_p, size]),
        "vayren_strategy_lab_view_render": (i32, [void_p, u8_p, size]),
        "vayren_strategy_lab_view_pointer_move": (i32, [void_p, f32, f32]),
        "vayren_strategy_lab_view_pointer_press": (i32, [void_p, f32, f32, i32]),
        "vayren_strategy_lab_view_pointer_release": (i32, [void_p, f32, f32, i32]),
        "vayren_strategy_lab_view_pointer_leave": (i32, [void_p]),
        "vayren_strategy_lab_view_scroll": (i32, [void_p, f32, f32, f32, f32]),
        "vayren_strategy_lab_view_key": (i32, [void_p, cstr, i32]),
    }
    for name, (restype, argtypes) in spec.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    if lib.vayren_strategy_lab_abi_version() != ABI_VERSION:
        raise NativeViewError(f"native Strategy Lab view ABI mismatch (expected {ABI_VERSION})")
    return lib


def load_view_library() -> Any:
    """Load the cdylib and verify the ABI handshake (fail-closed)."""
    path = find_view_library()
    try:
        lib = _bind(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeViewError(f"cannot load native Strategy Lab view {path}: {exc}") from exc
    return lib


def _fmt_money(value: Any) -> str:
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def strategy_lab_snapshot_dict(workspace: Any) -> dict[str, Any]:
    """Project the canonical Qt Strategy Lab state onto the bridge schema.

    Pure read-only structural pass-through of REAL facts (names, mtimes,
    config numbers, result metrics). Everything optional is guarded: a missing
    piece becomes an honest absence, never a placeholder. No formatting or
    derivation beyond raw display strings happens for the UI's benefit —
    number formatting lives in Rust; these strings are label data.
    """
    snap: dict[str, Any] = {
        "strategies": [],
        "selected_name": "",
        "mode": "buy",
        "run": "ready",
        "engine_wired": True,
        "outdated": False,
        "config": {},
        "results": None,
    }
    if workspace is None:
        return snap
    nav: Any = getattr(workspace, "left_nav", None)
    names: list[str] = []
    with contextlib.suppress(Exception):
        names = list(nav.names()) if nav is not None else []
    mtimes: dict[str, float] = {}
    with contextlib.suppress(Exception):
        mtimes = {str(n): float(m) for n, m in list(getattr(nav, "_items", []) or [])}
    favorites: set[str] = set()
    with contextlib.suppress(Exception):
        favorites = set(getattr(nav, "_favorites", set()) or set())
    rows = []
    for name in names:
        modified = ""
        stamp = mtimes.get(name, 0.0)
        if stamp:
            with contextlib.suppress(Exception):
                modified = datetime.fromtimestamp(stamp).strftime("%d %b %y")
        rows.append(
            {
                "name": name,
                "description": "",
                "tags": [],
                "version": "",
                "modified": modified,
                "last_backtest": "",
                "favorite": name in favorites,
            }
        )
    snap["strategies"] = rows
    selected = ""
    with contextlib.suppress(Exception):
        selected = (workspace.current_tab_name() or "").strip()
    if not selected:
        with contextlib.suppress(Exception):
            selected = str(nav.current_name() or "") if nav is not None else ""
    snap["selected_name"] = selected if selected in names else ""
    with contextlib.suppress(Exception):
        snap["mode"] = str(workspace.view_mode)
    snap["run"] = str(getattr(workspace, "_run_state", "ready"))
    with contextlib.suppress(Exception):
        stale = bool(getattr(workspace, "_stale_sides", ()))
        snap["outdated"] = stale and snap["run"] == "complete"
    config: dict[str, Any] = {}
    count = 0
    try:
        cfg = workspace.right_settings.current_config()
        symbols = [str(s) for s in (cfg.get("symbols") or []) if s]
        if not symbols and cfg.get("symbol"):
            symbols = [str(cfg["symbol"])]
        count = len(symbols)
        config["universe"] = (
            f"{count} STOCK" if count == 1 else f"{count} STOCKS" if count else "NO UNIVERSE"
        )
        config["timeframe"] = str(cfg.get("timeframe") or "—")
        start = str(cfg.get("start_date") or "")[:10]
        end = str(cfg.get("end_date") or "")[:10]
        config["dates"] = f"{start} → {end}" if start and end else "—"
        config["capital"] = _fmt_money(cfg.get("initial_capital"))
        slip, comm = cfg.get("slippage_pct"), cfg.get("commission_pct")
        if slip is not None and comm is not None:
            config["cost"] = f"{slip}% / {comm}%"
    except Exception:  # noqa: BLE001
        pass
    snap["config"] = config
    snap["results"] = _lab_result_snapshot(workspace)
    # Verdict pill facts (Qt interpretation layer) + batch progress line —
    # read-only reuse of the existing backend functions, never re-derived.
    verdict = ""
    verdict_note = ""
    verdict_tone = 0
    if snap["selected_name"]:
        with contextlib.suppress(Exception):
            from app.ui import lab_theme as lab_t
            from app.ui.strategy_lab_workspace import interpret_result

            v, note, colour = interpret_result(getattr(workspace, "_full_result", None))
            verdict, verdict_note = str(v), str(note)
            verdict_tone = {
                lab_t.POS: 1,
                lab_t.NEG: 3,
                lab_t.WARN: 2,
                lab_t.MUTED: 0,
            }.get(str(colour), 0)
    snap["verdict"] = verdict
    snap["verdict_note"] = verdict_note
    snap["verdict_tone"] = verdict_tone
    progress = ""
    if snap["run"] == "running":
        with contextlib.suppress(Exception):
            progress = str(workspace._topbar_status.text())
    snap["progress"] = progress
    if snap["results"] and snap["selected_name"]:
        for row in rows:
            if row["name"] == snap["selected_name"]:
                eq = str(config.get("timeframe") or "")
                row["last_backtest"] = f"{count} SYM · {eq}".strip(" ·")
    # ── parity surface: editor, editable config, ranking controls, drill-down,
    # compare, selection, run label, chart captions. All read-only reuse of the
    # existing Qt widgets/functions — the provider never derives or invents.
    snap["code"] = _lab_code(workspace)
    snap["params"] = _lab_params(workspace)
    snap["cfg_edit"] = _lab_cfg_edit(workspace)
    snap["rankby"] = _lab_rankby(workspace)
    snap["detail"] = _lab_detail(workspace)
    snap["buy"] = _lab_side_block(workspace, "buy")
    snap["sell"] = _lab_side_block(workspace, "sell")
    snap["compare"] = _lab_compare(workspace)
    snap["selected_trade"] = _lab_selected_trade(workspace)
    snap["trade_filter"] = _lab_trade_filter(workspace)
    snap["universe"] = _lab_universe(workspace)
    snap["run_label"] = _lab_run_label(workspace)
    snap["equity_summary"] = _lab_equity_summary(workspace)
    snap["drawdown_summary"] = _lab_drawdown_summary(workspace)
    return snap


def _lab_result_snapshot(workspace: Any) -> dict[str, Any] | None:
    """Raw numbers only; formatting happens in Rust. None = no honest data."""
    try:
        if str(getattr(workspace, "_run_state", "")) not in ("complete", "failed"):
            return None
        result = getattr(workspace, "_full_result", None)
        if result is None:
            return None
    except Exception:  # noqa: BLE001
        return None
    metrics: dict[str, Any] = {}
    m = getattr(result, "metrics", None)
    if m is not None:
        for key in (
            "net_profit",
            "total_trades",
            "profit_factor",
            "expectancy",
            "max_drawdown_pct",
            "sharpe_ratio",
            "avg_trade",
        ):
            metrics[key] = getattr(m, key, None)
        win_rate = getattr(m, "win_rate", None)
        metrics["win_rate"] = win_rate * 100.0 if win_rate is not None else None
    trades = []
    for t in list(getattr(result, "trades", ()) or [])[:200]:
        reason = getattr(t, "exit_reason", "")
        reason = getattr(reason, "value", reason)
        trades.append(
            {
                "symbol": str(getattr(t, "symbol", "") or ""),
                "side": str(getattr(t, "side", "") or ""),
                "entry_time": str(getattr(t, "entry_time", "") or ""),
                "exit_time": str(getattr(t, "exit_time", "") or ""),
                "entry_px": getattr(t, "entry_price", None),
                "exit_px": getattr(t, "exit_price", None),
                "pnl": getattr(t, "pnl", None),
                "r_multiple": getattr(t, "r_multiple", None),
                "bars": getattr(t, "bars_held", None),
                "reason": str(reason or ""),
                "winning": bool(getattr(t, "winning", False)),
            }
        )
    curve = [
        {
            "equity": getattr(p, "equity", None),
            "drawdown_pct": getattr(p, "drawdown_pct", None),
        }
        for p in list(getattr(result, "equity_curve", ()) or [])[:2000]
    ]
    ranking = []
    with contextlib.suppress(Exception):
        for r in workspace.right_settings.ranking.rows():
            wr = getattr(r, "win_rate", None)
            ranking.append(
                {
                    "rank": getattr(r, "rank", None),
                    "symbol": str(getattr(r, "symbol", "") or ""),
                    "status": str(getattr(r, "status", "") or ""),
                    "note": str(getattr(r, "note", "") or ""),
                    "net_profit": getattr(r, "net_profit", None),
                    "return_pct": getattr(r, "return_pct", None),
                    "total_trades": getattr(r, "total_trades", None),
                    "win_rate": wr * 100.0 if isinstance(wr, (int, float)) else None,
                    "profit_factor": getattr(r, "profit_factor", None),
                    "max_drawdown_pct": getattr(r, "max_drawdown_pct", None),
                    "sharpe_ratio": getattr(r, "sharpe_ratio", None),
                }
            )
    risk_notes: list[str] = []
    dd = metrics.get("max_drawdown_pct")
    if dd is not None:
        risk_notes.append(f"{len(ranking)} symbols analyzed · max drawdown {dd:.2f}%")
    sh = metrics.get("sharpe_ratio")
    if sh is not None:
        risk_notes.append(f"sharpe {sh:.2f} (per-trade, annualised)")
    return {
        "metrics": metrics,
        "trades": trades,
        "equity_curve": curve,
        "ranking": ranking,
        "risk_notes": risk_notes,
    }


def _lab_code(workspace: Any) -> str:
    """Current editor buffer (capped; the native EDITOR tab edits a copy and
    commits via save:/compile: — same content the Qt relays carry)."""
    with contextlib.suppress(Exception):
        code = workspace.center_detail.get_code()
        return str(code or "")[:200_000]
    return ""


def _lab_params(workspace: Any) -> list[dict[str, Any]]:
    """Parameter specs + current values (``ParamsPane.refresh`` derivation,
    reused — never re-derived here beyond calling the same compiler)."""
    try:
        from strategy.language import compile_strategy

        code = workspace.center_detail.get_code()
        compiled = compile_strategy(code or "")
        current = dict(workspace.center_detail.current_params() or {})
        entries: list[tuple[str, str, float]] = []
        seen: set[str] = set()
        for spec in getattr(compiled, "param_specs", ()) or ():
            if spec.key in seen:
                continue
            seen.add(spec.key)
            entries.append((spec.key, spec.label, float(spec.default)))
        if not entries:
            entries = [(k, k, float(v)) for k, v in compiled.param_defaults.items()]
        return [
            {"key": key, "label": str(label), "value": float(current.get(key, default))}
            for key, label, default in entries
        ]
    except Exception:  # noqa: BLE001
        return []


def _lab_cfg_edit(workspace: Any) -> dict[str, Any]:
    """Raw editable config: universe CSV, timeframe + items, ISO dates,
    capital number, and the panel's own inline error text (or empty)."""
    out: dict[str, Any] = {
        "universe_csv": "",
        "timeframe": "",
        "timeframes": [],
        "dates_start": "",
        "dates_end": "",
        "capital": None,
        "config_error": "",
    }
    try:
        panel = workspace.right_settings
        cfg = panel.current_config()
        symbols = [str(s) for s in (cfg.get("symbols") or []) if s]
        out["universe_csv"] = ",".join(symbols)
        out["timeframe"] = str(cfg.get("timeframe") or "")
        out["dates_start"] = str(cfg.get("start_date") or "")
        out["dates_end"] = str(cfg.get("end_date") or "")
        out["capital"] = cfg.get("initial_capital")
    except Exception:  # noqa: BLE001
        pass
    with contextlib.suppress(Exception):
        combo = workspace.right_settings._tf_combo
        out["timeframes"] = [str(combo.itemText(i)) for i in range(combo.count())]
    with contextlib.suppress(Exception):
        err = workspace.right_settings._error
        if err.isVisible():
            out["config_error"] = str(err.text() or "")
    return out


def _lab_rankby(workspace: Any) -> dict[str, Any]:
    """Ranking criterion dropdown state + search text + order flag — the Qt
    box order and handler semantics are reused verbatim by the sink."""
    out: dict[str, Any] = {"labels": [], "current": 0, "search": "", "desc": True}
    with contextlib.suppress(Exception):
        ranking = workspace.right_settings.ranking
        box = ranking._sort_box
        out["labels"] = [str(box.itemText(i)) for i in range(box.count())]
        out["current"] = int(box.currentIndex())
        out["search"] = str(ranking._search.text() or "")
        out["desc"] = bool(ranking._sort_desc)
    return out


def _lab_detail(workspace: Any) -> dict[str, Any] | None:
    """Selected-stock drill-down: the detail panel's own rendered title,
    trade stats, caption, six metric block texts, and spark values
    (decimated to ≤300 — the Qt spark decimates the same way at paint)."""
    try:
        ranking = workspace.right_settings.ranking
        symbol = ranking.selected_symbol()
        if not symbol:
            return None
        detail = ranking.detail
        metrics = []
        blocks = getattr(detail, "_blocks", {}) or {}
        for label in ("NET P&L", "RETURN", "WIN RATE", "PROFIT FACTOR", "MAX DD", "SHARPE"):
            block = blocks.get(label)
            value = ""
            if block is not None:
                with contextlib.suppress(Exception):
                    value = str(block._value.text() or "")
            metrics.append({"label": label, "value": value})
        values: list[float] = []
        with contextlib.suppress(Exception):
            values = [float(v) for v in (detail.spark._values or [])]
        if len(values) > 300:
            step = len(values) / 300
            values = [values[int(i * step)] for i in range(300)]
        with contextlib.suppress(Exception):
            return {
                "symbol": str(symbol),
                "title": str(detail.title.text() or ""),
                "stats": str(detail.trade_stats.text() or "")
                if detail.trade_stats.isVisible()
                else "",
                "caption": str(detail.caption.text() or ""),
                "metrics": metrics,
                "equity": values,
            }
    except Exception:  # noqa: BLE001
        pass
    return None


def _lab_raw_block(result: Any) -> dict[str, Any] | None:
    """Raw single-side block (metrics/trades/curve) for COMPARE BUY/SELL."""
    if result is None:
        return None
    metrics: dict[str, Any] = {}
    m = getattr(result, "metrics", None)
    if m is not None:
        for key in (
            "net_profit",
            "total_trades",
            "profit_factor",
            "expectancy",
            "max_drawdown_pct",
            "sharpe_ratio",
        ):
            metrics[key] = getattr(m, key, None)
        win_rate = getattr(m, "win_rate", None)
        metrics["win_rate"] = win_rate * 100.0 if win_rate is not None else None
    trades = []
    for t in list(getattr(result, "trades", ()) or [])[:200]:
        reason = getattr(t, "exit_reason", "")
        reason = getattr(reason, "value", reason)
        trades.append(
            {
                "symbol": str(getattr(t, "symbol", "") or ""),
                "side": str(getattr(t, "side", "") or ""),
                "entry_time": str(getattr(t, "entry_time", "") or ""),
                "exit_time": str(getattr(t, "exit_time", "") or ""),
                "entry_px": getattr(t, "entry_price", None),
                "exit_px": getattr(t, "exit_price", None),
                "pnl": getattr(t, "pnl", None),
                "r_multiple": getattr(t, "r_multiple", None),
                "bars": getattr(t, "bars_held", None),
                "reason": str(reason or ""),
                "winning": bool(getattr(t, "winning", False)),
            }
        )
    curve = [
        {
            "equity": getattr(p, "equity", None),
            "drawdown_pct": getattr(p, "drawdown_pct", None),
        }
        for p in list(getattr(result, "equity_curve", ()) or [])[:2000]
    ]
    return {"metrics": metrics, "trades": trades, "equity_curve": curve}


def _lab_side_block(workspace: Any, side: str) -> dict[str, Any] | None:
    with contextlib.suppress(Exception):
        result = workspace.buy_result if side == "buy" else workspace.sell_result
        return _lab_raw_block(result)
    return None


def _lab_compare(workspace: Any) -> dict[str, Any] | None:
    """COMPARE page facts from the already-fed hidden compare view: banner
    verdict/reason text, trade summary, stale notice, matrix cell texts +
    winner (read off the backend's own winner highlight), and the per-stock
    board (``ranking.rows()`` — the backend docstring shares this derivation
    with the board — plus ``_COMPARE_ROWS`` meta and raw values)."""
    try:
        view = workspace._compare_view
    except Exception:  # noqa: BLE001
        return None
    out: dict[str, Any] = {
        "banner_verdict": "",
        "banner_reason": "",
        "trade_summary": "",
        "stale_notice": "",
        "matrix": [],
        "board_scope": "",
        "board_leader": "",
        "board_rows": [],
        "ranking": [],
    }
    with contextlib.suppress(Exception):
        out["banner_verdict"] = str(view._banner._verdict.text() or "")
        out["banner_reason"] = str(view._banner._reason.text() or "")
    with contextlib.suppress(Exception):
        out["trade_summary"] = str(view._trade_summary.text() or "")
    with contextlib.suppress(Exception):
        if view._stale_notice.isVisible():
            out["stale_notice"] = str(view._stale_notice.text() or "")
    with contextlib.suppress(Exception):
        from app.ui.strategy_lab_workspace import _BUY_ACCENT, _METRIC_ORDER, _SELL_ACCENT

        for key in _METRIC_ORDER:
            buy_lbl = view._matrix._buy_labels[key]
            sell_lbl = view._matrix._sell_labels[key]
            buy_text, sell_text = str(buy_lbl.text()), str(sell_lbl.text())
            winner = ""
            if _BUY_ACCENT in buy_lbl.styleSheet():
                winner = "buy"
            elif _SELL_ACCENT in sell_lbl.styleSheet():
                winner = "sell"
            out["matrix"].append(
                {"key": str(key), "buy": buy_text, "sell": sell_text, "winner": winner}
            )
    with contextlib.suppress(Exception):
        from app.ui.strategy_lab_workspace import _COMPARE_ROWS

        board = view._comparison
        out["board_scope"] = str(board._scope.text() or "")
        out["board_leader"] = str(board._leader.text() or "")
        rows = list(workspace.right_settings.ranking.rows())
        for label, attr, kind, higher in _COMPARE_ROWS:
            out["board_rows"].append(
                {
                    "label": str(label),
                    "kind": str(kind),
                    "higher": higher,
                    "values": [
                        {"symbol": str(r.symbol), "value": getattr(r, attr, None)} for r in rows[:6]
                    ],
                }
            )
        for r in rows[:6]:
            wr = getattr(r, "win_rate", None)
            out["ranking"].append(
                {
                    "rank": getattr(r, "rank", None),
                    "symbol": str(getattr(r, "symbol", "") or ""),
                    "status": str(getattr(r, "status", "") or ""),
                    "note": str(getattr(r, "note", "") or ""),
                    "net_profit": getattr(r, "net_profit", None),
                    "return_pct": getattr(r, "return_pct", None),
                    "total_trades": getattr(r, "total_trades", None),
                    "win_rate": wr * 100.0 if isinstance(wr, (int, float)) else None,
                    "profit_factor": getattr(r, "profit_factor", None),
                    "max_drawdown_pct": getattr(r, "max_drawdown_pct", None),
                    "sharpe_ratio": getattr(r, "sharpe_ratio", None),
                }
            )
    if not out["banner_verdict"] and not out["matrix"]:
        return None
    return out


def _lab_universe(workspace: Any) -> dict[str, Any]:
    """The Market Watchlist universe + current selection (read-only reuse
    of the existing `WatchlistMultiSelect` — the single source of truth,
    never a second list)."""
    out: dict[str, Any] = {"symbols": [], "selected": []}
    with contextlib.suppress(Exception):
        available = workspace.right_settings._symbols.available_symbols
        out["symbols"] = [str(s) for s in available if s]
    with contextlib.suppress(Exception):
        out["selected"] = [str(s) for s in workspace.right_settings.selected_symbols()]
    return out


def _lab_selected_trade(workspace: Any) -> int:
    with contextlib.suppress(Exception):
        index = workspace.journal.selected_index()
        return int(index) if index is not None else -1
    return -1


def _lab_trade_filter(workspace: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"needle": "", "symbol": "ALL", "active": False}
    with contextlib.suppress(Exception):
        journal = workspace.journal
        out["needle"] = str(getattr(journal, "_needle", "") or "")
        out["symbol"] = str(journal.symbol_filter or "ALL")
        out["active"] = bool(out["needle"]) or out["symbol"] != "ALL"
    return out


def _lab_run_label(workspace: Any) -> str:
    """The real topbar RUN button text (RUN BUY / RUN SELL / RUN ALL /
    RUNNING… / ■ STOP morphs) — the native button echoes it verbatim."""
    with contextlib.suppress(Exception):
        return str(workspace._topbar_run.text() or "")
    return ""


def _lab_equity_summary(workspace: Any) -> str:
    with contextlib.suppress(Exception):
        return str(workspace._equity_summary.text() or "")
    return ""


def _lab_drawdown_summary(workspace: Any) -> str:
    """Same profile string the Qt drawdown chart paints (same function,
    same format) — the native caption echoes it verbatim."""
    try:
        from backtest.ui.analytics_views import _drawdown_profile

        result = getattr(workspace, "_full_result", None)
        curve = getattr(result, "equity_curve", None) if result is not None else None
        draws = [float(p.drawdown_pct) for p in (curve or [])]
        if len(draws) < 2:
            return ""
        max_dd, longest, recovered = _drawdown_profile(draws)
        return (
            f"MAX DRAWDOWN  -{max_dd:.2f}%   ·   LONGEST UNDERWATER  {longest} bars"
            f"   ·   {'RECOVERED' if recovered else 'NOT RECOVERED'}"
        )
    except Exception:  # noqa: BLE001
        return ""


def _lab_set_code(workspace: Any, code: str) -> None:
    """Replace the editor buffer through the typing path (dirty tracking +
    status chip), without recompiling params — exactly what keystrokes do.
    Params refresh on the save/compile relay paths, same as Qt."""
    editor = workspace.center_detail
    widget = editor.editor
    widget.blockSignals(True)
    try:
        widget.setPlainText(code)
    finally:
        widget.blockSignals(False)
    changed = getattr(editor, "_on_text_changed", None)
    if callable(changed):
        changed()


def apply_lab_strategy_action(workspace: Any, action: str) -> None:
    """Forward one queued Slint interaction to the existing Qt backend.

    Public methods and signals only — the backend keeps single ownership of
    business behavior (open, mode, tab, filter, run). Underscore-prefixed
    Qt members referenced here (``_row_menu_for``, ``_export_csv``,
    ``_on_topbar_run``, ``_emit_run_*``, ``_step_selection``) are the stable
    backend behavior entry points this bridge is owned by — same family as
    the previously bridged ``_topbar_status`` read.
    """
    if workspace is None or not action:
        return
    try:
        if action.startswith("select:"):
            index = int(action.split(":", 1)[1])
            names = list(workspace.left_nav.names())
            if 0 <= index < len(names):
                workspace.strategy_open_requested.emit(names[index])
        elif action.startswith("mode:"):
            kind = int(action.split(":", 1)[1])
            workspace.set_view_mode({0: "buy", 1: "sell", 2: "compare"}.get(kind, "buy"))
        elif action.startswith("tab:"):
            # Native tabs: 0 EDITOR (center column, always live in Qt — no
            # call), 1..4 → Qt result stack pages 0..3 (PERFORMANCE, TRADES,
            # EQUITY, DRAWDOWN).
            tab = int(action.split(":", 1)[1])
            if tab >= 1:
                workspace.show_result(tab - 1, expand=True)
        elif action.startswith("filter:"):
            kind = int(action.split(":", 1)[1])
            workspace.left_nav.set_lib_filter({1: "fav", 2: "recent"}.get(kind, "all"))
        elif action == "run":
            # Full topbar semantics (busy → STOP arm/emit, guards included).
            toggle = getattr(workspace, "_on_topbar_run", None)
            if callable(toggle):
                toggle()
        elif action.startswith("save:"):
            code = action.split(":", 1)[1]
            _lab_set_code(workspace, code)
            workspace.save_requested_relay.emit(code)
        elif action.startswith("compile:"):
            code = action.split(":", 1)[1]
            _lab_set_code(workspace, code)
            workspace.compile_requested_relay.emit(code)
        elif action == "save":
            workspace.save_requested_relay.emit(workspace.center_detail.get_code())
        elif action == "compile":
            workspace.compile_requested_relay.emit(workspace.center_detail.get_code())
        elif action == "new":
            workspace.left_nav.new_strategy_requested.emit()
        elif action == "runall":
            workspace._emit_run_all()
        elif action == "runbuy":
            workspace._emit_run_buy()
        elif action == "runsell":
            workspace._emit_run_sell()
        elif action.startswith("menu:"):
            menu_index = int(action.split(":", 1)[1])
            menu_names = list(workspace.left_nav.names())
            if 0 <= menu_index < len(menu_names):
                workspace.left_nav._row_menu_for(menu_names[menu_index])
        elif action.startswith("ranksel:"):
            symbol = action.split(":", 1)[1]
            if symbol:
                ranking = workspace.right_settings.ranking
                ranking.select_symbol(symbol)
                # Downstream (journal symbol filter, derived views) runs off
                # this signal in Qt — emit it so the click behaves identically.
                ranking.symbol_focused.emit(symbol)
        elif action.startswith("ranksearch:"):
            workspace.right_settings.ranking._search.setText(action.split(":", 1)[1])
        elif action.startswith("rankby:"):
            workspace.right_settings.ranking._sort_box.setCurrentIndex(int(action.split(":", 1)[1]))
        elif action == "ranktoggle":
            ranking = workspace.right_settings.ranking
            ranking._on_header_clicked(ranking._sort_col)
        elif action.startswith("tradesel:"):
            workspace.journal.set_selected_index(int(action.split(":", 1)[1]))
        elif action.startswith("tradefocus:"):
            workspace.trade_focus.emit(int(action.split(":", 1)[1]))
        elif action == "tradeup":
            workspace.journal._step_selection(-1)
        elif action == "tradedown":
            workspace.journal._step_selection(1)
        elif action == "tradeenter":
            index = workspace.journal.selected_index()
            if index is not None:
                workspace.trade_focus.emit(index)
        elif action.startswith("tradefilter:"):
            workspace.journal._filter.setText(action.split(":", 1)[1])
        elif action.startswith("paramset:"):
            _, key, value = action.split(":", 2)
            try:
                amount = float(value)
            except (TypeError, ValueError):
                return
            detail = workspace.center_detail
            params = dict(detail.current_params() or {})
            params[key] = amount
            detail.set_params(params)
            detail.param_changed.emit(key, amount)
        elif action.startswith("settimeframe:"):
            workspace.right_settings.select_timeframe(action.split(":", 1)[1])
        elif action.startswith("symbols:"):
            raw = action.split(":", 1)[1]
            workspace.right_settings.set_selected_symbols(
                [s.strip() for s in raw.split(",") if s.strip()]
            )
        elif action.startswith("capital:"):
            workspace.right_settings.set_capital(action.split(":", 1)[1])
        elif action.startswith("dates:"):
            _, start, end = action.split(":", 2)
            workspace.right_settings.set_dates(start.strip(), end.strip())
        elif action == "exporttrades":
            workspace.journal._export_csv()
    except Exception:  # noqa: BLE001 (a failed action must never break the pump)
        logger.debug("slint strategy lab host: action failed: %s", action, exc_info=True)


class SlintStrategyLabHost(QWidget):
    """Plain viewport blitting native Slint Strategy Lab frames.

    Zero Strategy Lab presentation lives here: pixels arrive from Rust, input
    events leave for Rust, backend facts arrive via ``state_provider`` (the
    canonical Qt workspace object is the state holder; it is intentionally NOT
    mounted as a widget). When the cdylib is unavailable the widget stays dark
    with one muted status line (honest bridge health) and logs the cause.
    """

    def __init__(
        self,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        action_sink: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = state_provider
        self._action_sink = action_sink
        self._lib: Any | None = None
        self._view: Any | None = None
        self._frame = bytearray()
        self._frame_w = 0
        self._frame_h = 0
        self._last_pushed = ""
        self._provider_ticks = 0
        self._pump: QTimer | None = None
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        try:
            self._lib = load_view_library()
        except NativeViewError as exc:
            logger.warning("slint strategy lab host unavailable: %s", exc)

    @property
    def is_native_available(self) -> bool:
        """Whether the native view library loaded (bridge health probe)."""
        return self._lib is not None

    # ── lifecycle ──

    def _dpr(self) -> float:
        try:
            return max(0.25, min(8.0, float(self.devicePixelRatio())))
        except (TypeError, ValueError):
            return 1.0

    def _ensure_view(self) -> bool:
        if self._lib is None or self._view is not None:
            return self._view is not None
        dpr = self._dpr()
        width = max(1, int(round(self.width() * dpr)))
        height = max(1, int(round(self.height() * dpr)))
        handle = self._lib.vayren_strategy_lab_view_create(width, height, dpr)
        if not handle:
            logger.warning("slint strategy lab host: native view creation failed")
            return False
        self._view = handle
        self._alloc_frame(width, height)
        self._push_snapshot(force=True)
        return True

    def _alloc_frame(self, width: int, height: int) -> None:
        if width != self._frame_w or height != self._frame_h:
            self._frame_w, self._frame_h = width, height
            self._frame = bytearray(width * height * 3)

    def showEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if self._ensure_view() and self._lib is not None and self._view is not None:
            self._lib.vayren_strategy_lab_view_resize(
                self._view, self._frame_w, self._frame_h, self._dpr()
            )
        if self._pump is None:
            pump = QTimer(self)
            pump.setInterval(PUMP_INTERVAL_MS)
            pump.timeout.connect(self._on_pump)
            pump.start()
            self._pump = pump

    def destroy_view(self) -> None:
        """Release the native view (idempotent; used by tests/teardown)."""
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_destroy(self._view)
        self._view = None

    # ── data feed + action drain ──

    def _push_snapshot(self, force: bool = False) -> None:
        if self._lib is None or self._view is None or self._provider is None:
            return
        try:
            state = self._provider()
        except Exception:  # noqa: BLE001 (provider contract: never raises; stay safe anyway)
            logger.debug("slint strategy lab host: state provider failed", exc_info=True)
            return
        try:
            payload = json.dumps(state, sort_keys=True, default=str)
        except (TypeError, ValueError):
            logger.debug("slint strategy lab host: snapshot not serializable", exc_info=True)
            return
        if not force and payload == self._last_pushed:
            return
        code = self._lib.vayren_strategy_lab_view_set_snapshot(self._view, payload.encode("utf-8"))
        if code == 0:
            self._last_pushed = payload
        else:
            logger.debug("slint strategy lab host: snapshot rejected (code %s)", code)

    def _drain_actions(self) -> None:
        if self._lib is None or self._view is None or self._action_sink is None:
            return
        # NOTE: the buffer MUST be a c_uint8 array (not create_string_buffer:
        # a c_char array raises TypeError against the u8_p ABI slot and the
        # old suppress swallowed it — silently wedging every native action).
        buffer = (ctypes.c_uint8 * ACTION_BUFFER_BYTES)()
        for _ in range(MAX_ACTIONS_PER_TICK):
            popped = -1
            with contextlib.suppress(Exception):
                popped = self._lib.vayren_strategy_lab_view_next_action(
                    self._view, buffer, len(buffer)
                )
            if popped <= 0:
                if popped not in (0, -5):
                    logger.debug("slint strategy lab host: action drain failed (code %s)", popped)
                return
            self._action_sink(bytes(buffer[: int(popped)]).decode("utf-8", "replace"))

    def _on_pump(self) -> None:
        if self._lib is None or self._view is None:
            return
        with contextlib.suppress(Exception):
            self._lib.vayren_strategy_lab_view_tick(self._view)
            self._drain_actions()
            self._provider_ticks += 1
            if self._provider_ticks >= PROVIDER_POLL_TICKS:
                self._provider_ticks = 0
                self._push_snapshot()
            width = max(1, int(round(self.width() * self._dpr())))
            height = max(1, int(round(self.height() * self._dpr())))
            self._alloc_frame(width, height)
            painted = self._lib.vayren_strategy_lab_view_render(
                self._view,
                (ctypes.c_uint8 * len(self._frame)).from_buffer(self._frame),
                len(self._frame),
            )
            if painted:
                self.update()

    # ── presentation: blit only ──

    def paintEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        painter = QPainter(self)
        if self._lib is None or self._view is None or not self._frame:
            painter.fillRect(self.rect(), self.palette().color(self.backgroundRole()))
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                "Native Strategy Lab view unavailable — build the Rust workspace.",
            )
            painter.end()
            return
        image = QImage(
            self._frame,
            self._frame_w,
            self._frame_h,
            self._frame_w * 3,
            QImage.Format.Format_RGB888,
        )
        image.setDevicePixelRatio(self._dpr())
        painter.drawImage(0, 0, image)
        painter.end()

    # ── input forwarding (1:1, logical units) ──

    @staticmethod
    def _button(button: Qt.MouseButton) -> int | None:
        if button == Qt.MouseButton.LeftButton:
            return 0
        if button == Qt.MouseButton.RightButton:
            return 1
        if button == Qt.MouseButton.MiddleButton:
            return 2
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mouseMoveEvent(event)
        if self._lib is not None and self._view is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_pointer_move(self._view, pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mousePressEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_pointer_press(
                    self._view, pos.x(), pos.y(), button
                )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_pointer_release(
                    self._view, pos.x(), pos.y(), button
                )

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_pointer_leave(self._view)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt override)
        super().wheelEvent(event)
        if self._lib is not None and self._view is not None:
            delta = event.angleDelta()
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_scroll(
                    self._view,
                    pos.x(),
                    pos.y(),
                    delta.x() / 120.0 * WHEEL_PX_PER_NOTCH,
                    -delta.y() / 120.0 * WHEEL_PX_PER_NOTCH,
                )

    # Control keys never reach Slint as text (arrows/shortcuts carry no
    # editable character): they travel in a tiny grammar the Rust view parses
    # (`KEY+<qt code>`, `CTRL+<qt code>`) and turns into queued backend
    # actions. Plain text keeps the existing 1:1 path untouched.
    _CONTROL_KEYS = frozenset({16777235, 16777237, 16777220, 16777221, 16777216})

    @staticmethod
    def _key_payload(event: QKeyEvent) -> bytes:
        code = int(event.key())
        mods = event.modifiers()
        ctrl = bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if ctrl and code in (83, 16777220, 16777221):  # Ctrl+S, Ctrl+Return/Enter
            return f"CTRL+{code}".encode()
        if code in SlintStrategyLabHost._CONTROL_KEYS and not event.text():
            return f"KEY+{code}".encode()
        return event.text().encode()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyPressEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_key(self._view, self._key_payload(event), 1)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyReleaseEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_key(self._view, self._key_payload(event), 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if self._lib is not None and self._view is not None:
            dpr = self._dpr()
            width = max(1, int(round(self.width() * dpr)))
            height = max(1, int(round(self.height() * dpr)))
            self._alloc_frame(width, height)
            with contextlib.suppress(Exception):
                self._lib.vayren_strategy_lab_view_resize(self._view, width, height, dpr)
