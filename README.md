# Vayren — Desktop Charting Platform

**Owns:** High-level overview, getting-started. **Not owns:** Rules/architecture/contracts → `AGENTS.md`, `ARCHITECTURE_CONSTITUTION.md`, `90_brain/`.
**When to read:** First overview for humans; AI should prefer `AGENTS.md` path.

## 1. Ye kya hai?

Vayren ek **desktop charting platform** hai.

Matlab — ek program jo aapke computer par chalta hai.

Wo aapke SQLite database se candles leta hai, aur screen par **candlestick chart** dikhata hai.

## 2. Ye kyu banaya?

Bahut simple kaam hai — par wahi ek kaam, bilkul perfect:

```
App kholo
    ↓
SQLite se candles lo
    ↓
Candlestick chart dikhao
```

Bas. Iske aage kuch nahi.

Koi indicator nahi. Koi strategy nahi. Koi broker nahi.

Ye ek **long-term platform** ki neev hai — aaj sirf chart, kal module-module banate jayenge.

## 3. Iska ek kaam kya hai?

Chart dikhana. Sirf ye.

## 4. Ye kya nahi karega?

| ❌ Nahi karega | Kyu |
|---|---|
| Trading logic | Ye charting platform hai, broker nahi |
| Indicators | Future module `11_indicator` ka kaam |
| Strategies | Future module `05_strategy` ka kaam |
| Portfolio | Future module `09_portfolio` ka kaam |
| Watchlists, replay, backtest | Sab future modules ka kaam |

Rule: **naya feature = naya module**. Purane module kabhi badha nahi jate.

## 5. Story

Socho aap ek trading office chala rahe ho.

- **Manager** hai — wo sab kaam shuru karta hai. *(App)*
- **Post Office** hai — jahan sab message aate-jate hain. *(Event Bus)*
- **Godown** hai — jahan purana data pada hai. *(SQLite)*
- **Painter** hai — jo candles ki painting banata hai. *(Chart)*

Manager bola: *"SPY ka data do."* → Godown ne data diya → Painter ne chart banaya.

Sabko alag-alag bolne ki zaroorat nahi. Sab **Post Office** se baat karte hain.

## 6. Diagram

```
00_app  (Manager — sab shuru karta hai)
   ↓
01_core (Post Office — EventBus)
   ↓
02_data (Data Writer — historical candles download karta hai)
   ↓
03_market (Godown — SQLite candles)
   ↓
04_chart (Painter — chart banata hai)
```

Event flow (ab 19 events — poora catalog `90_brain/event_catalog.md` mein):

```
AppStarted → ListSymbols → SymbolsListed → QuotesLoaded → LoadSymbol → DataLoaded → ChartReady → WindowRendered
(plus TimeframeChanged, DownloadRequest/Progress/Completed … — event-driven chain)
```

Detail: `90_brain/event_catalog.md` authoritative hai.

## 7. Example

```bash
make setup
python scripts/seed_sample_db.py   # pehli baar: sample data banane ke liye
make dev
```

Aapka asli data directory bhi de sakte hain:

```bash
python -m app --data-dir D:\ZerodhaTradingData --limit 5000
# env: VAYREN_DATA_DIR=D:\ZerodhaTradingData  python -m app
```

Chart kholne ke baad:

| Action | Kaam |
|---|---|
| Mouse wheel | Zoom (cursor ke around) |
| Left-drag | Pan |
| Window resize | Chart khud adjust |

## 8. Future

Roadmap fixed hai — har feature apna alag chapter:

```
05_strategy → 06_backtest → 07_risk → 08_execution → 09_portfolio
→ 10_scanner → 11_indicator → 12_drawing → 13_replay
→ 14_workspace → 15_plugin
```

---

**Sahin jagah se shuru karna ho toh:** `90_brain/` kholo — wahan sab kuch pada hai.

> Vayren = chart dikhane wala app. Bas. Baaki sab future hai.
