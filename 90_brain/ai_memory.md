# AI Memory — Abhi Kya State Hai

**Last update:** 2026-08-10 (Release v1.1.0 — version control setup)

## 1. Ye kya hai?

Ye document AI ko batata hai ki **abhi platform kahan hai** — kya bana, kya baaki. Naya session shuru karte hi ye padho.

## 2. Cheezein Kahaan Hain

| Module | Chapter | Andar kya hai |
|---|---|---|
| app | `00_app/app/` | `App` (`--data-dir`, `--limit` default **None**), `Bootstrap` (`limit=None`), `AppLifecycle`, `__main__.py` |
| core | `01_core/core/` | `EventBus`, `Event`, `AppStarted`, logger, `Registry` |
| market | `02_market/market/` | `Bar`, `SqliteCandleDatabase` + `OhlcvCandleDatabase`, `CandleRepository` + `SymbolRepository`, `MarketDataLoader` + `SymbolListLoader` + `TimeframeListLoader`, `timeframe/` package (ladder + aggregation), events: `LoadSymbol`, `ListSymbols`, `DataLoaded`, `SymbolsListed`, `TimeframeChanged`, `ListTimeframes`, `TimeframesListed` |
| chart | `03_chart/chart/` | `ChartModel` (+ `timeframe`, `exchange`), `CrosshairValue`, `ChartEngine`, `CandleRenderer` + `TimeAxisRenderer` + `CrosshairRenderer` + `LabelRenderer` + `OverlayRenderer`, `CandleChartWidget` (axis strip + snapping crosshair + overlays), `SymbolListWidget`, `ChartWindow`, `ChartReady`, `WindowRendered` |
| brain | `90_brain/` | Permanent knowledge — code chhune se pehle padho |
| archive | `99_archive/` | Purane modules, sirf reference — kabhi import nahi |

## 3. Verified Facts (is session mein pakke kiye)

| Fact | Detail |
|---|---|
| Bus synchronous hai | Poora chain ek publish mein khatam |
| Subscriptions ek jagah | Sirf `00_app/app/bootstrap/bootstrap.py` |
| Asli data | `D:\ZerodhaTradingData` — 527 DBs, har stock ka apna file |
| Asli schema | Table `ohlcv` (`candle_time` TEXT PK, open/high/low/close REAL, volume INTEGER) — koi `symbol` column nahi |
| Asli granularity | AMBUJACEM.db = 61,601 rows @ **15m** (09:15..15:15, 25 bars/day, deltas 900s) |
| Timeframe detection | `CandleRepository.detect_timeframes` — base = mode of row deltas; `available_timeframes` = ladder entries jo base ke multiple hain. AMBUJACEM → `('15m','30m','45m','1h','2h','4h','1D','1W')` (1m/3m/5m excluded). **Kabhi cached nahi** |
| Aggregation | Session-anchored intraday (session start = mode of **first-bar-per-day** — partial windows se robust), daily = midnight, weekly = Monday. OHLCV raw rows se exact; aggregated bar ke bar_size pe `"30m"` jaisa label |
| Query pattern | Sirf timeframe change par 2 queries (sample 5000 detect + `(limit+1)×ratio` window fetch); `bars[-limit:]` partial oldest bucket drop karta hai. **`limit=None`** (default ab) = full sample + full fetch, koi drop nahi |
| Load-all semantics | `LoadSymbol.limit` / `TimeframeChanged.limit` ab `int \| None = None` — default = **poori SQLite history** (TCS 61,676 rows, 2016-06-09 → 2026-06-10). Explicit `--limit N` hi cap karta hai. DB layers (`fetch_candles`) `limit is None` pe ascending full scan — `LIMIT ?` NULL binding **kabhi nahi** (SQLite `datatype mismatch`) |
| Initial viewport | Naya symbol: `CandleChartWidget.set_model` → `_fit_all_count(total)` = `ceil((total-0.5)/(1-RIGHT_MARGIN_FRACTION))` min-bounded by `MIN_VISIBLE_BARS` — poora dataset fit, latest bar right margin pe. Same-symbol reload → trailing `INITIAL_BARS` window. `_log_data_range` har load pe first/last ts log karta hai |
| TimeframeChanged flow | UI → `TimeframeChanged` → `MarketDataLoader.on_timeframe_changed` → aggregation → `DataLoaded` → chart update (chart bina touch) |
| ChartModel | `timeframe`/`exchange` fields (Phase 5A) — engine infer/default karta hai; market ka aggregation bar_size isse override nahi karta |
| Axis layout | `CandleChartWidget._chart_rects()` → `(chart, volume, axis)`; `TIME_AXIS_HEIGHT = 24` |
| Window layout | `ChartWindow` → QSplitter (sidebar | container); container QVBoxLayout: toolbar (stretch 0) + chart (stretch 1) — toolbar min-height, chart baaki sab vertical space. Phase 5B fix (blank space above chart) |
| Crosshair | `CrosshairRenderer` + snap to candle (`CrosshairValue`) + 4 overlay labels (Phase 4+5A+bugfix): symbol/timeframe/exchange + OHLC top bar, right price at crosshair Y, bottom time centered at crosshair X — timeframe-aware format (intraday: `Tue 04 Aug '26\n13:00`, daily: date only, weekly: `Week 32\n2026`, monthly: `Aug 2026`). `LabelRenderer` supports multi-line text |

## 4. Workflow — AI Agent Ke Liye

```
1. 90_brain/*.md padho (minimum: project_rules, architecture,
   event_catalog, module_contracts, naming_conventions, coding_standards, ai_memory)
2. 99_archive sirf reference ke liye
3. Module contracts ke hisaab se implement
4. make check (lint + format + typecheck + test + validators)
5. 90_brain/development_log.md + ai_memory.md update
```

## 5. Open Items — Baaki Kaam

| Item | Status |
|---|---|
| Load-all bug fix | ✅ Done (Phase 5C) — naya symbol ab poori history dikhata hai (2016→2026, 61k+ rows), `limit: int \| None = None` end-to-end; widget initial viewport fit-whole-dataset. App default `--limit` None; explicit cap abhi bhi kaam karta hai |
| Timeframe selector UI | Phase 5B ne market side ready kiya (`TimeframeChanged`/`ListTimeframes` events + detection) — ab UI (selector dropdown/combo) aana baaki hai, wo `ListTimeframes` publish karke list le aur `TimeframeChanged` publish kare |
| Chart par daily/weekly bars ka visual QA | `1D`/`1W` bars midnight timestamp pe — desktop par axis labels check karo |
  | Crosshair tooltip/OHLC readout | ✅ Done (Phase 5A + bugfix) — crosshair snaps to nearest candle; bottom time label centered at crosshair X (timeframe-aware format: intraday date+time, daily date-only, weekly Week N/year, monthly Month Year), right price label centered at crosshair Y, symbol info + OHLC single-line in top bar. Time label multi-line bug fixed |
| Zoom/pan/resize ka visual QA | Live boot verified — asli desktop par haath se chart interactions check karo |
| `data/vayren.db` + `seed_sample_db.py` | Ab unused — Phase 1 legacy. Hata sakte hain jab chahe (tests independent hain) |
| 527 stocks ka UX | Sidebar plain list hai — search/filter aage ke phase (roadmap dekho) |

## 6. Known Oddities (Koi Problem Nahi)

| Cheez | Detail |
|---|---|
| Offscreen Qt warnings | Sirf headless mein aate hain, desktop par nahi |
| Em dash title | `VAYREN — SYMBOL` — console mein `â��` dikhe to sirf codepage; GUI sahi dikhata hai |
| Antialiasing off | Time-axis aur crosshair lines crisp rakhne ke liye shapes pe `Antialiasing=False` (text pe `TextAntialiasing=True`) — blended pixels 0 |
| Offscreen grab unreliable | Widget pe `grab()` offscreen mein crash/flaky — widget tests pixel-free (state + monkeypatch); pixel checks sirf QImage pe |
| Partial last bucket | 25 bars/day (odd) → last 30m bar sirf 15:15 ka hota hai (genuine data state, TradingView jaisa) |
| 15:15 bar | Day ka 25th bar — session-anchored buckets isse apne aap handle karte hain |

## 7. Future

Naya kaam shuru karo toh pehle `roadmap.md` dekho — kaunsa phase, kaunsa chapter.

> Brain state bataata hai: kya bana, kahan hai, kya baaki. Update karte rehna.
