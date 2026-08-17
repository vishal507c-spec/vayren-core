# data â€” Historical Data Ingest Domain (02_data)

The real ingest component of VAYREN: a preserved historical OHLCV download
engine (MODE 1 â€” download only). It downloads missing candles into per-symbol
SQLite databases that the `market` domain reads.

## Capabilities (declared in `manifest.py`)

| Capability | What it does |
|---|---|
| `historical_data.download` | download a symbol/interval/date range into its candle DB |
| `historical_data.coverage` | scan a symbol's DB and report coverage (state, head/tail gaps, rows) |
| `historical_data.status` | provider readiness (SDKs + credentials) and engine status |

## Events

- Requests: `DownloadRequest`, `CoverageRequest`, `CancelDownload`
- Facts: `DownloadStarted`, `DownloadProgress`, `DownloadCompleted`,
  `DownloadFailed`, `DownloadCoverage`

## Engine

- `HistoricalDownloadEngine` â€” single instance, strictly single-threaded.
- Storage: per-symbol `SYMBOL.db` files under `data_dir` (schema identical to
  the market storage: `ohlcv` / `non_trading` / `history_boundaries`).
- Idempotent: `INSERT OR IGNORE`; state derived from the database itself, no
  progress files; resumes after interruption.
- Never fabricates candles. Never audits/repairs/verifies (MODE 1 contract).
- Market hours (09:15â€“12:40 IST) block downloads; NSE holidays honoured.
- Lock file + heartbeat guards against concurrent engine processes.
- Credentials come from environment variables (`VAYREN_ZERODHA_*`) at auth
  time; never stored in events, settings, logs or the UI.

## Wiring

- `DownloadWorker` (QThread) owns the engine and emits Qt signals carrying
  event objects; `00_app/app/bootstrap/bootstrap.py` bridges those signals to
  `EventBus.publish` and subscribes bus requests back into the worker.
- The UI lives in `data/ui/` (HistoricalDataWindow) and only ever talks to
  the bus â€” same rule as every other widget.

## Import rules

`data` may import `core` only (never `market`, `chart`, `app`).

## Tests

`data/tests/` â€” unit tests for every engine module (no network), Qt tests for
worker + UI, and the app-level integration pins updated to include
`historical_data`.
