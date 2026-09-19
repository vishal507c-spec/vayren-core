"""TradeChartController — single-click Trade → Exact Chart Context.

Responsibilities (spec §18-24, §34):
- CLICK → SNAP: instant chart sync to exact symbol/timeframe/timestamp
- Chart reuse: keep existing CandleChartWidget alive, update in place
- Historical caching: reuse cached bars, fetch only when missing
- Generation counter: rapid clicks race-safe, final trade always wins
- Intelligent viewport + precise entry/exit overlay (temporary)
- Performance: no blocking, minimal requests, cached path is instant

Depends on: core, market, chart, backtest
Owned by: 00_app (composition root) — per ALLOWED graph app → all
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from logging import getLogger
from typing import Any

from chart.events.chart_ready import ChartReady
from core.event_bus.event_bus import EventBus
from core.observable import Signal
from market.events.load_symbol import LoadSymbol
from market.events.timeframe_changed import TimeframeChanged

logger = getLogger(__name__)


@dataclass(frozen=True)
class TradeContext:
    """Minimal trade context needed to position the chart (spec §4)."""

    trade_index: int  # 1-based display id
    symbol: str
    side: str
    timeframe: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    pnl: float
    entry_idx: int
    exit_idx: int
    r_multiple: float | None = None
    bars_held: int | None = None
    exit_reason: str | None = None


class TradeChartController:
    """Bridges Strategy Lab trade selection → chart workspace.

    Events are published via the existing EventBus; chart updates are applied
    via the live widget. The controller owns caching, generation tracking and
    viewport math delegation.
    """

    # emitted when historical load starts/fails for status panel (optional)
    loading_started = Signal(str, str)  # symbol, timeframe
    loading_failed = Signal(str)

    def __init__(
        self,
        bus: EventBus,
        widget: Any,
        window: Any,
        repository: Any,
        overlay: Any,
        context_panel: Any | None = None,
        lab_workspace: Any | None = None,
    ) -> None:
        self._bus = bus
        self._widget = widget
        self._window = window
        self._repo = repository
        self._overlay = overlay
        self._panel = context_panel
        self._lab_workspace = lab_workspace

        self._cache: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._generation: int = 0
        self._pending: tuple[int, TradeContext, Any] | None = None  # gen, ctx, tradeRecord
        self._current_result: Any | None = None
        self._current_config: Any | None = None
        self._current_trades: tuple[Any, ...] = ()
        self._selected_index: int | None = None  # 0-based
        self._last_click_ms: float = 0.0
        self._subscribed = False

    def bind(self) -> None:
        """Subscribe to bus events. Must be called AFTER bootstrap's core subscriptions
        so that chart-ready handling runs LAST (after window clears stale overlay)."""
        if self._subscribed:
            return
        self._subscribed = True
        with contextlib.suppress(Exception):
            self._bus.subscribe(ChartReady, self._on_chart_ready)  # type: ignore[arg-type]
        try:
            from market.events.data_loaded import DataLoaded

            self._bus.subscribe(DataLoaded, self._on_data_loaded)  # type: ignore[arg-type]
        except Exception:
            pass

    # ── public API ────────────────────────────────────────────

    def set_result(self, result: Any | None, config: Any | None = None) -> None:
        """Update the active backtest result/config (called after BacktestCompleted).

        Preserves the selected trade if the new result still contains it (by
        entry_time+symbol), to avoid wiping the trade context when an
        automatic recalc (ChartReady → RunBacktest) completes.
        """
        # Preserve selection if possible
        prev_selected = self._selected_index
        prev_trade: Any | None = None
        if prev_selected is not None and hasattr(self, "_current_trades"):
            try:
                if 0 <= prev_selected < len(self._current_trades):
                    prev_trade = self._current_trades[prev_selected]
            except Exception:
                prev_trade = None
        self._current_result = result
        self._current_config = config
        if result is not None and hasattr(result, "trades"):
            self._current_trades = tuple(result.trades)
        else:
            self._current_trades = ()
        # Try to restore selection if trade still exists in new result
        restored = False
        restored_idx: int | None = None
        if prev_trade is not None and self._current_trades:
            try:
                for i, t in enumerate(self._current_trades):
                    if getattr(t, "entry_time", None) == getattr(
                        prev_trade, "entry_time", None
                    ) and getattr(t, "symbol", None) == getattr(prev_trade, "symbol", None):
                        self._selected_index = i
                        restored_idx = i
                        restored = True
                        break
                if not restored:
                    # Trade no longer exists (different symbol/timeframe or filtered)
                    self._selected_index = None
                    self._pending = None
                    if self._panel is not None:
                        with contextlib.suppress(Exception):
                            self._panel.clear()
                    if hasattr(self, "_overlay") and self._overlay is not None:
                        with contextlib.suppress(Exception):
                            self._overlay.clear_focused()
                else:
                    # Preserve overlay/panel — re-apply focused trade from new list
                    try:
                        new_trade = self._current_trades[restored_idx]  # type: ignore[index]
                        if hasattr(self, "_overlay") and self._overlay is not None:
                            with contextlib.suppress(Exception):
                                self._overlay.set_focused_trade(new_trade, restored_idx + 1)  # type: ignore[attr-defined]
                        # Panel will be updated via controller's next focus or keep as is
                        # Do not clear panel/overlay
                    except Exception:
                        pass
                    # Keep pending as is (if any) — trade context still valid
                    pass
                return
            except Exception:
                pass
        # No previous selection or failed to restore → reset
        self._selected_index = None
        self._pending = None
        if self._panel is not None:
            with contextlib.suppress(Exception):
                self._panel.clear()
        if hasattr(self, "_overlay") and self._overlay is not None:
            with contextlib.suppress(Exception):
                self._overlay.clear_focused()

    def select_trade_by_record(self, trade: Any) -> bool:
        """Select by TradeRecord object (resolves index via entry_time)."""
        if trade is None:
            return False
        # Find index in current_trades by matching entry_time + symbol
        idx = None
        try:
            entry_time = getattr(trade, "entry_time", None)
            symbol = getattr(trade, "symbol", None)
            for i, t in enumerate(self._current_trades):
                if (
                    getattr(t, "entry_time", None) == entry_time
                    and getattr(t, "symbol", None) == symbol
                ):
                    idx = i
                    break
            if idx is None:
                # fallback: try by object identity
                for i, t in enumerate(self._current_trades):
                    if t is trade:
                        idx = i
                        break
        except Exception:
            idx = None
        if idx is None:
            # As last resort, try direct index if trade has attribute
            try:
                # If trade not in current, treat as standalone (e.g., filtered view)
                # Build context directly without index mapping and focus
                return self._select_trade_direct(trade)
            except Exception:
                return False
        return self.select_trade(idx)

    def _select_trade_direct(self, trade: Any) -> bool:
        """Focus a trade object directly even if not in _current_trades (filtered view)."""
        if trade is None:
            return False
        self._generation += 1
        gen = self._generation
        # Find or assign selected index for navigation (use entry_time to map)
        try:
            entry_time = getattr(trade, "entry_time", "")
            # Try to map to full index for next/prev
            for i, t in enumerate(self._current_trades):
                if getattr(t, "entry_time", None) == entry_time:
                    self._selected_index = i
                    break
            else:
                # Not found, keep previous or set to 0
                if self._selected_index is None:
                    self._selected_index = 0
        except Exception:
            self._selected_index = 0
        return self._focus_trade_object(gen, trade)

    def _focus_trade_object(self, gen: int, trade: Any) -> bool:
        """Core focus logic given a trade object and generation."""
        symbol = getattr(trade, "symbol", "") or (
            getattr(self._current_config, "symbol", "") if self._current_config else ""
        )
        timeframe = getattr(self._current_config, "timeframe", "") if self._current_config else ""
        if not timeframe:
            timeframe = getattr(self._window, "_current_timeframe", "") or "15m"
        if not symbol:
            symbol = getattr(self._window, "_current_symbol", "") or ""
        entry_time: str = getattr(trade, "entry_time", "")
        exit_time: str = getattr(trade, "exit_time", "")
        entry_price: float = float(getattr(trade, "entry_price", 0.0))
        exit_price: float = float(getattr(trade, "exit_price", 0.0))
        pnl: float = float(getattr(trade, "pnl", 0.0))
        r_mult = getattr(trade, "r_multiple", None)
        bars_held = getattr(trade, "bars_held", None)
        exit_reason = getattr(trade, "exit_reason", None)
        side = getattr(trade, "side", "")
        entry_idx_win = int(getattr(trade, "entry_index", 0))
        exit_idx_win = int(getattr(trade, "exit_index", entry_idx_win))
        try:
            display_idx = int(self._selected_index) + 1 if self._selected_index is not None else 1
        except Exception:
            display_idx = 1
        ctx = TradeContext(
            trade_index=display_idx,
            symbol=symbol,
            side=side,
            timeframe=timeframe,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            pnl=pnl,
            entry_idx=entry_idx_win,
            exit_idx=exit_idx_win,
            r_multiple=r_mult,
            bars_held=bars_held,
            exit_reason=exit_reason,
        )
        start_ms = time.perf_counter() * 1000.0
        self._last_click_ms = start_ms
        if self._try_instant_focus(gen, ctx, trade):
            elapsed = (time.perf_counter() * 1000.0) - start_ms
            logger.info(
                "Trade #%d instant focus (cache hit) in %.1f ms [gen %d] %s %s %s",
                ctx.trade_index,
                elapsed,
                gen,
                symbol,
                timeframe,
                entry_time[:16],
            )
            return True
        cached = self._cache.get((symbol, timeframe))
        if cached is not None and self._apply_cached_bars(  # noqa: SIM102
            gen, ctx, trade, cached, symbol, timeframe, start_ms
        ):
            return True
        self._pending = (gen, ctx, trade)
        if self._panel is not None:
            with contextlib.suppress(Exception):
                sel = self._selected_index
                has_prev = sel is not None and sel > 0
                has_next = sel is not None and sel < len(self._current_trades) - 1
                self._panel.show_loading()
                with contextlib.suppress(Exception):
                    self._panel.set_nav_enabled(has_prev, has_next)
        try:
            if timeframe:
                self._bus.publish(TimeframeChanged(symbol=symbol, timeframe=timeframe, limit=None))
                with contextlib.suppress(Exception):
                    self._window._current_symbol = symbol  # type: ignore[attr-defined]
                    self._window._current_timeframe = timeframe  # type: ignore[attr-defined]
                logger.info(
                    "Trade #%d requesting %s %s via TimeframeChanged [gen %d]",
                    ctx.trade_index,
                    symbol,
                    timeframe,
                    gen,
                )
            else:
                self._bus.publish(LoadSymbol(symbol=symbol, limit=None))
                logger.info(
                    "Trade #%d requesting %s via LoadSymbol [gen %d]", ctx.trade_index, symbol, gen
                )
            self.loading_started.emit(symbol, timeframe)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to publish load event for trade %d: %s", ctx.trade_index, exc)
            if self._panel is not None:
                with contextlib.suppress(Exception):
                    self._panel.show_error("Failed to load historical context.")
            self.loading_failed.emit(str(exc))
            return False
        elapsed = (time.perf_counter() * 1000.0) - start_ms
        logger.info("Trade #%d load requested in %.1f ms [gen %d]", ctx.trade_index, elapsed, gen)
        return True

    def select_trade(self, trade_index_0based: int) -> bool:
        """Single-click trade selection (spec §5). Returns False if invalid."""
        if not self._current_trades:
            logger.warning("No trades available for selection")
            return False
        if trade_index_0based < 0 or trade_index_0based >= len(self._current_trades):
            logger.warning("Trade index out of range: %d", trade_index_0based)
            return False

        self._generation += 1
        gen = self._generation
        self._selected_index = trade_index_0based
        trade = self._current_trades[trade_index_0based]

        # Build TradeContext from TradeRecord + config
        symbol = getattr(trade, "symbol", "") or (
            getattr(self._current_config, "symbol", "") if self._current_config else ""
        )
        timeframe = getattr(self._current_config, "timeframe", "") if self._current_config else ""
        # fallback to widget timeframe if config missing
        if not timeframe:
            timeframe = getattr(self._window, "_current_timeframe", "") or "15m"
        if not symbol:
            symbol = getattr(self._window, "_current_symbol", "") or ""

        # Timestamps & prices from trade
        entry_time: str = getattr(trade, "entry_time", "")
        exit_time: str = getattr(trade, "exit_time", "")
        entry_price: float = float(getattr(trade, "entry_price", 0.0))
        exit_price: float = float(getattr(trade, "exit_price", 0.0))
        pnl: float = float(getattr(trade, "pnl", 0.0))
        r_mult = getattr(trade, "r_multiple", None)
        bars_held = getattr(trade, "bars_held", None)
        exit_reason = getattr(trade, "exit_reason", None)
        side = getattr(trade, "side", "")

        # entry/exit indices are window-relative; for cache lookup we need global indices
        # We'll resolve global indices via timestamp search after ensuring bars are loaded.

        # Create provisional context with window indices as fallback
        entry_idx_win = int(getattr(trade, "entry_index", 0))
        exit_idx_win = int(getattr(trade, "exit_index", entry_idx_win))

        ctx = TradeContext(
            trade_index=trade_index_0based + 1,
            symbol=symbol,
            side=side,
            timeframe=timeframe,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            pnl=pnl,
            entry_idx=entry_idx_win,
            exit_idx=exit_idx_win,
            r_multiple=r_mult,
            bars_held=bars_held,
            exit_reason=exit_reason,
        )

        # Performance: track click -> state update latency
        start_ms = time.perf_counter() * 1000.0
        self._last_click_ms = start_ms

        # Immediate UI feedback: update selected row highlight synchronously
        # (lab_workspace will be notified via signal; we also set overlay early if cache hit)

        # Check if chart already shows correct symbol/timeframe and bars contain trade timestamps
        if self._try_instant_focus(gen, ctx, trade):
            elapsed = (time.perf_counter() * 1000.0) - start_ms
            logger.info(
                "Trade #%d instant focus (cache hit) in %.1f ms [gen %d] %s %s %s",
                ctx.trade_index,
                elapsed,
                gen,
                symbol,
                timeframe,
                entry_time[:16],
            )
            return True

        # Cache hit for bars but model not yet matching? try cache path
        cached = self._cache.get((symbol, timeframe))
        if cached is not None:  # noqa: SIM102
            # reuse cached bars — build ChartModel without bus round-trip
            if self._apply_cached_bars(gen, ctx, trade, cached, symbol, timeframe, start_ms):
                return True

        # Need to load via EventBus — race-safe pending
        self._pending = (gen, ctx, trade)
        if self._panel is not None:
            with contextlib.suppress(Exception):
                has_prev = trade_index_0based > 0
                has_next = trade_index_0based < len(self._current_trades) - 1
                self._panel.show_loading()
                with contextlib.suppress(Exception):
                    self._panel.set_nav_enabled(has_prev, has_next)

        # Publish appropriate load event (chart reuse: switch symbol/timeframe)
        try:
            # Prefer TimeframeChanged when we have a timeframe (keeps aggregation path)
            if timeframe:
                self._bus.publish(TimeframeChanged(symbol=symbol, timeframe=timeframe, limit=None))
                # Also ensure symbol is selected in watchlist context
                with contextlib.suppress(Exception):
                    self._window._current_symbol = symbol  # type: ignore[attr-defined]
                    self._window._current_timeframe = timeframe  # type: ignore[attr-defined]
                logger.info(
                    "Trade #%d requesting %s %s via TimeframeChanged [gen %d]",
                    ctx.trade_index,
                    symbol,
                    timeframe,
                    gen,
                )
            else:
                self._bus.publish(LoadSymbol(symbol=symbol, limit=None))
                logger.info(
                    "Trade #%d requesting %s via LoadSymbol [gen %d]", ctx.trade_index, symbol, gen
                )
            # ListTimeframes stays correct via existing window handler; no extra publish needed here
            self.loading_started.emit(symbol, timeframe)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to publish load event for trade %d: %s", ctx.trade_index, exc)
            if self._panel is not None:
                with contextlib.suppress(Exception):
                    self._panel.show_error("Failed to load historical context.")
            self.loading_failed.emit(str(exc))
            return False

        elapsed = (time.perf_counter() * 1000.0) - start_ms
        logger.info("Trade #%d load requested in %.1f ms [gen %d]", ctx.trade_index, elapsed, gen)
        return True

    def select_next(self) -> bool:
        if self._selected_index is None or not self._current_trades:
            return False
        nxt = self._selected_index + 1
        if nxt >= len(self._current_trades):
            return False
        return self.select_trade(nxt)

    def select_prev(self) -> bool:
        if self._selected_index is None or not self._current_trades:
            # if nothing selected, go to last
            if self._current_trades:
                return self.select_trade(len(self._current_trades) - 1)
            return False
        prv = self._selected_index - 1
        if prv < 0:
            return False
        return self.select_trade(prv)

    def open_in_market(self) -> None:
        """Escape hatch §17: preserve context and show market tab."""
        if self._selected_index is None or not self._current_trades:
            return
        # Ensure chart already focused; then switch to market view
        with contextlib.suppress(Exception):
            if hasattr(self._window, "show_market"):
                self._window.show_market()
            # window already has correct symbol/timeframe if focus succeeded

    def clear(self) -> None:
        self._pending = None
        self._selected_index = None
        with contextlib.suppress(Exception):
            if self._overlay is not None:
                self._overlay.clear_focused()
            if self._widget is not None:
                self._widget.update()
            if self._panel is not None:
                self._panel.clear()

    # ── internal ──────────────────────────────────────────────

    def _on_data_loaded(self, event: Any) -> None:
        """Cache every DataLoaded payload by (symbol, timeframe)."""
        try:
            symbol = getattr(event, "symbol", "")
            bars = getattr(event, "bars", ())
            if not symbol or not bars:
                return
            # Infer timeframe from first bar's bar_size or via widget model
            # We cache under both the window's current timeframe and infer
            cur_tf = getattr(self._window, "_current_timeframe", None)
            if cur_tf:
                self._cache[(symbol, cur_tf)] = tuple(bars)
                logger.debug("Cache store %s %s (%d bars)", symbol, cur_tf, len(bars))
            # Also store under bar_size for direct lookup
            try:
                b0 = bars[0]
                bs = getattr(b0, "bar_size", None)
                if bs and bs != cur_tf:
                    self._cache[(symbol, bs)] = tuple(bars)
            except Exception:
                pass
        except Exception:
            pass

    def _try_instant_focus(self, gen: int, ctx: TradeContext, trade: Any) -> bool:
        """If widget already has correct symbol/timeframe and contains trade, focus instantly."""  # noqa: E501
        try:
            model = getattr(self._widget, "_model", None)
            if model is None or not getattr(model, "bars", None):
                return False
            if getattr(model, "symbol", None) != ctx.symbol:
                return False
            if getattr(model, "timeframe", None) != ctx.timeframe:  # noqa: SIM102
                # allow case-insensitive match?
                if str(getattr(model, "timeframe", "")).lower() != str(ctx.timeframe).lower():
                    return False
            # locate indices by exact timestamp (timezone-correct)
            widget = self._widget
            e_idx = widget.find_bar_index(ctx.entry_time)
            x_idx = widget.find_bar_index(ctx.exit_time)
            if e_idx is None or x_idx is None:
                return False
            # Stale protection: ensure generation still current
            if gen != self._generation:
                logger.warning("Stale instant focus dropped [gen %d != %d]", gen, self._generation)
                return False
            # Apply focused overlay (temporary, replaces previous)
            with contextlib.suppress(Exception):
                if self._overlay is not None:
                    self._overlay.set_focused_trade(trade, ctx.trade_index)
            # Intelligent viewport
            with contextlib.suppress(Exception):
                widget.focus_on_trade(e_idx, x_idx)
            # Context panel
            if self._panel is not None:
                with contextlib.suppress(Exception):
                    has_prev = self._selected_index is not None and self._selected_index > 0
                    has_next = (
                        self._selected_index is not None
                        and self._selected_index < len(self._current_trades) - 1
                    )
                    self._panel.set_trade(
                        trade_index=ctx.trade_index,
                        symbol=ctx.symbol,
                        side=ctx.side,
                        timeframe=ctx.timeframe,
                        entry_time=ctx.entry_time,
                        entry_price=ctx.entry_price,
                        exit_time=ctx.exit_time,
                        exit_price=ctx.exit_price,
                        pnl=ctx.pnl,
                        r_multiple=ctx.r_multiple,
                        bars_held=ctx.bars_held,
                        exit_reason=ctx.exit_reason,
                        has_prev=has_prev,
                        has_next=has_next,
                    )
            # Preload likely next trades (§20) — lazy via cache only
            with contextlib.suppress(Exception):
                self._preload_neighbors(ctx.trade_index)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Instant focus failed: %s", exc)
            return False

    def _apply_cached_bars(
        self,
        gen: int,
        ctx: TradeContext,
        trade: Any,
        bars: tuple[Any, ...],
        symbol: str,
        timeframe: str,
        start_ms: float,
    ) -> bool:
        """Reuse cached bars without bus fetch: build ChartModel and apply focus."""
        try:
            if gen != self._generation:
                return False
            from chart.models.chart_model import ChartModel

            model = ChartModel(symbol=symbol, bars=bars, timeframe=timeframe, exchange="NSE")
            # Chart is reused — keep widget alive, replace model
            with contextlib.suppress(Exception):
                self._widget.set_model(model)  # resets to latest 150
            # After set_model, viewport is at latest; now snap to trade
            # Locate global indices by timestamp
            e_idx = None
            x_idx = None
            for idx, bar in enumerate(bars):
                if bar.timestamp == ctx.entry_time or bar.timestamp[:16] == ctx.entry_time[:16]:  # noqa: SIM102
                    if e_idx is None:
                        e_idx = idx
                if bar.timestamp == ctx.exit_time or bar.timestamp[:16] == ctx.exit_time[:16]:
                    x_idx = idx
                if e_idx is not None and x_idx is not None:
                    break
            if e_idx is None or x_idx is None:
                # fallback to widget search (handles bar_size prefix)
                e_idx = self._widget.find_bar_index(ctx.entry_time)
                x_idx = self._widget.find_bar_index(ctx.exit_time)
            if e_idx is None or x_idx is None:
                logger.warning(
                    "Cached bars missing trade timestamps %r .. %r", ctx.entry_time, ctx.exit_time
                )
                if self._panel is not None:
                    with contextlib.suppress(Exception):
                        self._panel.show_error("Historical data unavailable for this trade.")
                return False
            with contextlib.suppress(Exception):
                if self._overlay is not None:
                    self._overlay.set_focused_trade(trade, ctx.trade_index)
            with contextlib.suppress(Exception):
                self._widget.focus_on_trade(e_idx, x_idx)
            if self._panel is not None:
                with contextlib.suppress(Exception):
                    has_prev = self._selected_index is not None and self._selected_index > 0
                    has_next = (
                        self._selected_index is not None
                        and self._selected_index < len(self._current_trades) - 1
                    )
                    self._panel.set_trade(
                        trade_index=ctx.trade_index,
                        symbol=ctx.symbol,
                        side=ctx.side,
                        timeframe=ctx.timeframe,
                        entry_time=ctx.entry_time,
                        entry_price=ctx.entry_price,
                        exit_time=ctx.exit_time,
                        exit_price=ctx.exit_price,
                        pnl=ctx.pnl,
                        r_multiple=ctx.r_multiple,
                        bars_held=ctx.bars_held,
                        exit_reason=ctx.exit_reason,
                        has_prev=has_prev,
                        has_next=has_next,
                    )
            # update window current state so future checks hit
            with contextlib.suppress(Exception):
                self._window._current_symbol = symbol  # type: ignore[attr-defined]
                self._window._current_timeframe = timeframe  # type: ignore[attr-defined]
            # Publish WindowRendered-like? not needed
            elapsed = (time.perf_counter() * 1000.0) - start_ms
            logger.info(
                "Trade #%d cache-reuse focus in %.1f ms [gen %d] %s %s",
                ctx.trade_index,
                elapsed,
                gen,
                symbol,
                timeframe,
            )
            # also cache is already present; pending cleared
            self._pending = None
            with contextlib.suppress(Exception):
                self._preload_neighbors(ctx.trade_index)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Cache reuse focus failed: %s", exc)
            return False

    def _on_chart_ready(self, event: ChartReady) -> None:
        """After async load, snap to pending trade if generation still matches."""
        try:
            if self._pending is None:
                return
            gen, ctx, trade = self._pending
            if gen != self._generation:
                logger.info(
                    "Stale ChartReady dropped [pending gen %d != current %d]", gen, self._generation
                )
                return
            model = getattr(event, "model", None)
            if model is None:
                return
            # Stale data protection §24
            if getattr(model, "symbol", None) != ctx.symbol:
                logger.warning(
                    "Symbol mismatch: selected trade %s vs loaded %s",
                    ctx.symbol,
                    getattr(model, "symbol", None),
                )
                return
            # Timeframe mismatch — do not show wrong context
            loaded_tf = getattr(model, "timeframe", "")
            if loaded_tf and ctx.timeframe and loaded_tf.lower() != ctx.timeframe.lower():
                logger.warning("Timeframe mismatch: %s vs %s", ctx.timeframe, loaded_tf)
                # Still try if close? For now warn but continue— bars are at loaded_tf
                # If mismatch, markers would be on wrong timeframe, so abort.
                if self._panel is not None:
                    with contextlib.suppress(Exception):
                        self._panel.show_error(
                            f"Timeframe mismatch: trade {ctx.timeframe} vs chart {loaded_tf}"
                        )
                return

            # Cache the loaded bars for future instant navigation
            with contextlib.suppress(Exception):
                bars = getattr(model, "bars", ())
                if bars:
                    self._cache[(ctx.symbol, ctx.timeframe)] = tuple(bars)
                    # also under loaded_tf if different
                    if loaded_tf and loaded_tf != ctx.timeframe:
                        self._cache[(ctx.symbol, loaded_tf)] = tuple(bars)

            # Locate entry/exit by exact timestamp
            widget = self._widget
            e_idx = widget.find_bar_index(ctx.entry_time)
            x_idx = widget.find_bar_index(ctx.exit_time)
            if e_idx is None or x_idx is None:
                logger.warning(
                    "Trade timestamps not in loaded bars: %r .. %r", ctx.entry_time, ctx.exit_time
                )
                if self._panel is not None:
                    with contextlib.suppress(Exception):
                        self._panel.show_error("Historical data unavailable for this trade.")
                self._pending = None
                return

            # Apply focused overlay (temporary)
            with contextlib.suppress(Exception):
                if self._overlay is not None:
                    self._overlay.set_focused_trade(trade, ctx.trade_index)
            # Intelligent viewport
            with contextlib.suppress(Exception):
                widget.focus_on_trade(e_idx, x_idx)
            # Context panel
            if self._panel is not None:
                with contextlib.suppress(Exception):
                    has_prev = self._selected_index is not None and self._selected_index > 0
                    has_next = (
                        self._selected_index is not None
                        and self._selected_index < len(self._current_trades) - 1
                    )
                    self._panel.set_trade(
                        trade_index=ctx.trade_index,
                        symbol=ctx.symbol,
                        side=ctx.side,
                        timeframe=ctx.timeframe,
                        entry_time=ctx.entry_time,
                        entry_price=ctx.entry_price,
                        exit_time=ctx.exit_time,
                        exit_price=ctx.exit_price,
                        pnl=ctx.pnl,
                        r_multiple=ctx.r_multiple,
                        bars_held=ctx.bars_held,
                        exit_reason=ctx.exit_reason,
                        has_prev=has_prev,
                        has_next=has_next,
                    )
            # Done — clear pending but keep generation
            self._pending = None
            logger.info(
                "Trade #%d async focus complete [gen %d] entry %d exit %d %s %s",
                ctx.trade_index,
                gen,
                e_idx,
                x_idx,
                ctx.entry_time[:16],
                ctx.exit_time[:16],
            )
            with contextlib.suppress(Exception):
                self._preload_neighbors(ctx.trade_index)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ChartReady trade focus failed: %s", exc)

    def _preload_neighbors(self, trade_index_1based: int) -> None:
        """Optionally preload bars for neighboring trades if they share symbol/timeframe.

        §20: prepare likely next trades without excessive cost — use cache only.
        """
        try:
            if not self._current_trades:
                return
            for delta in (-1, 1):
                n_idx = trade_index_1based - 1 + delta  # 0-based
                if 0 <= n_idx < len(self._current_trades):
                    nt = self._current_trades[n_idx]
                    sym = getattr(nt, "symbol", "")
                    if not sym and self._current_config:
                        sym = getattr(self._current_config, "symbol", "")
                    tf = (
                        getattr(self._current_config, "timeframe", "")
                        if self._current_config
                        else ""
                    )
                    if not tf:
                        tf = getattr(self._window, "_current_timeframe", "") or ""
                    key = (sym, tf)
                    if key not in self._cache:
                        # Do not fetch proactively for different symbol — would be heavy.
                        # Only preload if same key already cached (no-op).
                        continue
        except Exception:
            pass
