# AI Memory — Abhi Kya State Hai

**Last update:** 2026-08-13 (Extreme responsiveness pass — measured bottlenecks, Phase 5L)

## 1. Ye kya hai?

Ye document AI ko batata hai ki **abhi platform kahan hai** — kya bana, kya baaki. Naya session shuru karte hi ye padho.

## 2. Cheezein Kahaan Hain

| Module | Chapter | Andar kya hai |
|---|---|---|
| app | `00_app/app/` | `App` (`--data-dir`, `--limit` default **None**), `Bootstrap` (`limit=None`), `AppLifecycle`, `__main__.py` |
| core | `01_core/core/` | `EventBus`, `Event`, `AppStarted`, logger, `Registry` |
| market | `02_market/market/` | `Bar`, `SqliteCandleDatabase` + `OhlcvCandleDatabase`, `CandleRepository` + `SymbolRepository`, `MarketDataLoader` + `SymbolListLoader` + `TimeframeListLoader`, `timeframe/` package (ladder + aggregation), events: `LoadSymbol`, `ListSymbols`, `DataLoaded`, `SymbolsListed`, `TimeframeChanged`, `ListTimeframes`, `TimeframesListed` |
| chart | `03_chart/chart/` | `ChartModel` (+ `timeframe`, `exchange`), `CrosshairValue`, `ChartEngine`, `CandleRenderer` + `TimeAxisRenderer` + `CrosshairRenderer` + `LabelRenderer` + `OverlayRenderer`, `CandleChartWidget` (axis strip + snapping crosshair + overlays), `WatchlistWidget` (+ inner `SymbolListWidget` — styled rows), `OptionsPanel` (◉ ◇ placeholders), `ChartWindow`, `ChartReady`, `WindowRendered` |
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
| Initial viewport | Fresh lifecycle (first open / naya symbol / timeframe change) → `_initial_count(total)` = `max(MIN_VISIBLE_BARS, min(INITIAL_BARS, total))` — **latest 150 bars** right margin pe, poori history loaded rehti hai (`_first` > 0, pan/zoom-out se purane candles milte hain). `_fit_all_count` (fit-whole-history) **hata diya** (Phase 5F fix). Same symbol+timeframe reload → incremental follow-latest re-anchor / manual-pan shift (waisa hi). `reset_view` bhi latest `INITIAL_BARS`. `_log_data_range` har load pe first/last ts log karta hai |
| TimeframeChanged flow | UI → `TimeframeChanged` → `MarketDataLoader.on_timeframe_changed` → aggregation → `DataLoaded` → chart update (chart bina touch) |
| Timeframe persistence (Phase 5J) | Selected timeframe **chart/session ka state hai, stock ka nahi** — symbol switch par kabhi reset nahi hota. `ChartWindow._current_timeframe` single source of truth: `on_chart_ready` → `model.timeframe`, `_on_timeframe_selected` → explicit selection, initial `None`. Symbol click: `_current_timeframe` hai → `TimeframeChanged(symbol, timeframe, limit)` (existing aggregation path); nahi hai (pehli load) → `LoadSymbol` (base bars). Har case mein `ListTimeframes` bhi. `ChartEngine`/loader/repository untouched — at-or-below-base timeframe repository ka existing fallback (plain fetch) handle karta hai |
| ChartModel | `timeframe`/`exchange` fields (Phase 5A) — engine infer/default karta hai; market ka aggregation bar_size isse override nahi karta |
| Axis layout | `CandleChartWidget._chart_rects()` → `(chart, volume, axis)`; `TIME_AXIS_HEIGHT = 24` |
| Window layout | `ChartWindow` → QSplitter 3 items: **watchlist (0) | options (1) | container (2)**; container QVBoxLayout: toolbar (stretch 0) + chart (stretch 1). Options = `OptionsPanel` (`OPTIONS_WIDTH=56` fixed, stretch 0) — splitter handles = clean separators; `setSizes([220, 56, 1004])`. `ChartWindow(widget, watchlist, options, toolbar, bus, limit=None)` |
| UI theme (Phase 5K) | `chart/theme.py` ka `APP_STYLE` — single palette-only QSS, `ChartWindow.__init__` mein `setStyleSheet(APP_STYLE)`. 4px radius, buttons transparent + hover `palette(midlight)`, checked/pressed `palette(mid)`, **active/checked = `palette(highlight)` pill** (timeframe buttons, selected menu item), framed rounded QMenu, hairline `QSplitter::handle` (bg `palette(mid)`). `ChartWindow` splitter `setHandleWidth(1)`. Watchlist row styling **`SymbolListWidget` ke apne QSS mein rehta hai** (widget-level sheet ancestor ko override karta hai): separators `palette(midlight)`, hover `palette(alternate-base)`, selection rounded highlight, slim 8px scrollbar. Chart header: backdrop band `OverlayRenderer.HEADER_BAND` (16,20,24,110) widget mein cached brush se; OHLC change segment `CandleRenderer.BULL/BEAR` color (`LabelRenderer.paint_left` ka optional `text_color`); "Symbol" label muted `palette(placeholder-text)`. Koi hardcoded QSS color nahi — sirf palette roles |
| Chart painting (Phase 5L) | **Static-layer pixmap cache**: `_static_pixmap` = grid + candles + axis ek widget-sized QPixmap (key: model id, first, last, price range, volume_max, size); `paintEvent` = blit + live header + crosshair overlays → crosshair/hover frames kabhi bars nahi scan karte (~0.93 ms/frame vs 10 ms pehle). `_window_stats()` single-pass (low/high/volume_max) cached per (model, window, price_manual) — `_price_range` auto-fill bhi cache se. Cache invalidation: model/window/price/size key mein hain; `set_model`/`reset_view` explicit clear. **Grid-cache tests ke contracts preserve**: `paint_grid` sirf cache rebuild par, header (symbol/OHLC) har paint par LIVE (static mein bake nahi — tests isko pin karte hain). Pan/zoom frames static rebuild karte hain (~1.6 ms) |
| Timeframe aggregation (Phase 5L) | `aggregate_bars`: **`strptime` band** (locale lookup per call = 2.1s/58k rows!) → `datetime.fromisoformat`; buckets single-pass running max/min/sum accumulators (pehle per-bucket generator re-scans). 1D limit=500: 1849 → 542 ms; 1W full: 2316 → 633 ms. Baaki cost = SQLite fetch 48k rows (~170 ms) + Python agg loop (~160 ms) ka honest floor — SQL-side aggregation (GROUP BY) = DB architecture change, out of scope. `infer_timeframe` ab sampled median gap (`_INFERENCE_SAMPLE=2048`) — 60k-bar load par 102 ms → ~1 ms. `ChartEngine` `_ascending` check: sorted bars par sort skip (85 → 14 ms). `WatchlistWidget.set_symbols`: identical set = no-op + batched `addItems`/updates off → symbol clicks par 7 ms/527 items → 0.002 ms. **Real data scale**: `D:\ZerodhaTradingData` 527 stocks, ~60k rows/stock, 15m base |
| Crosshair | `CrosshairRenderer` + snap to candle (`CrosshairValue`) + crosshair labels. **Permanent header** (Phase 5I fix): `_paint_header` har `paintEvent` mein top bar paint karta hai — `SYMBOL • timeframe • exchange` (`paint_symbol_info`) + **latest bar** ka `paint_ohlc` (`model.bars[-1]`, O/H/L/C + change + %, change = close−open, pct = `bar.return_pct` — real data, koi fake nahi). Crosshair se **bilkul independent** — visibility kabhi crosshair par depend nahi karti; model change (symbol/timeframe) par automatic update (paint model se derive hota hai). Crosshair overlays ab sirf: right price label (crosshair Y) + bottom time label (crosshair X, timeframe-aware format: intraday `Tue 04 Aug '26\n13:00`, daily date-only, weekly `Week 32\n2026`, monthly `Aug 2026`). Top bar crosshair dirty-rect se bahar |
| Free pan (Phase 5E) | `CandleChartWidget` drag ab **2D pan** karta hai — horizontal (time window via `_first`/`_last`) + vertical (price range, span/zoom unchanged via `_price_manual`). Pointer `grabMouse()`/`releaseMouse()` = capture. 2-finger touch ab vertical bhi pan karta hai. Zoom kabhi nahi badalta — sirf viewport position |
| Context menu + Alt+R (Phase 5F) | `CandleChartWidget` right-click par **single-action QMenu** (`↩ Reset chart view`, shortcut `Alt+R`) — ek hi `QAction` widget par `addAction` (WindowShortcut → poore window mein `Alt+R` chalta hai). Dono paths → `reset_view()`: viewport-only fresh-chart restore (`_initial_count` + `_anchor_first` + `_price_manual=None` + follow-latest) — data/symbol/timeframe kabhi nahi chhute. `contextMenuEvent` suppress karta hai default menu. Menu Qt se band hota hai (selection/Escape/click-outside) |
| Watchlist panel (Phase 5H) | `WatchlistWidget` — pure UI, `SymbolListWidget` ko compose karta hai. Row 1: `Watchlist ▼` selector (InstantPopup menu = watchlist names, active checked) + `+` (naya khali watchlist `Watchlist N`, uspe switch) + `↩` (reset chart view → `reset_requested` → `ChartWindow` → existing `widget.reset_view()`) + `⋯` (menu: New watchlist / Remove watchlist; All Stocks remove disabled). Row 2: `Symbol` label + `Sort by ▼` (Name A→Z / Z→A — real sorting). Row 3: stock list. Watchlists session-local in-memory `dict[name → symbols]`, seeded `"All Stocks"` = asli symbol universe (`SymbolsListed`). Signals: `symbol_selected(str)`, `reset_requested()`. Properties: `watchlists`, `active_watchlist`, `symbols`, `current_symbol` |

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
| Load-all bug fix | ✅ Done (Phase 5C) — data poori load hota hai (2016→2026, 61k+ rows), `limit: int \| None = None` end-to-end; Phase 5F fix ke baad initial viewport latest `INITIAL_BARS` dikhata hai (fit-whole-dataset hata diya). App default `--limit` None; explicit cap abhi bhi kaam karta hai |
| Timeframe selector UI | Phase 5B ne market side ready kiya (`TimeframeChanged`/`ListTimeframes` events + detection) — ab UI (selector dropdown/combo) aana baaki hai, wo `ListTimeframes` publish karke list le aur `TimeframeChanged` publish kare |
| Chart par daily/weekly bars ka visual QA | `1D`/`1W` bars midnight timestamp pe — desktop par axis labels check karo |
  | Crosshair tooltip/OHLC readout | ✅ Done (Phase 5A + bugfix) — crosshair snaps to nearest candle; bottom time label centered at crosshair X (timeframe-aware format: intraday date+time, daily date-only, weekly Week N/year, monthly Month Year), right price label centered at crosshair Y, symbol info + OHLC single-line in top bar. Time label multi-line bug fixed |
| Zoom/pan/resize ka visual QA | Live boot verified — asli desktop par haath se chart interactions check karo |
| `data/vayren.db` + `seed_sample_db.py` | Ab unused — Phase 1 legacy. Hata sakte hain jab chahe (tests independent hain) |
| Watchlist panel | ✅ Done (Phase 5H + rebuild) — `WatchlistWidget`: header `Watchlist ▼ + ↩ ⋯` + separators + `Symbol / Sort by ▼` row + styled stock list. Rows sirf symbol dikhate hain (**no fake data** — price/company/change/logo app mein exist nahi karte, kabhi invent nahi karna; row layout future fields ke liye design-ready). Filter row [S][G][T] / bottom bar [Grid][Edit][⋯] — koi existing controls nahi → banaye nahi. Sirf list scroll karta hai (header/sort fixed) |
| Options section | ✅ Done — `OptionsPanel` watchlist ke right (watchlist \| options \| chart), 2 placeholder buttons (◉, ◇) vertically — **disabled** (click impossible), no connections/menu/popup. Indicators/drawing tooling yahan aayega (roadmap dekho) |
| 527 stocks ka UX | Watchlist ab header row ke saath hai — search/filter aur watchlist persistence aage ke phase (roadmap dekho) |

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
