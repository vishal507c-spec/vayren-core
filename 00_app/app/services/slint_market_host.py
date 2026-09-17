"""Slint Market host — dumb viewport for the native view (no UI here).

The native Market UI (layout, components, styling, state, interaction)
lives 100% in Rust+Slint (`rust/vayren-shell/ui/market.slint` +
`rust/vayren-market-view`). This module only:

- loads the `vayren_market_view` cdylib (fail-closed, same handshake
  discipline as the Portfolio/LIVE/Strategy Lab hosts),
- blits its RGB frames into a plain QWidget (no Market painting, no
  layout, no business logic),
- forwards input events (mouse/hover/wheel/keys/resize) 1:1 in logical units,
- pushes backend snapshots produced by :func:`market_snapshot_dict` and
  drains queued user actions into :func:`apply_market_action`.

The existing Market backend (EventBus loaders + ChartEngine + ChartWindow's
retained widgets) stays the single owner of data and behavior: the snapshot
is a read-only projection of REAL facts (symbols, quotes, bars, timeframes,
indicator visibility). Missing pieces degrade to honest absence (the native
screen shows its loading/empty state) — nothing is ever invented. All number
formatting lives in Rust.

Bridge snapshot schema (owned here; Rust parses defensively via
`market::apply_snapshot_json`): ``symbols[]{symbol,price,change_pct}``,
``selected_symbol``, ``watchlists[]``, ``active_watchlist``,
``timeframes[]``, ``timeframe``, ``exchange``, ``bars`` (null | [] |
``[{time,open,high,low,close,volume}]`` raw numbers), ``status``
(ready|loading|empty|error), ``status_detail``, ``indicators`` (name->bool),
``volume_visible``, ``bars_total`` (full backend count when the ``bars`` tail is
wrapped to the viewable window).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import platform
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
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
VIEW_LIB_ENV_OVERRIDE = "VAYREN_MARKET_VIEW_LIB"
PUMP_INTERVAL_MS = 33
PROVIDER_POLL_TICKS = 10
WHEEL_PX_PER_NOTCH = 50.0
MAX_ACTIONS_PER_TICK = 8
ACTION_BUFFER = 1024


class NativeViewError(RuntimeError):
    """The native Market view library is unavailable or incompatible."""


def _lib_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_market_view.dll",)
    if system == "darwin":
        return ("libvayren_market_view.dylib",)
    return ("libvayren_market_view.so",)


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
        "Native Market view library not found. Build it first: "
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
        "vayren_market_abi_version": (u32, []),
        "vayren_market_view_create": (void_p, [u32, u32, f32]),
        "vayren_market_view_destroy": (None, [void_p]),
        "vayren_market_view_resize": (i32, [void_p, u32, u32, f32]),
        "vayren_market_view_set_snapshot": (i32, [void_p, cstr]),
        "vayren_market_view_tick": (i32, [void_p]),
        "vayren_market_view_next_action": (i32, [void_p, u8_p, size]),
        "vayren_market_view_render": (i32, [void_p, u8_p, size]),
        "vayren_market_view_pointer_move": (i32, [void_p, f32, f32]),
        "vayren_market_view_pointer_press": (i32, [void_p, f32, f32, i32]),
        "vayren_market_view_pointer_release": (i32, [void_p, f32, f32, i32]),
        "vayren_market_view_pointer_leave": (i32, [void_p]),
        "vayren_market_view_scroll": (i32, [void_p, f32, f32, f32, f32]),
        "vayren_market_view_key": (i32, [void_p, cstr, i32]),
    }
    for name, (restype, argtypes) in spec.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    if lib.vayren_market_abi_version() != ABI_VERSION:
        raise NativeViewError(f"native Market view ABI mismatch (expected {ABI_VERSION})")
    return lib


def load_view_library() -> Any:
    """Load the cdylib and verify the ABI handshake (fail-closed)."""
    path = find_view_library()
    try:
        lib = _bind(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeViewError(f"cannot load native Market view {path}: {exc}") from exc
    return lib


def market_snapshot_dict(window: Any) -> dict[str, Any]:
    """Project the real Market backend state onto the bridge schema.

    Pure read-only pass-through of facts the retained Qt widgets already hold
    (watchlist symbols + quotes, timeframe toolbar, loaded chart model,
    indicator visibility). Every access is guarded: a missing piece becomes
    an honest absence (``bars: null`` → native loading/empty state), never a
    placeholder and never invented numbers.
    """
    snap: dict[str, Any] = {
        "symbols": [],
        "selected_symbol": "",
        "watchlists": [],
        "active_watchlist": "",
        "timeframes": [],
        "timeframe": "",
        "exchange": "",
        "bars": None,
        "status": "loading",
        "indicators": {},
        "strategies": [],
        "plot_series": [],
        "trades": [],
        "focused_trade": None,
        "trade_context": {"visible": False},
    }
    if window is None:
        return snap
    watchlist: Any = getattr(window, "watchlist", None)
    quotes: dict[str, Any] = {}
    if watchlist is not None:
        with contextlib.suppress(Exception):
            quotes = dict(getattr(watchlist, "_quotes", {}) or {})
        with contextlib.suppress(Exception):
            snap["symbols"] = [
                {
                    "symbol": str(sym),
                    "price": getattr(quotes.get(str(sym)), "price", None),
                    "change_pct": getattr(quotes.get(str(sym)), "change_pct", None),
                }
                for sym in watchlist.symbols
            ]
        with contextlib.suppress(Exception):
            snap["watchlists"] = [str(w) for w in watchlist.watchlists]
        with contextlib.suppress(Exception):
            snap["active_watchlist"] = str(watchlist.active_watchlist or "")
    with contextlib.suppress(Exception):
        snap["selected_symbol"] = str(window.current_symbol or "")
    toolbar: Any = getattr(window, "toolbar", None)
    if toolbar is not None:
        with contextlib.suppress(Exception):
            snap["timeframes"] = [str(t) for t in toolbar.timeframes]
    with contextlib.suppress(Exception):
        snap["timeframe"] = str(window.current_timeframe or "")
    # Strategy names available in the INDICATORS popup (injected by bootstrap).
    with contextlib.suppress(Exception):
        snap["strategies"] = [
            str(s) for s in (getattr(window.indicators.panel, "_strategy_names", ()) or ())
        ]
    widget: Any = getattr(window, "_widget", None)
    model: Any = getattr(widget, "_model", None) if widget is not None else None
    offset = 0
    all_bars: tuple = ()
    tail: tuple = ()
    if model is not None and getattr(model, "bars", None):
        snap["status"] = "ready"
        snap["timeframe"] = str(getattr(model, "timeframe", "") or snap["timeframe"])
        snap["exchange"] = str(getattr(model, "exchange", "") or "")
        snap["selected_symbol"] = str(getattr(model, "symbol", "") or snap["selected_symbol"])
        all_bars = tuple(model.bars)
        tail = all_bars[-1800:]
        offset = len(all_bars) - len(tail)
        snap["bars"] = [
            {
                "time": str(getattr(bar, "timestamp", "")),
                "open": getattr(bar, "open", None),
                "high": getattr(bar, "high", None),
                "low": getattr(bar, "low", None),
                "close": getattr(bar, "close", None),
                "volume": getattr(bar, "volume", None),
            }
            for bar in tail
        ]
    else:
        snap["status"] = "empty" if snap["selected_symbol"] else "loading"
    if widget is not None:
        with contextlib.suppress(Exception):
            visibility = dict(widget.indicator_visibility)
            snap["indicators"] = {str(k): bool(v) for k, v in visibility.items()}
        # Real strategy plot series (owner-aware PlotOverlay._series), with
        # absolute bar indices re-based onto the delivered tail window.
        with contextlib.suppress(Exception):
            series_rows: list[dict[str, Any]] = []
            for overlay in list(getattr(widget, "_overlays", {}).values()):
                raw = getattr(overlay, "_series", None)
                if not isinstance(raw, dict):
                    continue
                meta = getattr(overlay, "_meta", {}) or {}
                for (owner, title), points in raw.items():
                    pts = [
                        [int(idx) - offset, float(val)]
                        for idx, val in sorted((points or {}).items())
                        if int(idx) - offset >= 0 and val is not None
                    ]
                    if pts:
                        entry = meta.get((owner, title), {}) or {}
                        series_rows.append(
                            {
                                "owner": str(owner),
                                "title": str(title),
                                "points": pts,
                                "extend": str(entry.get("extend", "none") or "none"),
                                "cap": entry.get("extend_bars", None),
                            }
                        )
            snap["plot_series"] = series_rows
        # Strategy-owned visuals (PlotOverlay store: markers, EOD pills, rays)
        # — the same indexed query the painter uses, re-based. Records carry
        # their render layer so the native side replays the painter's
        # deterministic color rotation in snapshot order.
        with contextlib.suppress(Exception):
            markers: list[dict[str, Any]] = []
            rays: list[dict[str, Any]] = []
            total = len(all_bars) if model is not None and getattr(model, "bars", None) else 0
            for overlay in list(getattr(widget, "_overlays", {}).values()):
                store = getattr(overlay, "_store", None)
                query = getattr(store, "query_visible", None)
                if not callable(query):
                    continue
                is_visible = getattr(overlay, "_is_visible", None)
                try:
                    # Full-range order (all types) replicates the painter's
                    # deterministic color rotation; kinds filtered below.
                    records: Any = query(0, total)
                except Exception:  # noqa: BLE001
                    continue
                order = -1
                for record in records or ():
                    order += 1
                    source = str(getattr(record, "source_strategy", "") or "")
                    if callable(is_visible):
                        try:
                            if not is_visible(source):
                                continue
                        except Exception:  # noqa: BLE001
                            pass
                    kind = str(getattr(record, "plot_type", "") or "").upper()
                    layer = getattr(record, "layer", 60)
                    try:
                        layer = int(layer)
                    except (TypeError, ValueError):
                        layer = 60
                    if kind == "RAY":
                        start = getattr(record, "start_bar", None)
                        price = getattr(record, "start_price", None)
                        if price is None:
                            price = getattr(record, "price", None)
                        cap = getattr(record, "extend_bars", None)
                        if not isinstance(start, int) or start - offset < 0:
                            continue
                        try:
                            price_f = float(price)  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            continue
                        rays.append(
                            {
                                "start": int(start) - offset,
                                "price": price_f,
                                "cap": int(cap) if isinstance(cap, int) else -1,
                                "layer": layer,
                                "order": order,
                            }
                        )
                        continue
                    bar = getattr(record, "bar_index", None)
                    if bar is None:
                        bar = getattr(record, "anchor_bar", None)
                    price = getattr(record, "price", None)
                    if price is None:
                        price = getattr(record, "start_price", None)
                    if not isinstance(bar, int) or bar - offset < 0:
                        continue
                    try:
                        price_f = float(price)  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        continue
                    marker = str(getattr(record, "marker_type", "") or "").upper()
                    if kind == "LABEL":
                        marker = "DOT"
                    markers.append(
                        {
                            "bar": int(bar) - offset,
                            "price": price_f,
                            "kind": marker,
                            "text": str(getattr(record, "text", "") or ""),
                            "layer": layer,
                            "order": order,
                        }
                    )
            snap["strategy_markers"] = markers
            snap["strategy_rays"] = rays
        # Real overlay trades (TradeOverlay._trades). Positions resolve through
        # chart timestamps (the codebase-wide cross-basis key: replay indices
        # are window-relative and must never index chart bars). Covered flags
        # evaluate backend-side in replay basis — the exact Qt rule.
        with contextlib.suppress(Exception):
            overlay = getattr(widget, "_overlay", None)
            trades = getattr(overlay, "_trades", ()) or ()
            tail_times = [str(getattr(bar, "timestamp", "") or "") for bar in (tail or [])]
            time_pos: dict[str, int] = {}
            for pos, stamp in enumerate(tail_times):
                time_pos.setdefault(stamp, pos)
                time_pos.setdefault(stamp[:16], pos)

            def _position(stamp: Any) -> int | None:
                text = str(stamp or "")
                if text in time_pos:
                    return time_pos[text]
                short = text[:16]
                return time_pos.get(short)

            covered_raw: Any = getattr(overlay, "covered_bars", None)
            if callable(covered_raw):
                covered_raw = covered_raw()
            covered_set = {
                int(b)
                for b in (covered_raw or ())
                if isinstance(b, int) and not isinstance(b, bool)
            }

            def _covered(index: Any) -> bool:
                return (
                    isinstance(index, int)
                    and not isinstance(index, bool)
                    and int(index) in covered_set
                )

            snap["trades"] = [
                {
                    "side": str(getattr(t, "side", "") or ""),
                    "entry_price": getattr(t, "entry_price", None),
                    "exit_price": getattr(t, "exit_price", None),
                    "winning": bool(getattr(t, "winning", False)),
                    "entry_pos": _position(getattr(t, "entry_time", "")),
                    "exit_pos": _position(getattr(t, "exit_time", "")),
                    "entry_covered": _covered(getattr(t, "entry_index", None)),
                    "exit_covered": _covered(getattr(t, "exit_index", None)),
                    "exit_reason": str(getattr(t, "exit_reason", "") or ""),
                }
                for t in trades
            ]
            focused_trade = getattr(overlay, "focused_trade", None)
            focused_index = getattr(overlay, "focused_index", None)
            if focused_trade is not None:
                snap["focused"] = {
                    "side": str(getattr(focused_trade, "side", "") or ""),
                    "entry_pos": _position(getattr(focused_trade, "entry_time", "")),
                    "exit_pos": _position(getattr(focused_trade, "exit_time", "")),
                    "entry_price": getattr(focused_trade, "entry_price", None),
                    "exit_price": getattr(focused_trade, "exit_price", None),
                    "winning": bool(getattr(focused_trade, "winning", False)),
                    "no": focused_index if isinstance(focused_index, int) else None,
                }
            else:
                snap["focused"] = None
            focused = getattr(overlay, "focused_index", None)
            if callable(focused):
                focused = focused()
            snap["focused_trade"] = focused if isinstance(focused, int) else None
    # Trade context strip facts (labels the retained Qt panel already renders).
    with contextlib.suppress(Exception):
        panel = getattr(window, "_trade_context_panel", None)
        if panel is not None and panel.isVisible():
            strip = getattr(panel, "_strip", None)
            snap["trade_context"] = {
                "visible": strip is not None and strip.isVisible(),
                "trade": str(panel._trade_label.text()),
                "symbol_side": str(panel._symbol_label.text()),
                "time": str(panel._time_label.text()),
                "pnl": str(panel._pnl_label.text()),
                "r": str(panel._r_label.text()),
            }
    snap["download"] = _download_snapshot(window)
    return snap


def _text(widget: Any) -> str:
    """`.text()` on a label/button, or `.toPlainText()` on an edit, guarded."""
    if widget is None:
        return ""
    with contextlib.suppress(Exception):
        if hasattr(widget, "toPlainText"):
            return str(widget.toPlainText())
        return str(widget.text())
    return ""


def _download_snapshot(window: Any) -> dict[str, Any]:
    """Read-only projection of the retained HistoricalDownloadPanel + the
    provider credentials manager. Every access is guarded; the panel is the
    authoritative state holder (it stays subscribed to the download events), so
    progress / plan / status / coverage / provider / log are its REAL live
    values. Nothing is invented. The download panel lives at `window._download`.
    """
    dpanel = getattr(window, "_download", None)
    if dpanel is None:
        return {}
    out: dict[str, Any] = {
        "busy": False,
        "interval_items": [],
        "interval_index": 0,
        "universe": [],
        "selected": [],
        "from_display": "",
        "to_display": "",
        "calendar": {},
        "plan": {},
        "status": {},
        "brokers": [],
        "broker_index": 0,
        "log": [],
        "credentials": {"fields": [], "has_stored": False, "status": "", "close": False},
    }
    console = getattr(dpanel, "_panel", None)  # DownloadPanel
    status = getattr(dpanel, "_status", None)  # StatusView
    logpanel = getattr(dpanel, "_log_panel", None)  # LogPanel
    if console is not None:
        out["busy"] = bool(getattr(console, "_busy", False))
        with contextlib.suppress(Exception):
            combo = console._interval
            out["interval_items"] = [combo.itemText(i) for i in range(combo.count())]
            out["interval_index"] = int(combo.currentIndex())
        with contextlib.suppress(Exception):
            stocks = console._stocks
            out["universe"] = list(stocks.symbols)
            out["selected"] = list(stocks.selected_symbols)
        with contextlib.suppress(Exception):
            out["from_display"] = console._from_edit.date().toString("dd MMM yyyy")
            out["to_display"] = console._to_edit.date().toString("dd MMM yyyy")
        with contextlib.suppress(Exception):
            for edit, key in ((console._from_edit, "from"), (console._to_edit, "to")):
                q = edit.date()
                out["calendar"][key] = {"year": q.year(), "month": q.month(), "day": q.day()}
        out["plan"] = {
            "stocks": _text(getattr(console, "_plan_stocks", None)),
            "interval": _text(getattr(console, "_plan_interval", None)),
            "range": _text(getattr(console, "_plan_range", None)),
            "days": _text(getattr(console, "_plan_days", None)),
            "rows": _text(getattr(console, "_plan_rows", None)),
            "error": _text(getattr(console, "_plan_error", None)),
        }
    if status is not None:
        s = {
            "mode": str(getattr(status, "_mode", "idle")),
            "run_status": _text(getattr(status, "_run_status", None)),
            "run_symbol": _text(getattr(status, "_run_symbol", None)),
            "run_interval": _text(getattr(status, "_run_interval", None)),
            "run_range": _text(getattr(status, "_run_range", None)),
            "run_chunk": _text(getattr(status, "_run_chunk", None)),
            "run_rows": _text(getattr(status, "_run_rows", None)),
            "run_coverage": _text(getattr(status, "_run_coverage", None)),
            "progress_pct": 0.0,
            "progress_note": _text(getattr(status, "_progress_note", None)),
            "perf_rows": _text(getattr(status, "_perf_rows", None)),
            "perf_elapsed": _text(getattr(status, "_perf_elapsed", None)),
            "perf_eta": _text(getattr(status, "_perf_eta", None)),
            "perf_size": _text(getattr(status, "_perf_size", None)),
            "complete_status": _text(getattr(status, "_complete_status", None)),
            "complete_rows": _text(getattr(status, "_complete_rows", None)),
            "complete_coverage": _text(getattr(status, "_complete_coverage", None)),
            "complete_duration": _text(getattr(status, "_complete_duration", None)),
            "complete_size": _text(getattr(status, "_complete_size", None)),
            "error_line": _text(getattr(status, "_error_line", None)),
            "error_detail": _text(getattr(status, "_error_detail", None)),
            "cov_symbol": _text(getattr(status, "_cov_symbol", None)),
            "cov_interval": _text(getattr(status, "_cov_interval", None)),
            "cov_range": _text(getattr(status, "_cov_range", None)),
            "cov_coverage": _text(getattr(status, "_coverage_label", None)),
            "cov_rows": _text(getattr(status, "_cov_rows", None)),
            "provider_name": "Zerodha",
            "provider_status": _text(getattr(status, "_provider_status", None)),
            "provider_label": _text(getattr(status, "_provider_label", None)),
            "provider_detail": _text(getattr(status, "_provider_detail", None)),
            "configure_visible": _visible(getattr(status, "_configure_button", None)),
            "advanced_visible": _visible(getattr(status, "_advanced_button", None)),
            "advanced_open": _visible(getattr(status, "_provider_detail", None)),
            "env_visible": _visible(getattr(status, "_env_fallback", None)),
            "broker_caps": _text(getattr(status, "_broker_caps", None)),
            "broker_error": _text(getattr(status, "_broker_error", None)),
        }
        with contextlib.suppress(Exception):
            s["progress_pct"] = float(status._progress.value())
        out["status"] = s
        with contextlib.suppress(Exception):
            combo = status._broker_combo
            out["brokers"] = [combo.itemText(i) for i in range(combo.count())]
            out["broker_index"] = int(combo.currentIndex())
    if logpanel is not None:
        with contextlib.suppress(Exception):
            out["log"] = _text(logpanel.log).splitlines()
        out["log_expanded"] = bool(getattr(logpanel, "expanded", False))
    # Credentials modal: read the manager schema live (never a duplicated form).
    mgr = getattr(status, "_credentials_manager", None)
    if mgr is not None:
        with contextlib.suppress(Exception):
            values = mgr.load_values()
            out["credentials"] = {
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "secret": bool(f.secret),
                        "value": str(values.get(f.key, "")),
                    }
                    for f in mgr.fields
                ],
                "has_stored": bool(mgr.has_stored()),
                "status": str(getattr(window, "_dl_cred_status", "")),
                "close": bool(getattr(window, "_dl_cred_close", False)),
            }
            if getattr(window, "_dl_cred_close", False):
                window._dl_cred_close = False
    return out


def _visible(widget: Any) -> bool:
    with contextlib.suppress(Exception):
        if widget is not None:
            return bool(widget.isVisible())
    return False


def apply_market_action(window: Any, action: str) -> None:
    """Forward one queued Slint interaction to the existing Market backend.

    Public methods and the same signals a user click emits — the retained Qt
    widgets keep single ownership of behavior (symbol load, timeframe load,
    watchlist add/remove/switch, indicator add/visibility/settings/source/
    remove, reset view, trade-context navigation). The ⚙ and `{}` indicator
    buttons re-emit the visibility panel's ``settings_requested`` /
    ``source_requested`` signals, so they reach the identical legacy handlers
    a Qt toolbar click reaches (no settings/source surface is invented).
    """
    if window is None or not action:
        return
    try:
        widget = getattr(window, "_widget", None)
        if action.startswith("select:"):
            symbol = action.split(":", 1)[1]
            if symbol:
                window.watchlist.symbol_selected.emit(symbol)
        elif action.startswith("timeframe:"):
            timeframe = action.split(":", 1)[1]
            if timeframe:
                window.toolbar.timeframe_selected.emit(timeframe)
        elif action == "watchlist:add":
            window.watchlist.add_watchlist()
        elif action == "watchlist:remove":
            window.watchlist.remove_active_watchlist()
        elif action.startswith("watchlist:select:"):
            name = action.split(":", 2)[2]
            if name:
                window.watchlist._set_active(name)
        elif action.startswith("indicator:add:"):
            if widget is not None:
                widget.add_indicator(action.split(":", 2)[2])
        elif action.startswith("indicator:vis:"):
            if widget is not None:
                name = action.split(":", 2)[2]
                widget.set_indicator_visible(name, not widget.is_indicator_visible(name))
        elif action.startswith("indicator:settings:"):
            # Legacy ⚙ path: re-emit the visibility panel's settings_requested
            # signal — the SAME path a Qt toolbar click takes (the retained
            # widget owns the response; no surface is invented here).
            if widget is not None:
                name = action.split(":", 2)[2]
                if name:
                    widget.visibility_panel.settings_requested.emit(name)
        elif action.startswith("indicator:source:"):
            # Legacy `{}` path: re-emit source_requested (same handler the Qt
            # toolbar button reaches). The retained widget owns the response.
            if widget is not None:
                name = action.split(":", 2)[2]
                if name:
                    widget.visibility_panel.source_requested.emit(name)
        elif action.startswith("indicator:rm:"):
            if widget is not None:
                widget.remove_indicator(action.split(":", 2)[2])
        elif action == "reset":
            if widget is not None:
                widget.reset_view()
        elif action == "trade:prev":
            panel = getattr(window, "_trade_context_panel", None)
            if panel is not None:
                panel.prev_trade.emit()
        elif action == "trade:next":
            panel = getattr(window, "_trade_context_panel", None)
            if panel is not None:
                panel.next_trade.emit()
        elif action == "trade:open":
            panel = getattr(window, "_trade_context_panel", None)
            if panel is not None:
                panel.open_in_market.emit()
        elif action.startswith("dl:"):
            _apply_download_action(window, action)
    except Exception:  # noqa: BLE001 (a failed action must never break the pump)
        logger.debug("slint market host: action failed: %s", action, exc_info=True)


def _dl_parts(action: str) -> list[str]:
    return action.split(":", 2)[2:] if action.count(":") >= 2 else []


def _apply_download_action(window: Any, action: str) -> None:
    """Replay a download-console action on the retained panel/manager using the
    SAME methods/signals the Qt widgets use — never a reimplementation."""
    dpanel = getattr(window, "_download", None)
    if dpanel is None:
        return
    console = getattr(dpanel, "_panel", None)  # DownloadPanel
    status = getattr(dpanel, "_status", None)  # StatusView
    logpanel = getattr(dpanel, "_log_panel", None)  # LogPanel
    kind = action.split(":")[1]

    if kind == "interval" and console is not None:
        with contextlib.suppress(Exception):
            console._interval.setCurrentIndex(int(action.rsplit(":", 1)[1]))
    elif kind == "select" and console is not None:
        symbol = action.split(":", 2)[2]
        with contextlib.suppress(Exception):
            stocks = console._stocks
            stocks.set_selected(symbol, symbol not in set(stocks.selected_symbols))
    elif kind == "select-all" and console is not None:
        with contextlib.suppress(Exception):
            console._stocks.select_all()
    elif kind == "clear-all" and console is not None:
        with contextlib.suppress(Exception):
            console._stocks.clear_all()
    elif kind == "day" and console is not None:
        parts = action.split(":")  # dl:day:<field>:<y>:<m>:<d>
        if len(parts) == 6:
            field, y, m, d = parts[2], int(parts[3]), int(parts[4]), int(parts[5])
            with contextlib.suppress(Exception):
                edit = console._from_edit if field == "from" else console._to_edit
                edit.setDate(QDate(y, m, d))
    elif kind == "quick" and console is not None:
        cap = action.rsplit(":", 1)[1]
        table = {"1M": (-1, 0), "3M": (-3, 0), "6M": (-6, 0), "1Y": (0, -1), "MAX": (0, -10)}
        months, years = table.get(cap, (0, 0))
        with contextlib.suppress(Exception):
            console._apply_quick_range(months, years)
    elif kind == "download" and console is not None:
        with contextlib.suppress(Exception):
            console._emit_download()
    elif kind == "coverage" and console is not None:
        with contextlib.suppress(Exception):
            console._emit_coverage()
    elif kind == "cancel" and console is not None:
        with contextlib.suppress(Exception):
            console.cancel_requested.emit()
    elif kind == "retry" and status is not None:
        with contextlib.suppress(Exception):
            status._on_retry()
    elif kind == "view-coverage" and status is not None:
        with contextlib.suppress(Exception):
            status._on_view_coverage()
    elif kind == "error-details" and status is not None:
        with contextlib.suppress(Exception):
            status._toggle_error_details()
    elif kind == "advanced" and status is not None:
        with contextlib.suppress(Exception):
            status._toggle_advanced()
    elif kind == "log" and logpanel is not None:
        with contextlib.suppress(Exception):
            logpanel._on_toggle()
    elif kind == "log-clear" and logpanel is not None:
        with contextlib.suppress(Exception):
            logpanel.clear()
    elif kind == "broker":
        name = action.split(":", 2)[2]
        if name:
            with contextlib.suppress(Exception):
                dpanel.broker_selected.emit(name)
    else:
        _apply_credentials(window, status, action)


def _apply_credentials(window: Any, status: Any, action: str) -> None:
    """Drive the provider credentials manager (the SAME service the Qt dialog
    used) for Test / Save / Clear; then refresh the panel's provider status."""
    kind = action.split(":")[1]
    if not kind.startswith("creds"):
        return
    mgr = getattr(status, "_credentials_manager", None) if status is not None else None
    if mgr is None:
        return
    if kind == "creds-test":
        values = _cred_values(action)
        error = mgr.validate(values)
        if error is not None:
            window._dl_cred_status = f"✕ {error}"
            return
        ready, _reason = mgr.test_connection(values)
        window._dl_cred_status = (
            "✓ Connection successful"
            if ready
            else "✕ Connection failed — check your API credentials."
        )
    elif kind == "creds-save":
        values = _cred_values(action)
        try:
            mgr.save(values)
        except Exception as exc:  # noqa: BLE001 (surface like the dialog's status line)
            window._dl_cred_status = f"✕ {exc}"
            return
        window._dl_cred_close = True
        window._dl_cred_status = ""
        with contextlib.suppress(Exception):
            ready, reason = mgr.reload()
            window._download.set_provider(ready, reason)
    elif kind == "creds-clear":
        mgr.clear()
        window._dl_cred_close = True
        window._dl_cred_status = "● Not Configured — locally stored credentials removed."
        with contextlib.suppress(Exception):
            ready, reason = mgr.reload()
            window._download.set_provider(ready, reason)


def _cred_values(action: str) -> dict[str, str]:
    import json

    payload = action.split(":", 2)[2] if action.count(":") >= 2 else "{}"
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


class SlintMarketHost(QWidget):
    """Plain viewport blitting native Slint Market frames.

    Zero Market presentation lives here: pixels arrive from Rust, input
    events leave for Rust, backend facts arrive via ``state_provider`` (the
    existing ChartWindow + its retained widgets are the state holders; the
    Qt market container is intentionally NOT mounted in production). When the
    cdylib is unavailable the widget stays dark with one muted status line
    (honest bridge health) and logs the cause.
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
            logger.warning("slint market host unavailable: %s", exc)

    @property
    def is_native_available(self) -> bool:
        """Whether the native view library loaded (bridge health probe)."""
        return self._lib is not None

    def refresh_now(self) -> None:
        """Force an immediate snapshot push (called on backend events)."""
        self._push_snapshot()

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
        handle = self._lib.vayren_market_view_create(width, height, dpr)
        if not handle:
            logger.warning("slint market host: native view creation failed")
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
            self._lib.vayren_market_view_resize(
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
                self._lib.vayren_market_view_destroy(self._view)
        self._view = None

    # ── data feed + action drain ──

    def _push_snapshot(self, force: bool = False) -> None:
        if self._lib is None or self._view is None or self._provider is None:
            return
        try:
            state = self._provider()
        except Exception:  # noqa: BLE001 (provider contract: never raises; stay safe anyway)
            logger.debug("slint market host: state provider failed", exc_info=True)
            return
        try:
            payload = json.dumps(state, sort_keys=True, default=str)
        except (TypeError, ValueError):
            logger.debug("slint market host: snapshot not serializable", exc_info=True)
            return
        if not force and payload == self._last_pushed:
            return
        code = self._lib.vayren_market_view_set_snapshot(self._view, payload.encode("utf-8"))
        if code == 0:
            self._last_pushed = payload
        else:
            logger.debug("slint market host: snapshot rejected (code %s)", code)

    def _drain_actions(self) -> None:
        if self._lib is None or self._view is None or self._action_sink is None:
            return
        # The ABI takes *mut u8 (LP_c_ubyte); a c_char array does NOT
        # auto-convert (ctypes.ArgumentError), and the suppress below would
        # silently swallow it - so view the buffer as u8 or NO queued Slint
        # action would ever reach the backend.
        buffer = ctypes.create_string_buffer(ACTION_BUFFER)
        raw = (ctypes.c_uint8 * len(buffer)).from_buffer(buffer)
        for _ in range(MAX_ACTIONS_PER_TICK):
            popped = -1
            with contextlib.suppress(Exception):
                popped = self._lib.vayren_market_view_next_action(self._view, raw, len(raw))
            if popped <= 0:
                return
            self._action_sink(buffer.raw[: int(popped)].decode("utf-8", "replace"))

    def _on_pump(self) -> None:
        if self._lib is None or self._view is None:
            return
        with contextlib.suppress(Exception):
            self._lib.vayren_market_view_tick(self._view)
            self._drain_actions()
            self._provider_ticks += 1
            if self._provider_ticks >= PROVIDER_POLL_TICKS:
                self._provider_ticks = 0
                self._push_snapshot()
            width = max(1, int(round(self.width() * self._dpr())))
            height = max(1, int(round(self.height() * self._dpr())))
            self._alloc_frame(width, height)
            painted = self._lib.vayren_market_view_render(
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
                "Native Market view unavailable — build the Rust workspace.",
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
                self._lib.vayren_market_view_pointer_move(self._view, pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mousePressEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_market_view_pointer_press(self._view, pos.x(), pos.y(), button)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_market_view_pointer_release(self._view, pos.x(), pos.y(), button)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_market_view_pointer_leave(self._view)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt override)
        super().wheelEvent(event)
        if self._lib is not None and self._view is not None:
            delta = event.angleDelta()
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_market_view_scroll(
                    self._view,
                    pos.x(),
                    pos.y(),
                    delta.x() / 120.0 * WHEEL_PX_PER_NOTCH,
                    -delta.y() / 120.0 * WHEEL_PX_PER_NOTCH,
                )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyPressEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_market_view_key(self._view, event.text().encode("utf-8"), 1)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyReleaseEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_market_view_key(self._view, event.text().encode("utf-8"), 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if self._lib is not None and self._view is not None:
            dpr = self._dpr()
            width = max(1, int(round(self.width() * dpr)))
            height = max(1, int(round(self.height() * dpr)))
            self._alloc_frame(width, height)
            with contextlib.suppress(Exception):
                self._lib.vayren_market_view_resize(self._view, width, height, dpr)
