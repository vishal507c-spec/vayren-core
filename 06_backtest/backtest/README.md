# backtest — Backtest Platform

Historical replay, execution simulation, position management, trade
journal and metrics — the research engine behind the Strategy Tester.

- `models/` — BacktestConfig, TradeRecord, EquityPoint, PerformanceMetrics, BacktestResult/StrategyResult
- `engine/` — replay slicing, ExecutionSimulator, PositionManager, TradeJournal, metrics computation
- `runner.py` — orchestrates replay→strategy→fills→journal→curve→metrics; plus bounded parallel multi-symbol batch (`BatchSpec`/`run_symbol_batch`, same core, deterministic merge)
- `worker.py` — QThread runner (off-UI-thread, like DownloadWorker); batch queue (`BatchEnqueued`, stock-level progress only)
- `events/` — RunBacktest (request), BacktestStarted/Progress/Completed/Failed
- `ui/` — performance panel, analytics views, chart overlay
- `validation.py` — honest request validation (no fake results)

Depends on: core, market, strategy. Backtest stays independent of UI rendering.
