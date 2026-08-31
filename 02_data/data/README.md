# Data — Historical Download (02_data)

`02_data/data/` — **write** path. `market` read karta hai, `data` download karke per-symbol `SYMBOL.db` mein likhta hai.

## Capabilities (`manifest.py`)
| Capability | Kaam |
|---|---|
| `historical_data.download` | symbol/interval/date range download |
| `historical_data.coverage` | DB coverage scan (state, gaps, rows) |
| `historical_data.status` | provider readiness + engine status |

## Events
Requests: `DownloadRequest`, `CoverageRequest`, `CancelDownload` → Facts: `DownloadStarted`, `DownloadProgress`, `DownloadCompleted`, `DownloadFailed`, `DownloadCoverage`

## Engine (MODE 1 — download only)
- `HistoricalDownloadEngine` — single-threaded, single instance.
- Storage: `<data_dir>/SYMBOL.db` — `ohlcv(candle_time TEXT PK, OHLC REAL, volume INTEGER)` + `non_trading` + `history_boundaries` (same as market). `INSERT OR IGNORE`, WAL, idempotent, resume.
- No fabrication, no audit/repair.
- Market hours 09:15–12:40 IST block, NSE holidays respected, lock+heartbeat prevents concurrent runs.
- Credentials: `VAYREN_ZERODHA_*` env at auth time + `ProviderCredentialsManager` (Windows Credential Manager / file fallback); never in events/logs/UI.

## Wiring
`DownloadWorker(QThread)` owns engine → Qt signals → `Bootstrap` bridges to `EventBus`. UI `data/ui/HistoricalDownloadPanel` (embedded side panel, not window) sirf bus se baat karta hai.

## Import
`data` → `core` only.
