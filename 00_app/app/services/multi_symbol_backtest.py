"""MultiSymbolBacktestCoordinator — fan one RUN out across selected symbols.

The Strategy Lab run button starts ONE batch through the existing
background worker (off-UI-thread, bounded process pool); the worker reports
stock-level progress and one outcome per symbol. Each symbol's
:class:`StrategyResult` stays separate internally; on completion they are
merged into one aggregate (trades keep their ``symbol`` field, so
per-symbol research filtering stays exact).

Selection stores symbols only — no market data is touched until a run
starts. Single-symbol runs never enter the coordinator (existing flow).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
from logging import getLogger
from typing import Any

from backtest.events import BacktestCompleted, BacktestFailed, BacktestStarted
from backtest.models.result import BacktestResult, StrategyResult
from PySide6.QtCore import QObject, Signal

logger = getLogger(__name__)

MERGED_SYMBOL = "MULTI"


@dataclass(frozen=True)
class BatchRequest:
    """The shared parameters for every symbol in one batch."""

    strategy_id: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    slippage_pct: float
    commission_pct: float


@dataclass(frozen=True)
class BatchOutcome:
    """Terminal state of one batch."""

    symbols: tuple[str, ...]
    results: tuple[StrategyResult, ...]
    errors: dict[str, str] = field(default_factory=dict)
    merged: StrategyResult | None = None
    bars_by_symbol: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when at least one symbol produced a result."""
        return bool(self.results)


def merge_results(
    results: list[StrategyResult], request: BatchRequest
) -> tuple[StrategyResult, dict[str, int]]:
    """Aggregate per-symbol results into one StrategyResult.

    Trades are concatenated in (entry_time, symbol) order — each keeps its
    own ``symbol``/indices from its run window — and the equity curve and
    metrics are recomputed from the merged trades with the existing engine
    helpers, so numbers stay honest. Returns the merged result plus the
    per-symbol bar windows (for the Exposure denominator).
    """
    from backtest.engine.metrics import compute_equity_curve, compute_metrics

    ordered = sorted(results, key=lambda r: r.config.symbol)
    trades = sorted(
        (t for r in ordered for t in r.trades),
        key=lambda t: (t.entry_time, t.symbol, t.entry_index),
    )
    starts = [r.period_start for r in ordered if r.period_start]
    ends = [r.period_end for r in ordered if r.period_end]
    start = min(starts) if starts else None
    end = max(ends) if ends else None
    capital = request.initial_capital
    curve = compute_equity_curve(tuple(trades), capital, start)
    metrics = compute_metrics(tuple(trades), curve, capital)
    base = ordered[0]
    merged = StrategyResult(
        strategy_id=base.strategy_id,
        name=base.name,
        config=replace(base.config, symbol=MERGED_SYMBOL),
        trades=tuple(trades),
        equity_curve=curve,
        metrics=metrics,
        bars_used=sum(r.bars_used for r in ordered),
        period_start=start,
        period_end=end,
        chart_series=(),
    )
    bars_by_symbol = {r.config.symbol: r.bars_used for r in ordered}
    return merged, bars_by_symbol


class MultiSymbolBacktestCoordinator(QObject):
    """Runs one strategy over selected symbols as a single bounded batch.

    Bootstrap owns the only instance and injects the background worker:
    :meth:`start` publishes one :class:`BacktestStarted` (so the existing
    busy UI engages) and enqueues one batch job; the worker's
    ``batch_progress``/``batch_done``/``batch_failed`` signals arrive back
    here, and :meth:`on_batch_done` merges outcomes into a :class:`BatchOutcome`
    plus one merged :class:`BacktestCompleted` for the regular UI path.
    Stale completions (after cancel/reset) are dropped by request id.
    """

    batch_started = Signal(tuple)  # symbols
    batch_progress = Signal(object)  # (done, total) — stock-level only
    batch_finalizing = Signal(object)  # request_id — pool done, merge/publish next
    batch_finished = Signal(object)  # BatchOutcome
    batch_failed = Signal(str)  # reason

    def __init__(self, bus: Any, worker: Any | None = None) -> None:
        super().__init__()
        self._bus = bus
        self._worker = worker
        self._request: BatchRequest | None = None
        self._symbols: tuple[str, ...] = ()
        self._batch_seq = 0
        self._active_id: str | None = None

    # ── state ─────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True while a multi-symbol batch is in flight."""
        return self._request is not None

    def cancel(self) -> None:
        """Drop the batch (Lab reset). In-flight results are discarded."""
        try:
            if self._worker is not None:
                self._worker.cancel_batch()
        except Exception:  # noqa: BLE001
            pass
        self._reset()

    def _reset(self) -> None:
        self._request = None
        self._symbols = ()
        self._active_id = None

    # ── driving ───────────────────────────────────────────────────

    def start(self, symbols: tuple[str, ...], request: BatchRequest) -> str:
        """Begin a batch; enqueues one job on the worker. Returns its id."""
        if self._worker is None:
            raise RuntimeError("batch worker not attached")
        self.cancel()
        self._batch_seq += 1
        request_id = f"msb{self._batch_seq}"
        self._request = request
        self._symbols = tuple(symbols)
        self._active_id = request_id
        self.batch_started.emit(self._symbols)
        with contextlib.suppress(Exception):
            self._bus.publish(
                BacktestStarted(
                    request_id=request_id,
                    strategy_ids=(request.strategy_id,),
                    symbol=MERGED_SYMBOL,
                    timeframe=request.timeframe,
                )
            )
        from backtest.worker import BatchEnqueued

        self._worker.enqueue_batch(
            BatchEnqueued(
                request_id=request_id,
                strategy_id=request.strategy_id,
                symbols=self._symbols,
                timeframe=request.timeframe,
                start_date=request.start_date,
                end_date=request.end_date,
                initial_capital=request.initial_capital,
                slippage_pct=request.slippage_pct,
                commission_pct=request.commission_pct,
            )
        )
        return request_id

    def on_batch_progress(self, payload: object) -> None:
        """Worker callback: re-emit stock-level progress for live batches."""
        try:
            request_id, done, total = payload  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return
        if not self.active or request_id != self._active_id:
            return
        self.batch_progress.emit((int(done), int(total)))

    def on_batch_done(self, payload: object) -> None:
        """Worker callback: merge per-symbol outcomes into one batch result."""
        try:
            request_id, outcomes = payload  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return
        if not self.active or request_id != self._active_id:
            return
        request = self._request
        symbols = self._symbols
        assert request is not None
        self.batch_finalizing.emit(request_id)
        self._reset()
        results: dict[str, StrategyResult] = {}
        errors: dict[str, str] = {}
        for outcome in outcomes or ():
            symbol = getattr(outcome, "symbol", "")
            result = getattr(outcome, "result", None)
            if result is not None:
                results[symbol] = result
            else:
                errors[symbol] = str(getattr(outcome, "error", None) or "backtest error")
        ordered = [results[s] for s in symbols if s in results]
        if not ordered:
            reason = "; ".join(f"{s}: {errors.get(s, 'no result')}" for s in symbols)
            reason = reason or "no results"
            self.batch_failed.emit(reason)
            with contextlib.suppress(Exception):
                self._bus.publish(BacktestFailed(request_id=f"{request_id}-final", reason=reason))
            return
        merged, bars_by_symbol = merge_results(ordered, request)
        outcome = BatchOutcome(
            symbols=symbols,
            results=tuple(ordered),
            errors=errors,
            merged=merged,
            bars_by_symbol=bars_by_symbol,
        )
        self.batch_finished.emit(outcome)
        with contextlib.suppress(Exception):
            self._bus.publish(
                BacktestCompleted(
                    request_id=f"{request_id}-final",
                    result=BacktestResult(results=(merged,)),
                )
            )
        with contextlib.suppress(Exception):
            logger.info(
                "multi-symbol batch done: %d/%d symbols, %d trades",
                len(ordered),
                len(symbols),
                len(merged.trades),
            )

    def on_batch_failed(self, payload: object) -> None:
        """Worker callback: the whole batch failed before any outcome."""
        try:
            request_id, reason = payload  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return
        if not self.active or request_id != self._active_id:
            return
        self._reset()
        text = str(reason or "batch error")
        self.batch_failed.emit(text)
        with contextlib.suppress(Exception):
            self._bus.publish(BacktestFailed(request_id=f"{request_id}-final", reason=text))
