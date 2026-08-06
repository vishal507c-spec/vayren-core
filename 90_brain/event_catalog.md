# Event Catalog — Post Office Ki Register

## 1. Ye kya hai?

Ye **saare events ki list** hai jo platform mein chalte hain. Sabse pakka document — yahan jo likha hai, wahi hota hai.

Phase 1 mein exactly **5 events** hain. Upar-se-neeche:

```
AppStarted → LoadSymbol → DataLoaded → ChartReady → WindowRendered
```

## 2. Table — 5 Events

| # | Event | Kaun bhejta hai | Kaun sunta hai | Andar kya hota hai |
|---|---|---|---|---|
| 1 | `AppStarted` | `Bootstrap.start()` | `AppLifecycle.on_app_started` | — |
| 2 | `LoadSymbol` | `AppLifecycle` | `MarketDataLoader.on_load_symbol` | `symbol: str`, `limit: int` |
| 3 | `DataLoaded` | `MarketDataLoader` | `ChartEngine.on_data_loaded` | `symbol: str`, `bars: tuple[Bar, ...]` |
| 4 | `ChartReady` | `ChartEngine` | `ChartWindow.on_chart_ready` | `model: ChartModel` |
| 5 | `WindowRendered` | `ChartWindow` | `AppLifecycle.on_window_rendered` | — |

## 3. Flow Diagram

```
AppStarted
    ↓
LoadSymbol          ← "SPY ka data do"
    ↓
DataLoaded          ← "SPY ke 1200 candles"
    ↓
ChartReady          ← "chart banne ke liye taiyar"
    ↓
WindowRendered      ← "window khul gayi, sab dikh raha hai"
```

## 4. Kaun Kaunsi Cheez Kaunsi Module Ki Hai

| Module | Events jo uski hain |
|---|---|
| `01_core/events` | `Event` (base), `AppStarted` |
| `02_market/events` | `LoadSymbol` (request), `DataLoaded` (result) |
| `03_chart/events` | `ChartReady` (result), `WindowRendered` (result) |

## 5. Rules — Events Ke

| Rule | Matlab |
|---|---|
| Ek type ka event | Har event frozen dataclass hai, `Event` base se bana hai |
| Exact type dispatch | Bus `type(event)` se milata hai — exact match |
| `LoadSymbol` alag hai | Ye ek **request** hai (command). Baaki sab past-tense facts hain |
| Naya event = naya module | Event sirf naya phase aane par add hote hain |
| No UI/DB inside events | Event mein kabhi window, connection, repository nahi hota |
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
```

- Frozen = badla nahi ja sakta
- `tuple` = order pakka
- `Bar` = market ka model (event apna model nahi banata)

## 8. Ye Kya Nahi Karega

- Koi 6th event nahi hai Phase 1 mein
- Events business logic nahi rakhte — sirf data
- `LoadSymbol` ko chhod kar, sab events past tense mein hain

## 9. Future

`04_indicator` aayega → `IndicatorCalculated` jaisa event catalog mein add hoga. Wahi pattern.

> 5 events, 5 messages, 1 post office. Catalog padho — sab clear.
