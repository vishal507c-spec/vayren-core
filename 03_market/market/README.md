# Market â€” Godown

## 1. Ye kya hai?

`03_market/market/` â€” candle data ka **godown**. Data yahan store hota hai, yahin se nikalta hai.

## 2. Andar Kya Hai â€” Layers

```
database   (Godown ka darwaza â€” SQL)
    â†“
repository (Godown ka munshi â€” rows â†’ Bar)
    â†“
loader     (Munshi jo order sunta hai â€” LoadSymbol â†’ DataLoaded)
```

| Layer | Kaam |
|---|---|
| `models` | `Bar` â€” ek candle ka shape (frozen dataclass) |
| `database` | `SqliteCandleDatabase` â€” raw SQLite, parameterized queries |
| `repository` | `CandleRepository` â€” SQL rows ko `Bar` mein badalta hai |
| `loader` | `MarketDataLoader` â€” event sunta hai, event bhejta hai |
| `events` | `LoadSymbol` (request), `DataLoaded` (result) |

## 3. Story

Socho ek godown hai jahan saalon ka data pada hai (SQLite file).

- **Manager** ne post office se kaha: *"SPY ka data do"* â†’ `LoadSymbol`
- **Munshi** (loader) ne suna
- Munshi **darwazedar** (database) se bola: *"SPY ke 1200 saamaan nikaalo"*
- Darwazedar ne nikala â€” rows
- **Munshi** (repository) ne har row ko `Bar` ke dibbe mein rakha
- Munshi ne post office se bheja: *"Le lo"* â†’ `DataLoaded`

```
LoadSymbol
    â†“
SqliteCandleDatabase.fetch_candles
    â†“
CandleRepository.get_candles
    â†“
DataLoaded
```

## 4. Database Contract â€” `data/vayren.db`

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
| File miss | `connect()` â†’ `FileNotFoundError` |
| Table miss | `connect()` â†’ `RuntimeError` (no 'candles' table) |
| Order | Sabse recent `limit` rows, ascending |
| `data/` | Gitignored â€” DB kabhi commit nahi hota |

## 5. Example

```python
database = SqliteCandleDatabase("data/vayren.db")
database.connect()
repository = CandleRepository(database)
bars = repository.get_candles("SPY", 500)   # [Bar, Bar, ...]
```

## 6. Ye Kya Nahi Karega

| âŒ Nahi karega | Kyu |
|---|---|
| Chart/drawing | Painter `04_chart` mein hai |
| Events kisi aur se subscribe | Loader sirf apna `on_load_symbol` expose karta hai |
| Database ko seedha call | Bahar wale `market.database` import nahi karte |
| Fail hone par event bhejna | Fail â†’ log, kuch nahi bheja jata |

## 7. Future

`11_indicator` aayega â†’ indicators ka data yahin se aayega (`DataLoaded` wahi rahega).

> Godown ka kaam: data do, sahi do, event se do.

