# Market — Godown

## 1. Ye kya hai?

`02_market/market/` — candle data ka **godown**. Data yahan store hota hai, yahin se nikalta hai.

## 2. Andar Kya Hai — Layers

```
database   (Godown ka darwaza — SQL)
    ↓
repository (Godown ka munshi — rows → Bar)
    ↓
loader     (Munshi jo order sunta hai — LoadSymbol → DataLoaded)
```

| Layer | Kaam |
|---|---|
| `models` | `Bar` — ek candle ka shape (frozen dataclass) |
| `database` | `SqliteCandleDatabase` — raw SQLite, parameterized queries |
| `repository` | `CandleRepository` — SQL rows ko `Bar` mein badalta hai |
| `loader` | `MarketDataLoader` — event sunta hai, event bhejta hai |
| `events` | `LoadSymbol` (request), `DataLoaded` (result) |

## 3. Story

Socho ek godown hai jahan saalon ka data pada hai (SQLite file).

- **Manager** ne post office se kaha: *"SPY ka data do"* → `LoadSymbol`
- **Munshi** (loader) ne suna
- Munshi **darwazedar** (database) se bola: *"SPY ke 1200 saamaan nikaalo"*
- Darwazedar ne nikala — rows
- **Munshi** (repository) ne har row ko `Bar` ke dibbe mein rakha
- Munshi ne post office se bheja: *"Le lo"* → `DataLoaded`

```
LoadSymbol
    ↓
SqliteCandleDatabase.fetch_candles
    ↓
CandleRepository.get_candles
    ↓
DataLoaded
```

## 4. Database Contract — `data/vayren.db`

Table ka naam `candles`:

```sql
CREATE TABLE candles (
    symbol    TEXT NOT NULL,
    timestamp TEXT NOT NULL,   -- ISO-8601, jaise 2026-08-06T14:30:00
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    INTEGER NOT NULL
);
CREATE INDEX idx_candles_symbol_timestamp ON candles (symbol, timestamp);
```

| Rule | Detail |
|---|---|
| File miss | `connect()` → `FileNotFoundError` |
| Table miss | `connect()` → `RuntimeError` (no 'candles' table) |
| Order | Sabse recent `limit` rows, ascending |
| `data/` | Gitignored — DB kabhi commit nahi hota |

## 5. Example

```python
database = SqliteCandleDatabase("data/vayren.db")
database.connect()
repository = CandleRepository(database)
bars = repository.get_candles("SPY", 500)   # [Bar, Bar, ...]
```

## 6. Ye Kya Nahi Karega

| ❌ Nahi karega | Kyu |
|---|---|
| Chart/drawing | Painter `03_chart` mein hai |
| Events kisi aur se subscribe | Loader sirf apna `on_load_symbol` expose karta hai |
| Database ko seedha call | Bahar wale `market.database` import nahi karte |
| Fail hone par event bhejna | Fail → log, kuch nahi bheja jata |

## 7. Future

`04_indicator` aayega → indicators ka data yahin se aayega (`DataLoaded` wahi rahega).

> Godown ka kaam: data do, sahi do, event se do.
