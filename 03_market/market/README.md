# Market — Godown (03_market)

`03_market/market/` — candle data ka **read** godown: database→repository→loader.

## Layers
| Layer | Kaam |
|---|---|
| `models` | `Bar`, `SymbolQuote` (frozen) |
| `database` | `SqliteCandleDatabase` / `OhlcvCandleDatabase` — raw SQLite |
| `repository` | `CandleRepository` / `SymbolRepository` — rows → `Bar`, timeframe aggregation, `get_quotes` |
| `loader` | `MarketDataLoader`, `SymbolListLoader`, `TimeframeListLoader`, `QuoteLoader` — event in → event out |
| `timeframe` | `TIMEFRAME_LADDER`, `aggregate_bars` (session-anchored) |
| `events` | `LoadSymbol`, `DataLoaded`, `ListSymbols`, `SymbolsListed`, `QuotesLoaded`, `TimeframeChanged` … (catalog dekho) |

## Database Contract — Asli Data
Per-symbol file `<data_dir>/SYMBOL.db` (e.g. `D:\ZerodhaTradingData\AMBUJACEM.db`):
```sql
CREATE TABLE ohlcv (
    candle_time TEXT PRIMARY KEY,  -- 2026-01-05 09:15:00
    open  REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume INTEGER NOT NULL
);
```
`candles(symbol)` legacy table sirf `SqliteCandleDatabase` tests mein. `LIMIT NULL` bind kabhi nahi (SQLite error) — `None` = full scan ascending.

## Example
```python
repo = SymbolRepository(data_dir)
bars = repo.get_candles("AMBUJACEM", limit=None)  # full history
quotes = repo.get_quotes(("AMBUJACEM","BPCL"))
```

## Kya Nahi Karega
Chart/drawing (`04_chart`), direct DB import bahar se, fail par event nahi — sirf log.
