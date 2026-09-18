"""Slint Market host: bridge contract + viewport behavior.

The host draws nothing itself (pixels come from Rust) and computes nothing
(position math lives in Rust `project()`); these tests pin the parity bridge:
real backend facts (watchlist, quotes, bars, indicators, strategy plot series,
overlay trades) project through unchanged, missing data degrades to honest
absence, actions re-enter the backend through the SAME signals/methods a user
click uses, and a real native frame paints varied pixels offscreen.
"""

import json
from types import SimpleNamespace
from typing import Any

import market.models.bar as bar_module
import pytest
from chart.models.chart_model import ChartModel
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.watchlist_widget import WatchlistWidget
from market.models.symbol_quote import SymbolQuote

from app.services.slint_market_host import (
    NativeViewError,
    SlintMarketHost,
    apply_market_action,
    find_view_library,
    load_view_library,
    market_snapshot_dict,
)


def _bar(symbol: str, ts: str, close: float) -> bar_module.Bar:
    return bar_module.Bar(
        symbol=symbol,
        open=close - 1.0,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        volume=1000,
        timestamp=ts,
    )


def _funded_window() -> SimpleNamespace:
    """Real Qt widgets wired like ChartWindow (state-holder path only)."""
    watchlist = WatchlistWidget()
    watchlist.set_symbols(("RELIANCE", "TCS"))
    watchlist.set_quotes(
        (
            SymbolQuote(symbol="RELIANCE", price=2451.10, change_pct=1.24, timestamp="t"),
            SymbolQuote(symbol="TCS", price=3802.55, change_pct=-0.42, timestamp="t"),
        )
    )
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "1h"))
    chart = CandleChartWidget()
    chart.set_model(
        ChartModel(
            symbol="RELIANCE",
            bars=(
                _bar("RELIANCE", "2024-01-02T09:15:00", 2450.0),
                _bar("RELIANCE", "2024-01-02T09:30:00", 2451.10),
            ),
            timeframe="15m",
            exchange="NSE",
        )
    )
    chart.add_indicator("SMA")

    # A strategy plot series + one overlay trade (real structured facts).
    def _query(*_args: object, **_kwargs: object) -> list:
        return [
            SimpleNamespace(
                bar_index=1,
                price=2451.0,
                marker_type="UP_ARROW",
                plot_type="MARKER",
                text="BUY 2451.00",
                source_strategy="OBR",
            )
        ]

    def _visible(*_args: object, **_kwargs: object) -> bool:
        return True

    plot_overlay: Any = SimpleNamespace(
        _series={("SMA", "SMA20"): {0: 2450.0, 1: 2451.0}},
        _meta={("SMA", "SMA20"): {"extend": "none"}},
        _store=SimpleNamespace(query_visible=_query),
        _is_visible=_visible,
    )
    trade_overlay: Any = SimpleNamespace(
        _trades=[
            SimpleNamespace(
                side="LONG",
                entry_time="2024-01-02T09:15:00",
                exit_time="2024-01-02T09:30:00",
                entry_price=2450.0,
                exit_price=2451.0,
                winning=True,
                entry_index=0,
                exit_index=1,
                exit_reason="SIGNAL",
            )
        ],
        covered_bars=frozenset({0}),
        focused_trade=None,
        focused_index=None,
    )
    chart.set_named_overlay("SMA", plot_overlay)
    chart.set_overlay(trade_overlay)
    return SimpleNamespace(
        watchlist=watchlist,
        toolbar=toolbar,
        current_symbol="RELIANCE",
        current_timeframe="15m",
        indicators=SimpleNamespace(panel=SimpleNamespace(_strategy_names=("OBR",))),
        _widget=chart,
    )


def test_snapshot_projects_real_backend_facts(qt_app) -> None:
    assert qt_app is not None
    snapshot = market_snapshot_dict(_funded_window())
    assert snapshot["status"] == "ready"
    assert [s["symbol"] for s in snapshot["symbols"]] == ["RELIANCE", "TCS"]
    reliance = next(s for s in snapshot["symbols"] if s["symbol"] == "RELIANCE")
    assert reliance["price"] == 2451.10
    assert reliance["change_pct"] == 1.24
    assert snapshot["selected_symbol"] == "RELIANCE"
    assert snapshot["timeframes"] == ["15m", "1h"]
    assert snapshot["timeframe"] == "15m"
    assert snapshot["exchange"] == "NSE"
    assert snapshot["strategies"] == ["OBR"]
    assert snapshot["bars"][0]["close"] == 2450.0
    assert snapshot["bars"][0]["time"] == "2024-01-02T09:15:00"
    assert snapshot["indicators"]["SMA"] is True
    # Real plot series (owner-aware) + overlay trade markers reach the bridge.
    assert snapshot["plot_series"][0]["owner"] == "SMA"
    assert snapshot["plot_series"][0]["points"][0] == [0, 2450.0]
    assert snapshot["plot_series"][0]["extend"] == "none"
    assert snapshot["trades"][0]["side"] == "LONG"
    assert snapshot["watchlists"] and snapshot["active_watchlist"] in snapshot["watchlists"]
    json.dumps(snapshot, sort_keys=True, default=str)


def test_snapshot_trades_resolve_through_chart_timestamps(qt_app) -> None:
    """Replay indices never index chart bars: positions resolve through
    chart timestamps (exact, then [:16] fallback); covered flags evaluate
    backend-side in replay basis (exact Qt rule)."""
    assert qt_app is not None
    window = _funded_window()
    chart = window._widget
    chart.set_model(
        ChartModel(
            symbol="RELIANCE",
            bars=(
                _bar("RELIANCE", "2024-01-02T09:15:00", 2450.0),
                _bar("RELIANCE", "2024-01-02T09:30:00", 2451.10),
                _bar("RELIANCE", "2024-01-02T09:45:00", 2452.0),
            ),
            timeframe="15m",
            exchange="NSE",
        )
    )
    overlay = chart._overlay
    overlay._trades = [
        SimpleNamespace(
            side="SHORT",
            entry_time="2024-01-02T09:30:00",
            exit_time="2024-01-02T09:45:00",
            entry_price=2451.0,
            exit_price=2450.0,
            winning=True,
            entry_index=7,  # replay basis: meaningless for chart bars
            exit_index=9,
            exit_reason="SIGNAL",
        ),
        SimpleNamespace(
            side="LONG",
            entry_time="2024-01-02T10:00:00",  # absent from chart bars
            exit_time="2024-01-02T10:15:00",
            entry_price=2453.0,
            exit_price=2454.0,
            winning=True,
            entry_index=11,
            exit_index=12,
            exit_reason="SIGNAL",
        ),
    ]
    overlay._covered_bars = frozenset({7})  # replay basis: covers the SHORT entry
    overlay.covered_bars = frozenset({7})
    snapshot = market_snapshot_dict(window)
    have, missing = snapshot["trades"]
    assert (have["entry_pos"], have["exit_pos"]) == (1, 2)
    assert have["entry_covered"] is True
    assert have["exit_covered"] is False
    assert (missing["entry_pos"], missing["exit_pos"]) == (None, None)
    json.dumps(snapshot, sort_keys=True, default=str)


def test_snapshot_trades_carry_exact_overlay_semantics(qt_app) -> None:
    assert qt_app is not None
    snapshot = market_snapshot_dict(_funded_window())
    trade = snapshot["trades"][0]
    # Chart positions resolve through chart timestamps (basis-proof);
    # covered flags evaluate backend-side in replay basis (exact Qt rule).
    assert trade["entry_pos"] == 0
    assert trade["exit_pos"] == 1
    assert trade["entry_covered"] is True
    assert trade["exit_covered"] is False
    assert trade["winning"] is True
    assert trade["exit_reason"] == "SIGNAL"
    # Covered flags + focused detail + strategy markers ride along.
    assert snapshot["focused"] is None
    marker = snapshot["strategy_markers"][0]
    assert marker == {
        "bar": 1,
        "price": 2451.0,
        "kind": "UP_ARROW",
        "text": "BUY 2451.00",
        "layer": 60,
        "order": 0,
    }
    json.dumps(snapshot, sort_keys=True, default=str)


def test_snapshot_caps_bars_to_the_viewable_window(qt_app) -> None:
    assert qt_app is not None
    window = _funded_window()
    bars = tuple(
        _bar("TEST", f"2024-01-02T09:{i // 60:02d}:{i % 60:02d}:00", 100.0 + i) for i in range(1805)
    )
    window._widget._model = ChartModel(symbol="TEST", bars=bars, timeframe="15m", exchange="NSE")
    snapshot = market_snapshot_dict(window)
    assert len(snapshot["bars"]) == 1800
    # The TAIL is delivered (the viewport shows the latest window).
    assert snapshot["bars"][-1]["close"] == 100.0 + 1804
    # Plot series re-based onto the delivered tail (offset dropped early idx).
    assert all(pt[0] >= 0 for row in snapshot["plot_series"] for pt in row["points"])


def test_snapshot_without_data_stays_honest(qt_app) -> None:
    assert qt_app is not None
    assert market_snapshot_dict(None)["status"] == "loading"
    empty = SimpleNamespace(
        watchlist=None,
        toolbar=None,
        current_symbol=None,
        current_timeframe=None,
        indicators=None,
        _widget=None,
    )
    snapshot = market_snapshot_dict(empty)
    assert snapshot["status"] == "loading"
    assert snapshot["bars"] is None
    assert snapshot["symbols"] == []
    no_model = _funded_window()
    no_model._widget._model = None
    no_model.current_symbol = "GHOST"
    degraded = market_snapshot_dict(no_model)
    assert degraded["status"] == "empty"
    assert degraded["bars"] is None
    json.dumps(degraded, sort_keys=True, default=str)


def test_actions_re_enter_backend_through_user_click_signals(qt_app) -> None:
    assert qt_app is not None
    window = _funded_window()
    clicked: list[str] = []
    picked: list[str] = []
    window.watchlist.symbol_selected.connect(clicked.append)
    window.toolbar.timeframe_selected.connect(picked.append)
    apply_market_action(window, "select:TCS")
    apply_market_action(window, "timeframe:1h")
    assert clicked == ["TCS"]
    assert picked == ["1h"]
    chart = window._widget
    assert chart.indicator_visibility["SMA"] is True
    apply_market_action(window, "indicator:vis:SMA")
    assert chart.indicator_visibility["SMA"] is False
    apply_market_action(window, "indicator:add:RSI")
    assert chart.indicator_visibility["RSI"] is True
    apply_market_action(window, "indicator:rm:RSI")
    assert "RSI" not in chart.indicator_visibility
    # watchlist add / switch / remove (built-in All Stocks is protected).
    before = window.watchlist.watchlists
    apply_market_action(window, "watchlist:add")
    assert len(window.watchlist.watchlists) == len(before) + 1
    apply_market_action(window, "watchlist:remove")
    apply_market_action(window, "reset")
    apply_market_action(window, "trade:prev")
    apply_market_action(window, "trade:next")
    apply_market_action(window, "trade:open")
    apply_market_action(window, "bogus:thing")
    apply_market_action(None, "select:TCS")


def test_indicator_settings_params_wire_stores_and_reruns(qt_app) -> None:
    """The native settings popup SAVE (``indicator:params:NAME:{json}``)
    stores real parameters on the chart and never mutates visibility itself.
    Malformed payloads are ignored (fail-closed); RESET clears overrides."""
    import json

    assert qt_app is not None
    window = _funded_window()
    chart = window._widget
    apply_market_action(window, "indicator:add:SMA")
    seen: list[tuple[str, dict]] = []
    chart.indicator_settings_changed.connect(lambda n, p: seen.append((n, dict(p))))
    apply_market_action(window, "indicator:params:SMA:" + json.dumps({"period": 21}))
    assert chart.indicator_settings_for("SMA") == {"period": 21.0}
    assert seen and seen[-1][0] == "SMA"
    # Settings never mutate visibility (the eye/delete own that).
    assert chart.indicator_visibility["SMA"] is True
    # Malformed / empty payloads are ignored.
    apply_market_action(window, "indicator:params:SMA:not-json")
    apply_market_action(window, "indicator:params:")
    assert chart.indicator_settings_for("SMA") == {"period": 21.0}
    # RESET clears overrides back to strategy defaults.
    apply_market_action(window, "indicator:clear-params:SMA")
    assert chart.indicator_settings_for("SMA") == {}
    # Non-numeric values are dropped, numeric ones kept.
    apply_market_action(window, "indicator:params:SMA:" + json.dumps({"period": "x"}))
    assert chart.indicator_settings_for("SMA") == {}


class _Combo:
    """Minimal QComboBox stand-in."""

    def __init__(self, items: list[tuple[str, str]]) -> None:
        self._items = items  # (display, data)
        self._idx = 0
        self.changed = 0

    def count(self) -> int:
        return len(self._items)

    def itemText(self, i: int) -> str:  # noqa: N802 — Qt QComboBox API mirror
        return self._items[i][0]

    def currentIndex(self) -> int:  # noqa: N802 — Qt QComboBox API mirror
        return self._idx

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802 — Qt QComboBox API mirror
        self._idx = i
        self.changed += 1


class _Date:
    def __init__(self, y: int, m: int, d: int) -> None:
        self._v = (y, m, d)

    def year(self) -> int:
        return self._v[0]

    def month(self) -> int:
        return self._v[1]

    def day(self) -> int:
        return self._v[2]

    def toString(self, fmt: str) -> str:  # noqa: ARG002, N802 — Qt QDate API mirror
        y, m, d = self._v
        return f"{d:02d} Sep {y}" if fmt.startswith("dd") else f"{y:04d}-09-{d:02d}"


class _DateEdit:
    def __init__(self, y: int, m: int, d: int) -> None:
        self._d = _Date(y, m, d)

    def date(self) -> _Date:
        return self._d

    def setDate(self, q: Any) -> None:  # noqa: N802 — Qt QDateEdit API mirror
        self._d = _Date(q.year(), q.month(), q.day())


class _Label:
    def __init__(self, text: str = "") -> None:
        self._t = text

    def text(self) -> str:
        return self._t


class _Stocks:
    def __init__(self, symbols: tuple[str, ...]) -> None:
        self._symbols = symbols
        self._selected: set[str] = set()

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def selected_symbols(self) -> tuple[str, ...]:
        return tuple(s for s in self._symbols if s in self._selected)

    def set_selected(self, symbol: str, selected: bool) -> None:
        if selected:
            self._selected.add(symbol)
        else:
            self._selected.discard(symbol)

    def select_all(self) -> None:
        self._selected = set(self._symbols)

    def clear_all(self) -> None:
        self._selected = set()


class _Console:
    def __init__(self) -> None:
        self._busy = False
        self._interval = _Combo([("1m", "1m"), ("5m", "5m"), ("15m", "15m")])
        self._stocks = _Stocks(("A", "B", "C"))
        self._from_edit = _DateEdit(2017, 1, 1)
        self._to_edit = _DateEdit(2026, 9, 15)
        self._plan_stocks = _Label("0")
        self.quick_calls: list[tuple[int, int]] = []

    def _apply_quick_range(self, months: int, years: int) -> None:
        self.quick_calls.append((months, years))

    def _emit_download(self) -> None:
        self.download_emitted = getattr(self, "download_emitted", 0) + 1


class _Mgr:
    def __init__(self) -> None:
        self.saved = None

    def load_values(self) -> dict[str, str]:
        return {"api_key": "K"}

    @property
    def fields(self) -> list[Any]:
        return [SimpleNamespace(key="api_key", label="API key", secret=True)]

    def has_stored(self) -> bool:
        return True

    def validate(self, values: dict[str, str]) -> str | None:  # noqa: ARG002 — provider contract mirror
        return None

    def test_connection(self, values: dict[str, str]) -> tuple[bool, str]:  # noqa: ARG002 — provider contract mirror
        return True, "ok"

    def save(self, values: dict[str, str]) -> tuple[bool, str]:
        self.saved = values
        return True, "ok"

    def reload(self) -> tuple[bool, str]:
        return True, "ready"


class _Status:
    def __init__(self) -> None:
        self._mode = "running"
        self._run_status = _Label("● Downloading")
        self._credentials_manager = _Mgr()


class _DownloadWindow(SimpleNamespace):
    pass


def _download_window() -> _DownloadWindow:
    console = _Console()
    dpanel = SimpleNamespace(
        _panel=console,
        _status=_Status(),
        _log_panel=SimpleNamespace(
            log=SimpleNamespace(toPlainText=lambda: "line1\nline2"),
            expanded=False,
            clear=lambda: None,
        ),
        broker_selected=SimpleNamespace(emit=lambda name: None),  # noqa: ARG005 — signal mirror
        set_provider=lambda ready, reason: None,  # noqa: ARG005 — signal mirror
    )
    base = _funded_window()
    return _DownloadWindow(**{**vars(base), "_download": dpanel})


def test_snapshot_projects_download_console(qt_app) -> None:
    assert qt_app is not None
    snapshot = market_snapshot_dict(_download_window())
    dl = snapshot["download"]
    assert dl["interval_items"] == ["1m", "5m", "15m"]
    assert dl["universe"] == ["A", "B", "C"]
    assert dl["from_display"].endswith("2017")
    assert dl["status"]["mode"] == "running"
    assert dl["status"]["run_status"] == "● Downloading"
    assert dl["log"] == ["line1", "line2"]
    assert dl["credentials"]["fields"][0]["key"] == "api_key"
    assert dl["credentials"]["has_stored"] is True
    json.dumps(snapshot, sort_keys=True, default=str)


def test_download_actions_drive_retained_panel(qt_app) -> None:
    assert qt_app is not None
    window = _download_window()
    console = window._download._panel
    apply_market_action(window, "dl:interval:1")
    assert console._interval.currentIndex() == 1
    apply_market_action(window, "dl:select-all")
    assert set(console._stocks.selected_symbols) == {"A", "B", "C"}
    apply_market_action(window, "dl:select:A")
    assert "A" not in console._stocks.selected_symbols  # toggle off
    apply_market_action(window, "dl:quick:6M")
    assert console.quick_calls == [(-6, 0)]
    apply_market_action(window, "dl:download")
    assert getattr(console, "download_emitted", 0) == 1
    # Credentials save reaches the manager and requests modal close once.
    apply_market_action(window, 'dl:creds-save:{"api_key":"NEW"}')
    assert window._download._status._credentials_manager.saved == {"api_key": "NEW"}
    assert getattr(window, "_dl_cred_close", False) is True
    apply_market_action(window, "dl:bogus")  # unknown is a safe no-op
    apply_market_action(None, "dl:download")


def test_missing_library_is_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_MARKET_VIEW_LIB", str(tmp_path / "absent.dll"))
    with pytest.raises(NativeViewError):
        find_view_library()
    with pytest.raises(NativeViewError):
        load_view_library()


def _needs_cdylib() -> pytest.MarkDecorator:
    try:
        find_view_library()
    except NativeViewError:
        return pytest.mark.skip(reason="native Market view cdylib not built")
    return pytest.mark.skipif(False, reason="")


@_needs_cdylib()
def test_host_renders_real_native_frame(qt_app) -> None:
    """End-to-end offscreen: real widget facts → Rust → varied real pixels."""
    assert qt_app is not None
    window = _funded_window()
    host = SlintMarketHost(
        state_provider=lambda: market_snapshot_dict(window),
        action_sink=lambda action: apply_market_action(window, action),
    )
    try:
        assert host.is_native_available
        host.resize(1280, 720)
        host.show()
        assert host._ensure_view()
        assert host._view is not None
        host._push_snapshot(force=True)
        host._on_pump()
        frame = bytes(host._frame)
        assert len(frame) == 1280 * 720 * 3
        assert any(b != frame[0] for b in frame), "native frame must contain varied pixels"
        assert host._lib is not None
        host._lib.vayren_market_view_pointer_move(host._view, 700.0, 400.0)
        host._on_pump()
        host._on_pump()
        assert len(bytes(host._frame)) == 1280 * 720 * 3
    finally:
        host.destroy_view()
        host.destroy_view()  # idempotent
        host.close()


@_needs_cdylib()
def test_host_without_window_stays_honest(qt_app) -> None:
    assert qt_app is not None
    host = SlintMarketHost(state_provider=lambda: market_snapshot_dict(None))
    try:
        host.resize(640, 480)
        host.show()
        assert host._ensure_view()
        host._on_pump()
        assert len(bytes(host._frame)) == 640 * 480 * 3
    finally:
        host.destroy_view()
        host.close()


@_needs_cdylib()
def test_drain_delivers_queued_select_to_sink(qt_app) -> None:
    """Regression pin: queued Slint actions must reach the action sink.

    `_drain_actions` once passed a c_char buffer to an LP_c_ubyte ABI slot
    (ctypes.ArgumentError on every pump, silently suppressed) so taps queued
    `select:X` but the backend never reloaded � header changed, candles did
    not. This test drives a real row tap through the native view and asserts
    the sink receives the wire string.
    """
    assert qt_app is not None
    window = _funded_window()
    host = SlintMarketHost(
        state_provider=lambda: market_snapshot_dict(window),
        action_sink=lambda action: apply_market_action(window, action),
    )
    try:
        host.resize(1280, 720)
        host.show()
        assert host._ensure_view()
        host._push_snapshot(force=True)
        lib, view = host._lib, host._view
        assert lib is not None and view is not None
        clicked: list[str] = []
        window.watchlist.symbol_selected.connect(clicked.append)
        y = 100.0
        while y < 700.0 and "TCS" not in clicked:
            assert lib.vayren_market_view_pointer_move(view, 110.0, y) == 0
            assert lib.vayren_market_view_pointer_press(view, 110.0, y, 0) == 0
            assert lib.vayren_market_view_pointer_release(view, 110.0, y, 0) == 0
            host._on_pump()
            y += 20.0
        assert "TCS" in clicked, "row tap never reached the backend signal"
    finally:
        host.destroy_view()
        host.close()


def test_snapshot_projects_market_status_panel_facts(qt_app) -> None:
    """The Market-status strip projects the retained Qt panel's real labels.

    Qt contract (market_status_panel.py): regime + data-status rows default to
    "--" (honest unknown), muted until set; set_* updates pass through
    unchanged. The bridge reads the same labels — Slint renders identical text.
    """
    assert qt_app is not None
    from app.ui.market_status_panel import MarketStatusPanel

    panel = MarketStatusPanel()
    window = _funded_window()
    window._left_extra = panel

    snapshot = market_snapshot_dict(window)
    status = snapshot["market_status"]
    # Defaults: Qt renders "--" everywhere; bridge passes it through raw.
    assert status["regime_current"] == "--"
    assert status["provider"] == "--"
    assert status["bars_loaded"] == "--"
    assert status["latency"] == "--"

    # Set values → snapshot mirrors exactly (same Qt setter path).
    panel.set_regime({"Current regime": "TRENDING", "Momentum": "RISING"})
    panel.set_data_status(provider="zerodha", latency="4ms", bars_loaded=61676)
    status = market_snapshot_dict(window)["market_status"]
    assert status["regime_current"] == "TRENDING"
    assert status["regime_momentum"] == "RISING"
    assert status["regime_trend"] == "--"
    assert status["provider"] == "zerodha"
    assert status["latency"] == "4ms"
    assert status["bars_loaded"] == "61676"

    # No panel on the window → honest absence (no invented keys/values).
    plain = market_snapshot_dict(_funded_window())
    assert "market_status" not in plain
    json.dumps(snapshot, sort_keys=True, default=str)
