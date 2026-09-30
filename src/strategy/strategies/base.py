"""Base class for native Python strategies."""

from __future__ import annotations

import math
from collections import deque
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from logging import getLogger

from strategy.models.parameters import StrategyParameters
from strategy.models.plot_event import (
    MarkerType,
    PlotEvent,
    PlotLifecycle,
    PlotType,
    PlotValidationError,
    RenderLayer,
    default_layer,
    make_event_id,
)
from strategy.models.signal import Signal, SignalKind
from strategy.runtime import BarView, StrategyLogic

logger = getLogger(__name__)

_DEFAULT_MAX_HISTORY = 200
_DEFAULT_WARMUP = 20
_WARMUP_PERIOD_KEYS = ("slow_period", "fast_period", "rsi_period")


def _parse_bar_time(stamp: object) -> datetime | None:
    """Parse a bar timestamp; None when unparseable (caller ignores)."""
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    text = stamp.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _parse_exit_time(time_str: object) -> tuple[int, int] | None:
    """Parse an ``HH:MM`` exit time; None when malformed (caller ignores)."""
    if not isinstance(time_str, str):
        return None
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute


class PythonStrategy(StrategyLogic):
    """Base for all native Python strategies — maintains indicator history."""

    def __init__(self, params: StrategyParameters | dict[str, float] | None = None) -> None:
        parsed: dict[str, float] = {}
        if params is not None:
            try:
                source = dict(params)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid strategy params mapping: {exc}") from exc
            for key, value in source.items():
                try:
                    parsed[key] = float(value)  # type: ignore[arg-type]
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid strategy param {key!r}: {value!r} is not a number"
                    ) from exc
        self.params: dict[str, float] = parsed
        raw_history = self.params.get("max_history", _DEFAULT_MAX_HISTORY)
        try:
            max_history = int(raw_history)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"max_history must be an int >= 1, got {raw_history!r}") from exc
        if max_history < 1:
            raise ValueError(f"max_history must be >= 1, got {max_history!r}")
        self._max_history = max_history
        self.closes: deque[float] = deque(maxlen=max_history)
        self.highs: deque[float] = deque(maxlen=max_history)
        self.lows: deque[float] = deque(maxlen=max_history)
        self.volumes: deque[int] = deque(maxlen=max_history)
        self._pending_kind: SignalKind | None = None
        self._pending_sl: float | None = None
        self._pending_tp: float | None = None
        self._pending_time_exit: str | None = None
        # Chart plot series — title -> {bar_index: value}
        self._plot_series: dict[str, dict[int, float]] = {}
        self._plot_meta: dict[str, dict[str, str]] = {}
        self._current_bar_index: int = 0
        # Universal strategy-owned plot events — event_id -> PlotEvent.
        # Trading API (buy/sell/...) never touches these; plot API never
        # touches trading state. Exact logical coordinates, unique per event
        # so same-bar multiples never overwrite each other.
        self._plot_events: dict[str, PlotEvent] = {}
        self._plot_auto_seq: dict[str, int] = {}
        self._muted_signal_bars: set[int] = set()
        self._history_warned = False

    def warmup(self) -> int:
        """Derive warmup from indicator periods (slow/fast/RSI), default 20."""
        periods: list[int] = []
        for key in _WARMUP_PERIOD_KEYS:
            if key in self.params:
                try:
                    periods.append(int(self.params[key]))
                except (TypeError, ValueError):
                    continue
        candidates = [p for p in periods if p > 0]
        return max(candidates) if candidates else _DEFAULT_WARMUP

    def on_bar(self, view: BarView) -> Signal | None:
        bar = view.bar
        if len(self.closes) >= self._max_history and not self._history_warned:
            logger.warning(
                "indicator history truncated at max_history=%d — oldest bars dropped",
                self._max_history,
            )
            self._history_warned = True
        self.closes.append(bar.close)
        self.highs.append(bar.high)
        self.lows.append(bar.low)
        self.volumes.append(bar.volume)
        self._pending_kind = None
        self._pending_sl = None
        self._pending_tp = None
        self._pending_time_exit = None
        self._current_bar_index = view.index

        signal = self.on_bar_logic(view)

        if signal is not None:
            # Merge pending SL/TP into a directly returned signal instead of
            # dropping them — stop_loss()/take_profit() accompany either path.
            stop_loss = signal.stop_loss if signal.stop_loss is not None else self._pending_sl
            take_profit = signal.take_profit if signal.take_profit is not None else self._pending_tp
            if stop_loss is not signal.stop_loss or take_profit is not signal.take_profit:
                signal = replace(signal, stop_loss=stop_loss, take_profit=take_profit)
            return signal

        # Time exit fires only when logic produced no signal — it never
        # overwrites a logic decision (returned Signal or pending buy/sell).
        if self._pending_time_exit and self._pending_kind is None and not view.state.flat:
            exit_time = _parse_exit_time(self._pending_time_exit)
            bar_time = _parse_bar_time(bar.timestamp)
            if (
                exit_time is not None
                and bar_time is not None
                and (bar_time.hour, bar_time.minute) >= exit_time
            ):
                self._pending_kind = (
                    SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL
                )

        if self._pending_kind is None:
            return None

        return Signal(
            index=view.index,
            timestamp=bar.timestamp,
            kind=self._pending_kind,
            price=bar.close,
            stop_loss=self._pending_sl,
            take_profit=self._pending_tp,
        )

    def on_bar_logic(self, view: BarView) -> Signal | None:
        raise NotImplementedError

    # Helpers for strategies
    def buy(self) -> None:
        self._pending_kind = SignalKind.BUY

    def sell(self) -> None:
        self._pending_kind = SignalKind.SELL

    def close_position(self, view: BarView) -> None:
        if not view.state.flat:
            self._pending_kind = SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL

    def stop_loss(self, price: float) -> None:
        self._pending_sl = float(price)

    def take_profit(self, price: float) -> None:
        self._pending_tp = float(price)

    def time_exit(self, time_str: str) -> None:
        self._pending_time_exit = str(time_str)

    def set_owner_id(self, owner_id: str) -> None:
        """Set the strategy identity used as ``source_strategy`` on plots."""
        with suppress(Exception):
            self._owner_id = str(owner_id or "")

    def _strategy_name(self) -> str:
        owner = str(getattr(self, "_owner_id", "") or "").strip()
        if owner:
            return owner
        return type(self).__name__

    def _next_plot_id(self, base: str, bar: int) -> str:
        key = f"{base}@{bar}"
        seq = int(self._plot_auto_seq.get(key, 0)) + 1
        self._plot_auto_seq[key] = seq
        return base if seq == 1 else f"{base}#{seq}"

    def _store_plot_event(self, event: PlotEvent) -> str:
        """Store one plot event; same-bar events coexist (keyed by event_id).

        Universal events render ONLY through the PlotStore path — they are
        never mirrored into the legacy title series, so one strategy event
        can never produce two visible representations. The legacy
        ``plot()``/``get_chart_series()`` path stays intact for direct
        legacy callers.
        """
        existing = self._plot_events.get(event.event_id)
        if existing is not None and existing.version >= event.version:
            return event.event_id
        self._plot_events[event.event_id] = event
        return event.event_id

    def _emit_point(
        self,
        plot_type: PlotType,
        bar_index: int,
        price: float,
        plot_id: str | None,
        marker_type: MarkerType | None = None,
        text: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        extend_bars: int | None = None,
        dependencies: tuple[str, ...] = (),
        timestamp: str | None = None,
    ) -> str:
        name = self._strategy_name()
        base = plot_id or f"{plot_type.value.lower()}_{bar_index}"
        pid = base if plot_id else self._next_plot_id(base, bar_index)
        event_id = make_event_id(name, symbol, timeframe, bar_index, pid)
        event = PlotEvent(
            event_id=event_id,
            source_strategy=name,
            plot_id=pid,
            plot_type=plot_type,
            symbol=symbol,
            timeframe=timeframe,
            bar_index=int(bar_index),
            price=float(price),
            timestamp=timestamp,
            marker_type=marker_type,
            text=text,
            layer=layer if layer is not None else default_layer(plot_type),
            lifecycle=PlotLifecycle.ACTIVE,
            extend_bars=extend_bars,
            dependencies=tuple(dependencies),
        )
        return self._store_plot_event(event)

    def plot(self, value: float, title: str) -> None:
        """Record a chart plot point — title identifies the series.

        Data is stored as {bar_index: value} so the backtest runner can
        package it into ChartSeries and the chart renderer can draw
        persistent lines across the relevant bars. Non-finite values
        raise :class:`PlotValidationError` instead of being silently dropped.
        """
        if not isinstance(title, str) or not title:
            raise PlotValidationError("plot title must be a non-empty string")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise PlotValidationError(f"plot value must be numeric, got {value!r}") from exc
        if not math.isfinite(numeric):
            raise PlotValidationError(f"plot value must be finite, got {value!r}")
        self._plot_series.setdefault(title, {})[self._current_bar_index] = numeric

    # ── universal strategy-owned plot API (visual only, never trades) ──

    def plot_marker(
        self,
        bar_index: int | None,
        price: float,
        marker_type: MarkerType,
        plot_id: str | None = None,
        text: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        timestamp: str | None = None,
    ) -> str:
        """Emit a generic marker (UP_ARROW/DOWN_ARROW/CIRCLE_X/...) at exact coords."""
        return self._emit_point(
            PlotType.MARKER,
            self._current_bar_index if bar_index is None else int(bar_index),
            float(price),
            plot_id,
            marker_type=marker_type,
            text=text,
            symbol=symbol,
            timeframe=timeframe,
            layer=layer,
            timestamp=timestamp,
        )

    def plot_line(
        self,
        start_bar: int,
        start_price: float,
        end_bar: int,
        end_price: float,
        plot_id: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        extend_bars: int | None = None,
        timestamp: str | None = None,
    ) -> str:
        """Emit a generic line segment (visual only)."""
        return self._emit_span(
            PlotType.LINE,
            start_bar,
            start_price,
            end_bar,
            end_price,
            plot_id,
            symbol=symbol,
            timeframe=timeframe,
            layer=layer,
            extend_bars=extend_bars,
            timestamp=timestamp,
        )

    def _emit_span(
        self,
        plot_type: PlotType,
        start_bar: int,
        start_price: float,
        end_bar: int,
        end_price: float,
        plot_id: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        extend_bars: int | None = None,
        timestamp: str | None = None,
    ) -> str:
        name = self._strategy_name()
        base = plot_id or f"{plot_type.value.lower()}_{start_bar}_{end_bar}"
        pid = base if plot_id else self._next_plot_id(base, int(start_bar))
        event_id = make_event_id(name, symbol, timeframe, int(start_bar), pid)
        return self._store_plot_event(
            PlotEvent(
                event_id=event_id,
                source_strategy=name,
                plot_id=pid,
                plot_type=plot_type,
                symbol=symbol,
                timeframe=timeframe,
                start_bar=int(start_bar),
                end_bar=int(end_bar),
                start_price=float(start_price),
                end_price=float(end_price),
                timestamp=timestamp,
                text=None,
                layer=layer if layer is not None else default_layer(plot_type),
                extend_bars=extend_bars,
            )
        )

    def plot_ray(
        self,
        start_bar: int,
        start_price: float,
        plot_id: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        extend_bars: int | None = None,
        timestamp: str | None = None,
    ) -> str:
        """Emit a generic ray (origin bar + price; extend_bars caps visual only)."""
        name = self._strategy_name()
        base = plot_id or f"ray_{start_bar}"
        pid = base if plot_id else self._next_plot_id(base, int(start_bar))
        event_id = make_event_id(name, symbol, timeframe, int(start_bar), pid)
        return self._store_plot_event(
            PlotEvent(
                event_id=event_id,
                source_strategy=name,
                plot_id=pid,
                plot_type=PlotType.RAY,
                symbol=symbol,
                timeframe=timeframe,
                start_bar=int(start_bar),
                start_price=float(start_price),
                timestamp=timestamp,
                text=None,
                layer=layer if layer is not None else default_layer(PlotType.RAY),
                extend_bars=extend_bars,
            )
        )

    def plot_segment(
        self,
        start_bar: int,
        start_price: float,
        end_bar: int,
        end_price: float,
        plot_id: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        timestamp: str | None = None,
    ) -> str:
        """Emit a generic bounded segment (exact start/end, no extension)."""
        return self._emit_span(
            PlotType.SEGMENT,
            start_bar,
            start_price,
            end_bar,
            end_price,
            plot_id,
            symbol=symbol,
            timeframe=timeframe,
            layer=layer if layer is not None else RenderLayer.SL_LINE,
            extend_bars=None,
            timestamp=timestamp,
        )

    def plot_zone(
        self,
        start_bar: int,
        end_bar: int,
        start_price: float,
        end_price: float,
        plot_id: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        timestamp: str | None = None,
    ) -> str:
        """Emit a generic price/time zone rectangle."""
        name = self._strategy_name()
        base = plot_id or f"zone_{start_bar}_{end_bar}"
        pid = base if plot_id else self._next_plot_id(base, int(start_bar))
        event_id = make_event_id(name, symbol, timeframe, int(start_bar), pid)
        return self._store_plot_event(
            PlotEvent(
                event_id=event_id,
                source_strategy=name,
                plot_id=pid,
                plot_type=PlotType.ZONE,
                symbol=symbol,
                timeframe=timeframe,
                start_bar=int(start_bar),
                end_bar=int(end_bar),
                start_price=float(start_price),
                end_price=float(end_price),
                timestamp=timestamp,
                text=None,
                layer=layer if layer is not None else default_layer(PlotType.ZONE),
            )
        )

    def plot_label(
        self,
        bar_index: int | None,
        price: float,
        text: str,
        plot_id: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        timestamp: str | None = None,
    ) -> str:
        """Emit a generic text label (only labels carry text by default)."""
        return self._emit_point(
            PlotType.LABEL,
            self._current_bar_index if bar_index is None else int(bar_index),
            float(price),
            plot_id,
            text=str(text),
            symbol=symbol,
            timeframe=timeframe,
            layer=layer,
            timestamp=timestamp,
        )

    def plot_level(
        self,
        start_bar: int,
        price: float,
        end_bar: int | None = None,
        plot_id: str | None = None,
        symbol: str = "",
        timeframe: str = "",
        layer: RenderLayer | None = None,
        extend_bars: int | None = None,
        timestamp: str | None = None,
    ) -> str:
        """Emit a generic horizontal level (REF/SL style visuals, no semantics)."""
        name = self._strategy_name()
        base = plot_id or f"level_{start_bar}"
        pid = base if plot_id else self._next_plot_id(base, int(start_bar))
        last = int(end_bar) if end_bar is not None else int(start_bar)
        event_id = make_event_id(name, symbol, timeframe, int(start_bar), pid)
        return self._store_plot_event(
            PlotEvent(
                event_id=event_id,
                source_strategy=name,
                plot_id=pid,
                plot_type=PlotType.HORIZONTAL_LEVEL,
                symbol=symbol,
                timeframe=timeframe,
                start_bar=int(start_bar),
                end_bar=last,
                start_price=float(price),
                timestamp=timestamp,
                text=None,
                layer=layer if layer is not None else default_layer(PlotType.HORIZONTAL_LEVEL),
                extend_bars=extend_bars,
            )
        )

    def update_plot(self, event_id: str, **changes: object) -> str | None:
        """Version a live plot (price/bar/text); renderer uses latest valid.

        When the change moves the anchor bar (``bar_index``/``start_bar``),
        the deterministic ``event_id`` is recomputed from the new anchor —
        the old key is retired and the new id is returned. A target bar
        beyond the current bar raises :class:`ValueError` (future plots are
        forbidden); unknown ids return None.
        """
        current = self._plot_events.get(str(event_id))
        if current is None:
            return None
        allowed = {
            "bar_index",
            "start_bar",
            "end_bar",
            "price",
            "start_price",
            "end_price",
            "timestamp",
            "text",
            "lifecycle",
            "extend_bars",
            "layer",
            "marker_type",
            "dependencies",
            "metadata",
        }
        safe = {key: value for key, value in changes.items() if key in allowed}
        for bar_key in ("bar_index", "start_bar", "end_bar"):
            if bar_key in safe and safe[bar_key] is not None:
                target = safe[bar_key]
                if isinstance(target, bool) or not isinstance(target, int):
                    raise PlotValidationError(f"{bar_key} must be an int, got {target!r}")
                if target < 0:
                    raise PlotValidationError(f"{bar_key} must be >= 0, got {target!r}")
                if target > self._current_bar_index:
                    raise ValueError(
                        f"{bar_key} {target} is beyond current bar {self._current_bar_index}"
                    )
        updated = current.with_update(**safe)
        new_anchor = updated.anchor_bar
        if new_anchor != current.anchor_bar:
            name = self._strategy_name()
            new_id = make_event_id(
                name, updated.symbol, updated.timeframe, new_anchor, updated.plot_id
            )
            del self._plot_events[current.event_id]
            updated = replace(updated, event_id=new_id)
            self._plot_events[new_id] = updated
            return new_id
        return self._store_plot_event(updated)

    def remove_plot(self, event_id: str) -> bool:
        """Mark a plot REMOVED (historical record kept, renderer skips)."""
        with suppress(Exception):
            current = self._plot_events.get(str(event_id))
            if current is None:
                return False
            self._store_plot_event(current.with_update(lifecycle=PlotLifecycle.REMOVED))
            return True
        return False

    def get_plot_events(self) -> tuple[PlotEvent, ...]:
        """All universal plot events, deterministic order (bar, layer, id)."""
        return tuple(
            sorted(
                self._plot_events.values(),
                key=lambda event: (
                    event.anchor_bar,
                    event.layer.value if event.layer is not None else 60,
                    event.event_id,
                ),
            )
        )

    def mute_signal_bar(self, bar_index: int) -> bool:
        """Declare one bar visually silent for execution signal markers.

        Generic ownership hook: the strategy decides that a bar (e.g. an
        intentionally invisible exit) must not gain an execution pill even
        though no PlotEvent exists there. Trading state is never touched;
        the bar index is exact and deterministic. Returns True when recorded.
        """
        with suppress(Exception):
            if isinstance(bar_index, bool) or not isinstance(bar_index, int):
                return False
            if bar_index < 0:
                return False
            self._muted_signal_bars.add(bar_index)
            return True
        return False

    def get_muted_signal_bars(self) -> tuple[int, ...]:
        """Bars muted via :meth:`mute_signal_bar`, ascending deterministic."""
        return tuple(sorted(self._muted_signal_bars))

    def get_chart_series(self) -> dict[str, dict[int, float]]:
        """Return chart plot series in legacy title-keyed format."""
        return {k: dict(v) for k, v in self._plot_series.items()}

    def set_chart_series_meta(self, title: str, meta: dict[str, str]) -> None:
        """Attach metadata for one plot series title (read back via ``get_*``).

        The companion ``get_chart_series_meta*`` accessors were previously
        dead (nothing ever populated ``_plot_meta``); this setter closes
        that gap so strategies can label their series.
        """
        if not isinstance(title, str) or not title:
            raise PlotValidationError("series title must be a non-empty string")
        if not isinstance(meta, dict):
            raise PlotValidationError(f"series meta must be a dict, got {meta!r}")
        self._plot_meta[title] = {str(k): str(v) for k, v in meta.items()}

    def get_chart_series_with_owner(self) -> dict[tuple[str, str], dict[int, float]]:
        """Return chart plot series keyed by (owner_id, title)."""
        owner = getattr(self, "_owner_id", "") or ""
        return {(owner, k): dict(v) for k, v in self._plot_series.items()}

    def get_chart_series_meta(self) -> dict[str, dict[str, str]]:
        """Return chart plot series metadata (title-keyed)."""
        return {k: dict(v) for k, v in self._plot_meta.items()}

    def get_chart_series_meta_with_owner(self) -> dict[tuple[str, str], dict[str, str]]:
        """Return chart plot series metadata keyed by (owner_id, title)."""
        owner = getattr(self, "_owner_id", "") or ""
        return {(owner, k): dict(v) for k, v in self._plot_meta.items()}
