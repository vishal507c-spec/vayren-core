"""MultiSymbolBacktestCoordinator — fan one RUN out across selected symbols.

The Strategy Lab run button publishes a single :class:`RunBacktest` per
symbol through the existing bus/worker (async, off-UI-thread) and this
coordinator chains them: when one finishes it publishes the next. Each
symbol's :class:`StrategyResult` stays separate internally; on completion
they are merged into one aggregate (trades keep their ``symbol`` field, so
per-symbol research filtering stays exact).

Selection stores symbols only — no market data is touched until a run
starts. Single-symbol runs never enter the coordinator (existing flow).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
from logging import getLogger
from typing import Any

from backtest.events import BacktestCompleted, BacktestFailed, RunBacktest
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
    """Chains per-symbol RunBacktest requests and merges their outcomes.

    Bootstrap owns the only instance: it subscribes ``on_completed`` /
    ``on_failed`` to the bus BEFORE the regular handlers, wires
    ``batch_finished`` to surface per-symbol errors, and calls
    :meth:`start` when the lab run selects 2+ symbols.
    """

    batch_started = Signal(tuple)  # symbols
    batch_finished = Signal(object)  # BatchOutcome
    batch_failed = Signal(str)  # reason

    def __init__(self, bus: Any) -> None:
        super().__init__()
        self._bus = bus
        self._request: BatchRequest | None = None
        self._symbols: list[str] = []
        self._index = 0
        self._batch_id = 0
        self._current_id: str | None = None
        self._member_ids: set[str] = set()
        self._results: dict[str, StrategyResult] = {}
        self._errors: dict[str, str] = {}

    # ── state ─────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True while a multi-symbol batch is in flight."""
        return self._request is not None

    def owns(self, request_id: str) -> bool:
        """True when ``request_id`` is the batch request currently in flight."""
        return self.active and request_id == self._current_id

    def is_member(self, request_id: str) -> bool:
        """True when ``request_id`` belongs to any batch request ever issued.

        Regular per-result UI handlers skip these; only the merged final
        event (a different id) reaches them. Survives cancel so stale
        in-flight results after a reset are dropped, not shown.
        """
        return request_id in self._member_ids

    def cancel(self) -> None:
        """Drop the batch (Lab reset). In-flight results are discarded."""
        self._request = None
        self._symbols = []
        self._index = 0
        self._current_id = None
        self._results = {}
        self._errors = {}

    # ── driving ───────────────────────────────────────────────────

    def start(self, symbols: tuple[str, ...], request: BatchRequest) -> str:
        """Begin a batch; publishes the first RunBacktest. Returns its id."""
        self.cancel()
        self._member_ids = set()
        self._request = request
        self._symbols = list(symbols)
        self._index = 0
        self._results = {}
        self._errors = {}
        self._batch_id += 1
        self.batch_started.emit(tuple(self._symbols))
        return self._publish_next()

    def _publish_next(self) -> str:
        assert self._request is not None
        symbol = self._symbols[self._index]
        self._current_id = f"msb{self._batch_id}-{self._index}"
        self._member_ids.add(self._current_id)
        self._bus.publish(
            RunBacktest(
                request_id=self._current_id,
                strategy_ids=(self._request.strategy_id,),
                symbol=symbol,
                timeframe=self._request.timeframe,
                start_date=self._request.start_date,
                end_date=self._request.end_date,
                initial_capital=self._request.initial_capital,
                slippage_pct=self._request.slippage_pct,
                commission_pct=self._request.commission_pct,
            )
        )
        return self._current_id

    def on_completed(self, event: BacktestCompleted) -> None:
        """Bus handler: record this symbol's result and advance the batch."""
        if not self.active or not self.owns(event.request_id):
            return
        result = getattr(event, "result", None)
        per_strategy = list(getattr(result, "results", ()) or ())
        symbol = self._symbols[self._index]
        if per_strategy:
            self._results[symbol] = per_strategy[0]
        else:
            detail = getattr(result, "error_detail", None) or "no bars in range"
            self._errors[symbol] = str(detail)
        self._advance()

    def on_failed(self, event: BacktestFailed) -> None:
        """Bus handler: record the failure and advance the batch."""
        if not self.active or not self.owns(event.request_id):
            return
        symbol = self._symbols[self._index]
        self._errors[symbol] = str(event.reason or "backtest error")
        self._advance()

    def _advance(self) -> None:
        assert self._request is not None
        self._index += 1
        if self._index < len(self._symbols):
            self._publish_next()
            return
        request = self._request
        symbols = tuple(self._symbols)
        results = [self._results[s] for s in symbols if s in self._results]
        errors = dict(self._errors)
        self.cancel()
        if not results:
            reason = "; ".join(f"{s}: {e}" for s, e in errors.items()) or "no results"
            self.batch_failed.emit(reason)
            self._bus.publish(
                BacktestFailed(request_id=f"msb{self._batch_id}-final", reason=reason)
            )
            return
        merged, bars_by_symbol = merge_results(results, request)
        outcome = BatchOutcome(
            symbols=symbols,
            results=tuple(results),
            errors=errors,
            merged=merged,
            bars_by_symbol=bars_by_symbol,
        )
        self.batch_finished.emit(outcome)
        self._bus.publish(
            BacktestCompleted(
                request_id=f"msb{self._batch_id}-final",
                result=BacktestResult(results=(merged,)),
            )
        )
        with contextlib.suppress(Exception):
            logger.info(
                "multi-symbol batch done: %d/%d symbols, %d trades",
                len(results),
                len(symbols),
                len(merged.trades),
            )
