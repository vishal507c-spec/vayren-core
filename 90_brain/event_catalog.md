# Event Catalog — Post Office Ki Register

## 1. Ye kya hai?

Ye **saare events ki list** hai jo platform mein chalte hain. Sabse pakka document — yahan jo likha hai, wahi hota hai.

Phase 5C mein **10 events** hain. Phase 5N mein **11** hua. Upar-se-neeche:

```
AppStarted → ListSymbols → SymbolsListed → (sidebar) → QuotesLoaded → (watchlist rows)
click → LoadSymbol → TimeframesListed → (timeframe list)
select timeframe → TimeframeChanged → DataLoaded → ChartReady → WindowRendered
click → LoadSymbol → DataLoaded → ChartReady → WindowRendered
```

## 2. Table — 11 Events

| # | Event | Kaun bhejta hai | Kaun sunta hai | Andar kya hota hai |
|---|---|---|---|---|
| 1 | `AppStarted` | `Bootstrap.start()` | `AppLifecycle.on_app_started` | — |
| 2 | `ListSymbols` | `AppLifecycle` | `SymbolListLoader.on_list_symbols` | — |
| 3 | `SymbolsListed` | `SymbolListLoader` | `ChartWindow.on_symbols_listed` → `QuoteLoader.on_symbols_listed` (isi order mein subscribe — watchlist pehle) | `symbols: tuple[str, ...]` |
| 4 | `QuotesLoaded` | `QuoteLoader` | `ChartWindow.on_quotes_loaded` | `quotes: tuple[SymbolQuote, ...]` (latest close, change %, timestamp — real SQLite) |
| 5 | `LoadSymbol` | `ChartWindow` (sidebar click) | `MarketDataLoader.on_load_symbol` | `symbol: str`, `limit: int | None` (None = poori history) |
| 6 | `ListTimeframes` | UI (symbol select) | `TimeframeListLoader.on_list_timeframes` | `symbol: str` |
| 7 | `TimeframesListed` | `TimeframeListLoader` | (selector UI) | `symbol: str`, `timeframes: tuple[str, ...]` |
| 8 | `TimeframeChanged` | UI (timeframe select) | `MarketDataLoader.on_timeframe_changed` | `symbol: str`, `timeframe: str`, `limit: int | None` (None = poori history) |
| 9 | `DataLoaded` | `MarketDataLoader` | `ChartEngine.on_data_loaded` | `symbol: str`, `bars: tuple[Bar, ...]` |
| 10 | `ChartReady` | `ChartEngine` | `ChartWindow.on_chart_ready` | `model: ChartModel` |
| 11 | `WindowRendered` | `ChartWindow` | `AppLifecycle.on_window_rendered` | — |

## 3. Flow Diagram

```
AppStarted
    ↓
ListSymbols          ← "kaunse stocks hain?"
    ↓
SymbolsListed        ← "527 stocks — sidebar bharo"
    ↓
QuotesLoaded         ← "527 latest quotes — watchlist rows bharo" (ek baar, phir cached)
    ↓
[user sidebar mein click karta hai]
    ↓
LoadSymbol           ← "AMBUJACEM ka data do"
    ↓
[TimeframesListed]   ← "AMBUJACEM ke available timeframes"
    ↓
[user timeframe select karta hai — 30m]
    ↓
TimeframeChanged     ← "AMBUJACEM 30m ka data do"
    ↓
DataLoaded           ← "AMBUJACEM ke 30m candles"
    ↓
ChartReady           ← "chart banne ke liye taiyar"
    ↓
WindowRendered       ← "window khul gayi, sab dikh raha hai"
```

## 4. Kaun Kaunsi Cheez Kaunsi Module Ki Hai

| Module | Events jo uski hain |
|---|---|
| `01_core/events` | `Event` (base), `AppStarted` |
| `02_market/events` | `ListSymbols` (request), `SymbolsListed` (result), `QuotesLoaded` (result), `LoadSymbol` (request), `DataLoaded` (result), `TimeframeChanged` (request), `ListTimeframes` (request), `TimeframesListed` (result) |
| `03_chart/events` | `ChartReady` (result), `WindowRendered` (result) |

## 5. Rules — Events Ke

| Rule | Matlab |
|---|---|
| Ek type ka event | Har event frozen dataclass hai, `Event` base se bana hai |
| Exact type dispatch | Bus `type(event)` se milata hai — exact match |
| Requests alag hain | `LoadSymbol`, `TimeframeChanged`, `ListTimeframes`, `ListSymbols` **requests** hain (commands). Baaki sab past-tense facts hain |
| Naya event = naya module | Event sirf naya phase aane par add hote hain |
| No UI/DB inside events | Event mein kabhi window, connection, repository nahi hota |
| `limit: int \| None = None` | Phase 5C se `LoadSymbol`/`TimeframeChanged` ka `limit` default `None` hai — `None` = asli SQLite ki **poori available history** (2016→2026), explicit `--limit N` sirf tab cap karta hai. DB layer `LIMIT ?` NULL never bind karta (SQLite `datatype mismatch` deta hai) — `limit is None` pe ascending full scan |
| Catalog update zaroori | Event badla → catalog + development_log update |

## 6. Story

Socho ek post office hai.

Har message ka apna envelope hai. Envelope par naam likha hai — `DataLoaded`.

Post office (EventBus) sirf naam milata hai → us naam ke jisne subscribe kiya hai, message pahunchta hai.

Agar kisi ne subscribe nahi kiya → message kisi ko nahi jata. Koi problem nahi.

Agar sunne wala fail ho gaya → post office log karta hai, aage ka kaam chalta rahta hai.

## 7. Example — Event Ka Shape

```python
@dataclass(frozen=True)
class DataLoaded(Event):
    symbol: str
    bars: tuple[Bar, ...]


@dataclass(frozen=True)
class TimeframeChanged(Event):
    symbol: str
    timeframe: str
    limit: int | None = None
```

- Frozen = badla nahi ja sakta
- `tuple` = order pakka
- `Bar` = market ka model (event apna model nahi banata)

## 8. Ye Kya Nahi Karega

- Phase 5N mein **11 events** hain — `QuotesLoaded` naya aaya (result; `SymbolsListed` ke turant baad, ek baar universe load par — `QuoteLoader` identical universe par no-op karta hai, kabhi re-query nahi)
- Events business logic nahi rakhte — sirf data
- `LoadSymbol`, `TimeframeChanged`, `ListTimeframes`, `ListSymbols` requests hain (commands); baaki sab past-tense facts

## 9. Future

`04_indicator` aayega → `IndicatorCalculated` jaisa event catalog mein add hoga. Wahi pattern.

> 11 events, 11 messages, 1 post office. Catalog padho — sab clear.
