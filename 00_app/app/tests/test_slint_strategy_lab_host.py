"""Slint Strategy Lab host: parity bridge contract (provider + sink).

The provider is a read-only projection of the canonical Qt workspace and the
sink forwards queued native actions into existing backend entry points. These
tests pin the parity surface added for 100% feature parity: editor buffer +
params, editable config echoes, ranking controls, drill-down detail,
buy/sell/compare blocks, trade selection/filter echoes, run label, and chart
captions — plus the sink routing for every new action.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.services.slint_strategy_lab_host import (
    SlintStrategyLabHost,
    apply_lab_strategy_action,
    strategy_lab_snapshot_dict,
)


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _true(*_args: Any, **_kwargs: Any) -> bool:
    return True


def _text(value: str = "", visible: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        text=lambda: value,
        isVisible=lambda: visible,
        setText=_noop,
    )


def _combo(items: list[str], current: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        count=lambda: len(items),
        itemText=lambda i, _items=items: _items[i],
        currentIndex=lambda: current,
        currentText=lambda: items[current] if items else "",
        setCurrentIndex=_noop,
    )


def _fake_result() -> SimpleNamespace:
    metrics = SimpleNamespace(
        net_profit=185236.4,
        total_trades=71,
        profit_factor=1.42,
        expectancy=2609.2,
        max_drawdown_pct=8.3,
        sharpe_ratio=1.15,
        avg_trade=260.5,
        win_rate=0.5214,
    )
    trade = SimpleNamespace(
        symbol="RELIANCE",
        side="LONG",
        entry_time="2026-01-02T09:15:00",
        exit_time="2026-01-02T15:30:00",
        entry_price=2801.1,
        exit_price=2812.4,
        pnl=11230.4,
        r_multiple=0.42,
        bars_held=5,
        exit_reason="TARGET",
        winning=True,
    )
    point = SimpleNamespace(equity=1000000.0, drawdown_pct=0.0)
    point2 = SimpleNamespace(equity=1185236.4, drawdown_pct=0.0)
    return SimpleNamespace(metrics=metrics, trades=[trade], equity_curve=[point, point2])


def _fake_rank_row() -> SimpleNamespace:
    return SimpleNamespace(
        rank=1,
        symbol="RELIANCE",
        status="ranked",
        note="",
        net_profit=185236.4,
        return_pct=18.5,
        total_trades=71,
        win_rate=0.5214,
        profit_factor=1.42,
        max_drawdown_pct=8.3,
        sharpe_ratio=1.15,
    )


def _fake_workspace() -> SimpleNamespace:
    ranking = SimpleNamespace(
        rows=lambda: [_fake_rank_row()],
        selected_symbol=lambda: "RELIANCE",
        select_symbol=_true,
        symbol_focused=SimpleNamespace(emit=_noop),
        _sort_box=_combo(["Net P&L", "Return %"], 0),
        _search=SimpleNamespace(text=lambda: ""),
        _sort_desc=True,
        _sort_col=2,
        _on_header_clicked=_noop,
        detail=SimpleNamespace(
            title=_text("RELIANCE  ·  Rank #1"),
            trade_stats=_text("40 winning · 31 losing", True),
            caption=_text("RELIANCE — EQUITY CURVE"),
            spark=SimpleNamespace(_values=[1.0, 2.0, 3.0]),
            _blocks={
                label: SimpleNamespace(_value=_text(f"{label} v"))
                for label in (
                    "NET P&L",
                    "RETURN",
                    "WIN RATE",
                    "PROFIT FACTOR",
                    "MAX DD",
                    "SHARPE",
                )
            },
        ),
    )
    right_settings = SimpleNamespace(
        current_config=lambda: {
            "symbols": ("RELIANCE", "TCS"),
            "symbol": "RELIANCE",
            "timeframe": "15m",
            "start_date": "2023-01-01",
            "end_date": "2026-09-12",
            "initial_capital": 1000000.0,
            "slippage_pct": 0.02,
            "commission_pct": 0.03,
        },
        ranking=ranking,
        selected_symbols=lambda: ("RELIANCE",),
        _symbols=SimpleNamespace(
            available_symbols=("RELIANCE", "TCS", "INFY"),
            selected_symbols=lambda: ("RELIANCE",),
        ),
        _tf_combo=_combo(["5m", "15m", "1h"], 1),
        _error=_text("", False),
        select_timeframe=_noop,
        set_selected_symbols=_noop,
        set_capital=_true,
        set_dates=_true,
    )
    label = _text("BUY (LONG)")
    buy_labels = dict.fromkeys(("Net Profit", "Total Trades"), label)
    sell_labels = {key: _text("--") for key in ("Net Profit", "Total Trades")}
    compare_view = SimpleNamespace(
        _banner=SimpleNamespace(_verdict=_text("BUY / LONG"), _reason=_text("why buy")),
        _trade_summary=_text("BUY: 40 trades"),
        _stale_notice=_text("", False),
        _matrix=SimpleNamespace(_buy_labels=buy_labels, _sell_labels=sell_labels),
        _comparison=SimpleNamespace(
            _scope=_text("2 STOCKS COMPARED"),
            _leader=_text("◆ LEADER  RELIANCE"),
        ),
    )
    journal = SimpleNamespace(
        selected_index=lambda: 3,
        set_selected_index=_noop,
        set_symbol_filter=_noop,
        symbol_filter="ALL",
        _needle="",
        _filter=SimpleNamespace(setText=_noop),
        _step_selection=_true,
        _export_csv=_noop,
        trade_clicked=SimpleNamespace(emit=_noop),
    )
    center_detail = SimpleNamespace(
        get_code=lambda: (
            "class _Spec:\n"
            "    def __init__(self, key, label, default):\n"
            "        self.key, self.label, self.default = key, label, default\n"
            "class Strategy:\n"
            "    @staticmethod\n"
            "    def param_specs():\n"
            '        return (_Spec("period", "Period", 10.0),)\n'
            "    def on_bar_logic(self, view):\n"
            "        pass\n"
        ),
        current_params=lambda: {"period": 10.0},
        set_params=_noop,
        param_changed=SimpleNamespace(emit=_noop),
        editor=SimpleNamespace(
            blockSignals=_noop,
            setPlainText=_noop,
        ),
        _on_text_changed=_noop,
    )
    return SimpleNamespace(
        left_nav=SimpleNamespace(
            names=lambda: ["OBR"],
            current_name=lambda: "OBR",
            _items=[("OBR", 0.0)],
            _favorites=set(),
        ),
        current_tab_name=lambda: "OBR",
        view_mode="buy",
        _run_state="complete",
        _stale_sides=(),
        right_settings=right_settings,
        center_detail=center_detail,
        journal=journal,
        trade_focus=SimpleNamespace(emit=_noop),
        _topbar_run=_text("▶  RUN BUY"),
        _topbar_status=_text(""),
        _full_result=_fake_result(),
        buy_result=_fake_result(),
        sell_result=None,
        _compare_view=compare_view,
        _equity_summary=_text("START ₹10,00,000.00"),
        _emit_run_all=_noop,
        _emit_run_buy=_noop,
        _emit_run_sell=_noop,
        strategy_open_requested=SimpleNamespace(emit=_noop),
        save_requested_relay=SimpleNamespace(emit=_noop),
        compile_requested_relay=SimpleNamespace(emit=_noop),
        set_view_mode=_noop,
        show_result=_noop,
    )


def test_provider_parity_keys_present() -> None:
    snap = strategy_lab_snapshot_dict(_fake_workspace())
    assert snap["code"].startswith("class _Spec:")
    assert snap["cfg_edit"]["universe_csv"] == "RELIANCE,TCS"
    assert snap["cfg_edit"]["timeframes"] == ["5m", "15m", "1h"]
    assert snap["cfg_edit"]["dates_start"] == "2023-01-01"
    assert snap["cfg_edit"]["capital"] == 1000000.0
    assert snap["cfg_edit"]["config_error"] == ""
    assert snap["rankby"]["labels"] == ["Net P&L", "Return %"]
    assert snap["rankby"]["current"] == 0
    assert snap["rankby"]["desc"] is True
    assert snap["selected_trade"] == 3
    assert snap["trade_filter"] == {"needle": "", "symbol": "ALL", "active": False}
    assert snap["run_label"] == "▶  RUN BUY"
    assert snap["equity_summary"] == "START ₹10,00,000.00"
    # Universe reuses the Market Watchlist store (single source, no copy).
    assert snap["universe"] == {
        "symbols": ["RELIANCE", "TCS", "INFY"],
        "selected": ["RELIANCE"],
    }
    # JSON-serializable (the host dumps with sort_keys).
    json.dumps(snap, sort_keys=True, default=str)


def test_provider_trades_carry_blotter_columns() -> None:
    snap = strategy_lab_snapshot_dict(_fake_workspace())
    trade = snap["results"]["trades"][0]
    assert trade["entry_px"] == 2801.1
    assert trade["exit_px"] == 2812.4
    assert trade["bars"] == 5
    assert trade["reason"] == "TARGET"
    assert trade["winning"] is True


def test_provider_detail_and_compare_echo_backend_facts() -> None:
    snap = strategy_lab_snapshot_dict(_fake_workspace())
    detail = snap["detail"]
    assert detail is not None
    assert detail["symbol"] == "RELIANCE"
    assert detail["stats"] == "40 winning · 31 losing"
    assert [m["label"] for m in detail["metrics"]] == [
        "NET P&L",
        "RETURN",
        "WIN RATE",
        "PROFIT FACTOR",
        "MAX DD",
        "SHARPE",
    ]
    assert detail["equity"] == [1.0, 2.0, 3.0]
    compare = snap["compare"]
    assert compare is not None
    assert compare["banner_verdict"] == "BUY / LONG"
    assert compare["banner_reason"] == "why buy"
    assert compare["trade_summary"] == "BUY: 40 trades"
    assert compare["board_scope"] == "2 STOCKS COMPARED"
    assert compare["board_rows"][0]["label"] == "NET P&L"
    assert compare["ranking"][0]["symbol"] == "RELIANCE"
    assert snap["buy"] is not None
    assert snap["sell"] is None


def test_provider_params_reuse_compiler_specs() -> None:
    snap = strategy_lab_snapshot_dict(_fake_workspace())
    assert {"key": "period", "label": "Period", "value": 10.0} in snap["params"]


def test_sink_routes_parity_actions() -> None:
    calls: list[tuple[str, Any]] = []

    def recorder(name: str) -> Any:
        def call(*args: Any) -> Any:
            calls.append((name, args))
            return True

        return call

    workspace = _fake_workspace()
    workspace.right_settings.select_timeframe = recorder("timeframe")
    workspace.right_settings.set_selected_symbols = recorder("symbols")
    workspace.right_settings.set_capital = recorder("capital")
    workspace.right_settings.set_dates = recorder("dates")
    workspace.right_settings.ranking._search = SimpleNamespace(setText=recorder("search"))
    workspace.right_settings.ranking._sort_box = SimpleNamespace(setCurrentIndex=recorder("rankby"))
    workspace.right_settings.ranking._on_header_clicked = recorder("ranktoggle")
    workspace.journal.set_selected_index = recorder("tradesel")
    workspace.journal._step_selection = recorder("step")
    workspace.journal._filter = SimpleNamespace(setText=recorder("tradefilter"))
    workspace.trade_focus.emit = recorder("tradefocus")
    workspace.center_detail.set_params = recorder("set_params")
    workspace.center_detail.param_changed = SimpleNamespace(emit=recorder("param_changed"))
    workspace._emit_run_all = recorder("runall")
    workspace._emit_run_buy = recorder("runbuy")
    workspace._emit_run_sell = recorder("runsell")

    apply_lab_strategy_action(workspace, "settimeframe:1h")
    apply_lab_strategy_action(workspace, "symbols:RELIANCE,TCS")
    apply_lab_strategy_action(workspace, "capital:500000")
    apply_lab_strategy_action(workspace, "dates:2023-01-01:2026-01-01")
    apply_lab_strategy_action(workspace, "ranksearch:REL")
    apply_lab_strategy_action(workspace, "rankby:2")
    apply_lab_strategy_action(workspace, "ranktoggle")
    apply_lab_strategy_action(workspace, "tradesel:3")
    apply_lab_strategy_action(workspace, "tradefocus:3")
    apply_lab_strategy_action(workspace, "tradeup")
    apply_lab_strategy_action(workspace, "tradedown")
    apply_lab_strategy_action(workspace, "tradeenter")
    apply_lab_strategy_action(workspace, "tradefilter:REL")
    apply_lab_strategy_action(workspace, "paramset:period:20")
    apply_lab_strategy_action(workspace, "runall")
    apply_lab_strategy_action(workspace, "runbuy")
    apply_lab_strategy_action(workspace, "runsell")

    routed = dict(calls)
    assert routed["timeframe"] == ("1h",)
    assert routed["symbols"] == (["RELIANCE", "TCS"],)
    assert routed["capital"] == ("500000",)
    assert routed["dates"] == ("2023-01-01", "2026-01-01")
    assert routed["search"] == ("REL",)
    assert routed["rankby"] == (2,)
    assert routed["tradesel"] == (3,)
    assert routed["tradefocus"] == (3,)
    assert routed["step"] == (1,)  # tradedown last stepped +1 after tradeup
    assert routed["tradefilter"] == ("REL",)
    assert routed["set_params"] == ({"period": 20.0},)
    assert routed["param_changed"] == ("period", 20.0)
    assert "runall" in routed and "runbuy" in routed and "runsell" in routed


def test_sink_save_and_compile_carry_buffer() -> None:
    seen: dict[str, Any] = {}
    workspace = _fake_workspace()
    workspace.center_detail.editor = SimpleNamespace(
        blockSignals=lambda b: seen.setdefault("blocked", []).append(b) or None,
        setPlainText=lambda t: seen.setdefault("buffers", []).append(t),
    )
    workspace.center_detail._on_text_changed = lambda: seen.setdefault("touched", True)
    workspace.save_requested_relay = SimpleNamespace(emit=lambda c: seen.setdefault("saved", c))
    workspace.compile_requested_relay = SimpleNamespace(
        emit=lambda c: seen.setdefault("compiled", c)
    )
    apply_lab_strategy_action(workspace, "save:code v2")
    apply_lab_strategy_action(workspace, "compile:code v3")
    assert seen["buffers"] == ["code v2", "code v3"]
    assert seen["touched"] is True
    assert seen["saved"] == "code v2"
    assert seen["compiled"] == "code v3"


def test_sink_tab_mapping_skips_editor() -> None:
    seen: list[Any] = []
    workspace = _fake_workspace()
    workspace.show_result = lambda i, **_kwargs: seen.append(i)
    apply_lab_strategy_action(workspace, "tab:0")
    assert seen == []
    apply_lab_strategy_action(workspace, "tab:2")
    assert seen == [1]


def test_drain_actions_honours_abi_buffer_contract(qt_app: Any) -> None:
    """Pin the blank-editor root cause: the drain buffer must satisfy the
    u8_p ABI slot. A c_char buffer raises TypeError inside ctypes, the old
    suppress swallowed it, and no native action ever reached the backend
    (optimistic header + blank editor)."""
    import ctypes

    assert qt_app is not None
    seen: list[str] = []
    calls = {"n": 0}
    action = b"select:0"
    proto = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t
    )

    @proto
    def next_action(_view: Any, out: Any, out_len: int) -> int:
        calls["n"] += 1
        if calls["n"] > 1:
            return 0
        if len(action) > out_len:
            return -5
        ctypes.memmove(out, action, len(action))
        return len(action)

    host = SlintStrategyLabHost()
    host._lib = SimpleNamespace(vayren_strategy_lab_view_next_action=next_action)  # type: ignore[attr-defined]
    host._view = 123  # type: ignore[attr-defined]
    host._action_sink = seen.append  # type: ignore[attr-defined]
    host._drain_actions()
    assert seen == ["select:0"]


def test_key_payload_grammar(qt_app: Any) -> None:
    assert qt_app is not None
    from PySide6.QtCore import Qt

    seen: list[bytes] = []

    class FakeLib:
        def vayren_strategy_lab_view_key(self, _view: Any, payload: bytes, _p: int) -> int:
            seen.append(bytes(payload))
            return 0

    host = SlintStrategyLabHost()
    host._lib = FakeLib()  # type: ignore[attr-defined]
    host._view = object()  # type: ignore[attr-defined]

    from PySide6.QtGui import QKeyEvent

    up = QKeyEvent(QKeyEvent.Type.KeyPress, 16777235, Qt.KeyboardModifier.NoModifier, "")
    host.keyPressEvent(up)
    ctrl_s = QKeyEvent(QKeyEvent.Type.KeyPress, 83, Qt.KeyboardModifier.ControlModifier, "")
    host.keyPressEvent(ctrl_s)
    assert seen[0] == b"KEY+16777235"
    assert seen[1] == b"CTRL+83"


def test_backtest_panel_capital_and_dates_setters(qt_app: Any) -> None:
    assert qt_app is not None
    from app.ui.strategy_lab_workspace import BacktestRunPanel

    panel = BacktestRunPanel()
    assert panel.set_capital(500000) is True
    assert panel.current_config()["initial_capital"] == 500000.0
    assert panel.set_capital("nope") is False
    assert panel.set_capital(5) is False  # below widget range
    assert panel.set_dates("2023-01-01", "2026-01-01") is True
    assert panel.current_config()["start_date"] == "2023-01-01"
    assert panel.current_config()["end_date"] == "2026-01-01"
    assert panel.set_dates("junk", "2026-01-01") is False
