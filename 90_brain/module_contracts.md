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
| `Bootstrap(database_path, symbol, limit)` | Bus + services + registry banata hai; **subscriptions sirf yahan**; `start()` = connect + `AppStarted` |
| `AppLifecycle(bus, symbol, limit)` | `on_app_started` → `LoadSymbol`; `on_window_rendered` = terminal |

Entry: `python -m app` ya `vayren` command.

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
| `SqliteCandleDatabase(path)` | `connect()` (file + table check), `fetch_candles(symbol, limit)` → sabse recent rows ascending, `close()` |
| `CandleRepository(database)` | `get_candles(symbol, limit)` → `list[Bar]` |
| `MarketDataLoader(repository, bus)` | `on_load_symbol` → repository → `DataLoaded`; fail → log, kuch nahi bhejta |
| `LoadSymbol` / `DataLoaded` | Events (catalog dekho) |

### Database Contract — `data/vayren.db`

SQLite database, table `candles`:

```sql
CREATE TABLE candles (
    symbol    TEXT NOT NULL,
    timestamp TEXT NOT NULL,  -- ISO-8601, jaise 2026-08-06T14:30:00
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    INTEGER NOT NULL
);
CREATE INDEX idx_candles_symbol_timestamp ON candles (symbol, timestamp);
```

> Apna asli database yahan daalo: `data/vayren.db`. `data/` folder gitignore mein hai — kabhi commit nahi hota.

## 5. 03_chart — `chart` (Painter)

| Public cheez | Kaam |
|---|---|
| `ChartModel` | Frozen: `symbol: str`, `bars: tuple[Bar, ...]` (timestamp ascending) |
| `ChartEngine(bus)` | `on_data_loaded`: sort karo → empty check → `ChartReady` |
| `CandleRenderer` | Stateless QPainter: grid, price labels, wicks, bodies, volume. No state, no data logic |
| `CandleChartWidget(QWidget)` | `set_model(model)`; wheel = zoom, drag = pan, resize = repaint. No bus, no SQL, no loading |
| `ChartWindow(QMainWindow, bus)` | Widget host karta hai; `on_chart_ready` → set model → show → `WindowRendered` |
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
