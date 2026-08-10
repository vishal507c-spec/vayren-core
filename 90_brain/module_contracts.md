# Module Contracts — Har Module Ka Pakka Contract

## 1. Ye kya hai?

Jab do modules baat karte hain, toh kya bol sakte hain — ye wahi list hai.

**Rule:** Cross-module import sirf yahan likhe public names se hota hai. Koi module dusre module ke andar ki cheez import nahi karta.

```
03_chart/chart/ ──imports──► market (sirf public API: Bar, DataLoaded)
                     └──────► core (sirf public API: EventBus, events)
```

## 2. 00_app — `app` (Manager)

| Public cheez | Kaam |
|---|---|
| `App.main(argv)` | Entry point: args → logging → QApplication → bootstrap → Qt loop |
| `Bootstrap(data_dir, limit=None)` | Bus + services + registry banata hai; **subscriptions sirf yahan**; `start()` = window show + `AppStarted`. `limit=None` = asli DB ki poori history load hoti hai |
| `AppLifecycle(bus)` | `on_app_started` → `ListSymbols`; `on_window_rendered` = terminal |

Entry: `python -m app --data-dir D:\ZerodhaTradingData` ya `vayren` command. Env override: `VAYREN_DATA_DIR`.

## 3. 01_core — `core` (Post Office)

| Public cheez | Kaam |
|---|---|
| `EventBus` | `subscribe(type, handler)`, `unsubscribe`, `publish`, `clear`. Handler fail ho → log, app chalta rahe |
| `Event` | Sab events ka base class |
| `AppStarted` | Startup fact |
| `configure_logging(level, log_file)` / `get_logger(name)` | Logging setup |
| `Registry[T]` | `register/get/list/__contains__/__len__/__iter__` — services yahan register hote hain |

## 4. 02_market — `market` (Godown)

| Public cheez | Kaam |
|---|---|
| `Bar` | Frozen dataclass: `symbol, open, high, low, close, volume, timestamp` (ISO string) + optional `bar_size, vwap, trades, source` |
| `SqliteCandleDatabase(path)` | Sample-schema DB: `connect()` (file + table check), `fetch_candles(symbol, limit: int \| None)` → sabse recent rows ascending, `limit=None` = poora table ascending, `close()` |
| `OhlcvCandleDatabase(path)` | **Asli Zerodha schema**: table `ohlcv`, column `candle_time`. Same interface — `CandleRepository` dono se kaam karta hai. `fetch_candles(symbol, limit: int \| None)` — `None` = ascending full scan (**SQLite `LIMIT ?` NULL bind kabhi mat karo** — `datatype mismatch` error deta hai) |
| `CandleRepository(database)` | `get_candles(symbol, limit: int \| None)` → `list[Bar]` — row → Bar mapping ka sirf ek jagah; `None` = full history. Phase 5B: `detect_bar_duration(symbol)` (rows ka dominant bar size), `detect_timeframes(symbol)` → `tuple[str, ...]`, `get_candles_timeframe(symbol, timeframe, limit: int \| None)` → aggregated bars (`None` = saari rows aggregate + full list) |
| `SymbolRepository(data_dir)` | `list_symbols()` → sorted tuple; `get_candles(symbol, limit: int \| None)` → per-stock file kholkar `CandleRepository` se load; Phase 5B: `detect_timeframes(symbol)` + `get_candles_timeframe(symbol, timeframe, limit: int \| None)` (same file-open pattern) |
| `MarketDataLoader(repository, bus)` | `on_load_symbol` → repository → `DataLoaded`; `on_timeframe_changed` → `get_candles_timeframe` → `DataLoaded`; fail → log, kuch nahi bhejta |
| `SymbolListLoader(repository, bus)` | `on_list_symbols` → scan → `SymbolsListed`; fail → log, kuch nahi bhejta |
| `TimeframeListLoader(repository, bus)` | `on_list_timeframes` → `detect_timeframes` → `TimeframesListed`; fail → log, kuch nahi bhejta. Hamesha SQLite se detect — koi cached list nahi |
| `TIMEFRAME_LADDER` | Canonical candidate labels: `("1m","3m","5m","15m","30m","45m","1h","2h","4h","1D","1W")` |
| `timeframe_seconds(name)` / `timeframe_name(seconds)` | Ladder (aur generated `90m`/`2D`/`2W`) ↔ seconds conversion |
| `available_timeframes(base_seconds)` | Ladder entries jo `base` ke whole multiple hain + base itself (generated label agar ladder mein nahi) — **existence hamesha DB se** |
| `LoadSymbol`, `ListSymbols`, `DataLoaded`, `SymbolsListed`, `TimeframeChanged`, `ListTimeframes`, `TimeframesListed` | Events (catalog dekho) |

### Timeframe Aggregation (Phase 5B)

- **Detection**: base duration = mode of bar-to-bar deltas (`detect_bar_duration`). Kabhi hardcode nahi; `available_timeframes` pure function hai — caller (`CandleRepository.detect_timeframes`) hamesha SQLite se rows leke chalta hai.
- **Bucket math** (`market/timeframe/aggregate.py`): intraday = session-anchored (session start = **mode of first-bar-per-day**, partial windows se robust) — NSE 09:15 alignment; daily = local midnight; weekly = Monday midnight.
- **OHLCV aggregation**: only real rows — open = first, high = max, low = min, close = last, volume = sum. Zero fabrication.
- **Query pattern**: sirf timeframe change par — 2 queries (detection sample 5000 + windowed fetch `(limit+1)×ratio`), oldest partial bucket dropped via `bars[-limit:]`. `limit=None` pe windowed fetch ke bajaye full sample + full fetch (koi drop nahi)
- `Bar.bar_size` aggregated bars pe set hota hai (e.g. `"30m"`); base bars unchanged. Chart `DataLoaded` se update hota hai — chart kuch nahi sunta naya.

### Database Contract — Asli Data (Phase 2)

`D:\ZerodhaTradingData` — **har stock ka apna SQLite file**. Symbol = file name.

```sql
CREATE TABLE ohlcv (
    candle_time TEXT PRIMARY KEY,  -- jaise 2026-01-05 09:15:00
    open  REAL NOT NULL,
    high  REAL NOT NULL,
    low   REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL
);
```

> Purana sample schema (`candles` table + `symbol` column) ab sirf `SqliteCandleDatabase` ke tests mein hai. App asli data directory se chalta hai.

## 5. 03_chart — `chart` (Painter)

| Public cheez | Kaam |
|---|---|
| `ChartModel` | Frozen: `symbol: str`, `bars: tuple[Bar, ...]` (timestamp ascending) |
| `ChartEngine(bus)` | `on_data_loaded`: sort karo → empty check → `ChartReady` |
| `CandleRenderer` | Stateless QPainter: grid, price labels, wicks, bodies, volume. No state, no data logic |
| `TimeAxisRenderer` | Stateless QPainter (X-axis): `select_step(avg_seconds, slot_px)` (min 96px spacing, calendar ladder), `format_for_step(step)` (10:15 → 10 Apr → Apr → Apr 2024 → 2024), `tick_times(first, last, step)` (calendar-aligned), `paint(painter, bars, first, last, axis_rect)` — crisp gridlines, antialiased labels, never overlap. No state, no events |
| `CrosshairRenderer` | Stateless QPainter: `paint(painter, position, plot_rect)` — vertical (full height) + horizontal (full width) lines through mouse, RGBA(180,180,180,120) 1px, cached pen (zero paint-time allocation). No state, no events |
| `CandleChartWidget(QWidget)` | `set_model(model)`; wheel = zoom, drag = pan, mouse move = crosshair (`_crosshair_pos`), leave = hide, resize = repaint. No bus, no SQL, no loading. **Initial viewport**: naya symbol → `_fit_all_count(total)` — poore dataset pe fit (first bar index 0, latest bar right margin pe); same-symbol reload → follow-latest (`INITIAL_BARS` trailing window)` |
| `SymbolListWidget(QListWidget)` | Sidebar: `set_symbols(tuple)`, `select_symbol(str)`, Qt signal `symbol_selected`. No bus — pure UI |
| `ChartWindow(QMainWindow)` | Splitter host (sidebar + chart); `on_symbols_listed` → sidebar; `on_chart_ready` → model + title `VAYREN — SYMBOL` + `WindowRendered`; click → `LoadSymbol`. `__init__(bus, data_dir, limit=None)` — `limit=None` = poori history |
| `ChartReady` / `WindowRendered` | Events (catalog dekho) |

## 6. 99_archive — Retired Modules (Museum)

| Cheez | Matlab |
|---|---|
| `99_archive/01_foundation … 13_knowledge` | Purana architecture, sirf reference ke liye |
| `sys.path` pe nahi | Build packages mein nahi — import nahi ho sakta |
| Rules | Kabhi import nahi, kabhi extend nahi. Remove sirf explicit approval se |

## 7. Story

Socho do dukaanon ka market hai.

Dukaan A (market) ke paas sirf ek counter hai — counter par likha hai kaunsi cheezein bechi ja sakti hain (`Bar`, `DataLoaded`).

Dukaan B (chart) counter se hi khareedta hai. Pichhle kamre (database) mein jane ki zaroorat nahi.

Contract ye counter ki list hai.

## 8. Rules

| Rule | Matlab |
|---|---|
| Contract ka code hi sahi hai | `__init__.py` jo export karta hai, wahi public API |
| Internal kabhi bahar nahi | `market.database` ko chart import nahi karta |
| Schema badla → yahan update | Contract + development_log dono update karo |
