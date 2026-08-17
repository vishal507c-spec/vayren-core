# Module Contracts — Har Module Ka Pakka Contract

## 1. Ye kya hai?

Jab do modules baat karte hain, toh kya bol sakte hain — ye wahi list hai.

**Rule:** Cross-module import sirf yahan likhe public names se hota hai. Koi module dusre module ke andar ki cheez import nahi karta.

```
04_chart/chart/ ──imports──► market (sirf public API: Bar, DataLoaded)
                     └──────► core (sirf public API: EventBus, events)
```

## 2. 00_app — `app` (Manager)

| Public cheez | Kaam |
|---|---|
| `App.main(argv)` | Entry point: args → logging → QApplication → bootstrap → Qt loop |
| `Bootstrap(data_dir, limit=None)` | Bus + services + registry banata hai; **subscriptions sirf yahan**; `start()` = window show + `AppStarted`. `limit=None` = asli DB ki poori history load hoti hai. **Part 4**: `_build_architecture()` startup par real `market_manifest()` + `chart_manifest()` ko `ComponentRegistry` mein register karta hai (**implementations = live service instances** — market ke 4 capabilities → `SymbolRepository` instance, chart.render → `ChartEngine` instance) + `SystemModel` build (startup par ek baar). Properties: `bus`, `services` (old name lookup — **unchanged**), `components` (new capability lookup), `system_model` (architecture model). Naya runtime nahi — existing wiring wahi hai |
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

### 3.1 AI Engineering + Evolution Layer (Part 3) — `core.ai`

**Motto: "AI proposes. VAYREN validates. Deterministic runtime executes."** — pure model/observation layer, stdlib-only, koi LLM call nahi, runtime kabhi AI se change nahi hota. `core.ai` sirf `core.system` + `core.contracts` import karta hai.

| Public cheez | Kaam |
|---|---|
| `Intent` / `IntentKind` | User goal ka typed model (CREATE_WORKFLOW/ADD_COMPONENT/ADD_DATA_SOURCE/CREATE_STRATEGY/IMPROVE_PERFORMANCE/OTHER) + `constraints`/`requested_capabilities`/`inputs`/`expected_outputs`/`risk_level` (default LOW) |
| `classify(goal)` / `parse_intent(goal)` / `validate_intent(intent)` | Keyword rules se IntentKind; `parse_intent` invalid par `IntentValidationError` |
| `Plan` / `PlanChange` / `PlanChangeKind` / `PlanRisk` / `Rollback` | Plan model — `id` regex `^[a-z][a-z0-9_]*$`, reused∩new overlap rejected, change targets components list mein |
| `validate_plan(plan)` / `risk_rank(level)` / `plan_risk(plan)` | Structural validation + risk ordering (LOW 0 < MEDIUM 1 < HIGH 2); plan risk = max declared (default LOW) |
| `Policy` / `PlanValidator` | `Policy` = `protected_components`/`forbidden_capabilities`/`max_risk`/`allowed_change_kinds`/`requires_rollback_above`. `PlanValidator.validate(plan, system)` — reused capability provided honi chahiye, nayi capability pehle se na ho, MODIFY/REMOVE sirf known components, phir har policy. Errors deterministic |
| `simulate_plan(system, plan)` / `ChangeSimulation` | Part 2 `analyze_change` per component → `affected_components`/`affected_capabilities`/`affected_workflows`/`required_tests` (`regression:<component>` + plan.tests)/`estimated_risk` (max)/sorted `reasons` |
| `Sandbox` / `SandboxStage` / `SandboxDeployment` / `SandboxError` | Strict lifecycle: PLAN→SANDBOX→TEST→BENCHMARK→VALIDATE→APPROVE→DEPLOY — **stage skip = `SandboxError`**. `deploy()` = recorded decision only (note: "deterministic runtime remains authoritative"); invalid plan approve nahi ho sakta; `simulation`/`validation` lazy |
| `AiBoundary` / `ActionKind` / `BoundaryDecision` / `BoundaryViolation` | 9 allowed actions + 6 **forbidden** (execute_trade, bypass_risk, delete_production_data, modify_protected_system, deploy_unvalidated, override_contract). Fail-closed: `classify()` unknown → None → `request_text` denied "unrecognized action request"; `require()` forbidden → `BoundaryViolation` (PermissionError) |
| `AiProvider` / `OfflineProvider` / `AiProviderRegistry` | Provider-agnostic boundary. `OfflineProvider` default (hamesha unavailable); `select(preferred)` → None on unavailable = **graceful AI optionality**; dupe register → ValueError |
| `EngineeringMemory` / `EngineeringEntry` / `Decision` | Engineering decisions (problem/hypothesis/experiment/change/benchmark/result/decision/reason/evidence) — `search()` case-insensitive sab fields, `decisions()` = ACCEPTED+REJECTED (DEFERRED excluded), `problems()` unique |
| `PerformanceMemory` / `PerformanceRecord` / `METRICS` / `LOWER_IS_BETTER` | Measured facts only — `record()` **≥1 metric required** (`ValueError`). `METRICS` = latency_ms/throughput/cpu_percent/memory_mb/io_ops/error_rate; `best()` min for lower-is-better (sab minus throughput), max for throughput; `average`/`latest`/`summary`/`subjects` |
| `OptimizationStudy` / `Candidate` / `RankedResult` / `OptimizationError` | Benchmark current vs candidates → `compare`/`recommend` (measured only). **`adopt()` hamesha `OptimizationError`** — adoption requires validated plan + sandbox approval (guarded self-optimization) |
| `ContextBuilder` / `ContextRequest` / `AiContext` / `SECTIONS` | Deterministic provider context — sections in canonical order (components/capabilities/contracts/dependencies/workflows/events/system_state/architecture_history/engineering_memory), `build()` default = all; unknown section → ValueError; `render()`/`to_json()` |

## 4. 03_market — `market` (Godown)

| Public cheez | Kaam |
|---|---|
| `Bar` | Frozen dataclass: `symbol, open, high, low, close, volume, timestamp` (ISO string) + optional `bar_size, vwap, trades, source` |
| `SqliteCandleDatabase(path)` | Sample-schema DB: `connect()` (file + table check), `fetch_candles(symbol, limit: int \| None)` → sabse recent rows ascending, `limit=None` = poora table ascending, `close()` |
| `OhlcvCandleDatabase(path)` | **Asli Zerodha schema**: table `ohlcv`, column `candle_time`. Same interface — `CandleRepository` dono se kaam karta hai. `fetch_candles(symbol, limit: int \| None)` — `None` = ascending full scan (**SQLite `LIMIT ?` NULL bind kabhi mat karo** — `datatype mismatch` error deta hai) |
| `CandleRepository(database)` | `get_candles(symbol, limit: int \| None)` → `list[Bar]` — row → Bar mapping ka sirf ek jagah; `None` = full history. Phase 5B: `detect_bar_duration(symbol)` (rows ka dominant bar size), `detect_timeframes(symbol)` → `tuple[str, ...]`, `get_candles_timeframe(symbol, timeframe, limit: int \| None)` → aggregated bars (`None` = saari rows aggregate + full list). Phase 5L: `aggregate_bars` ab `datetime.fromisoformat` parse (strptime = locale lookup per call, 100x slower) + single-pass bucket accumulators |
| `SymbolRepository(data_dir)` | `list_symbols()` → sorted tuple; `get_candles(symbol, limit: int \| None)` → per-stock file kholkar `CandleRepository` se load; Phase 5B: `detect_timeframes(symbol)` + `get_candles_timeframe(symbol, timeframe, limit: int \| None)` (same file-open pattern). Phase 5N: `get_quotes(symbols: tuple[str, ...])` → `tuple[SymbolQuote, ...]` — har symbol ke DB ka **sirf latest candle** (`fetch_candles(symbol, 1)` = `ORDER BY candle_time DESC LIMIT 1`), mapping CandleRepository ke through; missing file → skip (kabhi raise nahi) |
| `SymbolQuote` | Frozen dataclass: `symbol, price` (latest close), `change_pct` (`(close−open)/open*100` — Bar.return_pct semantics), `timestamp` (latest candle time). Kabhi fabricated nahi — sirf real candle se |
| `MarketDataLoader(repository, bus)` | `on_load_symbol` → repository → `DataLoaded`; `on_timeframe_changed` → `get_candles_timeframe` → `DataLoaded`; fail → log, kuch nahi bhejta |
| `SymbolListLoader(repository, bus)` | `on_list_symbols` → scan → `SymbolsListed`; fail → log, kuch nahi bhejta |
| `QuoteLoader(repository, bus)` | `on_symbols_listed` → `get_quotes` → `QuotesLoaded`; **identical universe = no-op** (`_last_symbols` guard, initial `None`); fail → log, kuch nahi bhejta. Batch ek baar at universe load — cached, kabhi re-query nahi |
| `TimeframeListLoader(repository, bus)` | `on_list_timeframes` → `detect_timeframes` → `TimeframesListed`; fail → log, kuch nahi bhejta. Hamesha SQLite se detect — koi cached list nahi |
| `TIMEFRAME_LADDER` | Canonical candidate labels: `("1m","3m","5m","15m","30m","45m","1h","2h","4h","1D","1W")` |
| `timeframe_seconds(name)` / `timeframe_name(seconds)` | Ladder (aur generated `90m`/`2D`/`2W`) ↔ seconds conversion |
| `available_timeframes(base_seconds)` | Ladder entries jo `base` ke whole multiple hain + base itself (generated label agar ladder mein nahi) — **existence hamesha DB se** |
| `LoadSymbol`, `ListSymbols`, `DataLoaded`, `SymbolsListed`, `QuotesLoaded`, `TimeframeChanged`, `ListTimeframes`, `TimeframesListed` | Events (catalog dekho) |
| `market_manifest()` | **Production manifest (Part 4)** — asli market component: storage, v1.0.0, capabilities `data.query.candles` / `data.query.timeframes` / `data.query.quotes` / `data.transform.aggregate` (contracts + inputs/outputs), deps `core`, events consumed (LoadSymbol, TimeframeChanged, ListSymbols, ListTimeframes) + produced (SymbolsListed, QuotesLoaded, DataLoaded, TimeframesListed), contract invariants. Registry registration hi validation hai |

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

## 5. 04_chart — `chart` (Painter)

| Public cheez | Kaam |
|---|---|
| `ChartModel` | Frozen: `symbol: str`, `bars: tuple[Bar, ...]` (timestamp ascending) |
| `ChartEngine(bus)` | `on_data_loaded`: sort karo → empty check → `ChartReady` |
| `CandleRenderer` | Stateless QPainter: grid, price labels, wicks, bodies, volume. No state, no data logic |
| `TimeAxisRenderer` | Stateless QPainter (X-axis): `select_step(avg_seconds, slot_px)` (min 96px spacing, calendar ladder), `format_for_step(step)` (10:15 → 10 Apr → Apr → Apr 2024 → 2024), `tick_times(first, last, step)` (calendar-aligned), `paint(painter, bars, first, last, axis_rect)` — crisp gridlines, antialiased labels, never overlap. No state, no events |
| `CrosshairRenderer` | Stateless QPainter: `paint(painter, position, plot_rect)` — vertical (full height) + horizontal (full width) lines through mouse, RGBA(180,180,180,120) 1px, cached pen (zero paint-time allocation). No state, no events |
| `chart/theme.py` | **VAYREN design system** (Phase 5M): `APP_PALETTE` — fixed dark terminal QPalette (Window/Base `#101418`, AlternateBase `#161c26`, Midlight `#1f2632`, Mid `#2a3342`, Highlight teal `#26a69a` + HighlightedText `#0b0f13`, muted placeholder `#5d6778`; Disabled/Inactive groups muted) + `APP_STYLE` — palette-only QSS applied in `ChartWindow.__init__`. **QSS `palette()` roles application palette se resolve hote hain** → window `QApplication.setPalette(APP_PALETTE)` bhi karta hai. 3px radius, hairline structural separators; QToolButton/QPushButton transparent + hover `palette(midlight)` + pressed `palette(mid)` + disabled muted; **checked = highlight pill + 600**; `TimeframeToolbar QPushButton` uniform segments (min-width 44, min-height 24, 500); `TimeframeToolbar` bottom 1px `palette(mid)`; framed QMenu (selected = highlight, **checked = accent + 600**, separator hairline, disabled muted); `QSplitter::handle` `palette(mid)` + **hover highlight**; `QToolTip` styled (palette tooltip-base/text + 1px mid border). `setHandleWidth(1)` = hairline panel boundaries. Pure presentation, no logic |
| `OverlayRenderer` | Stateless QPainter — 2 groups: **(1) Permanent header strip** (har paint): `paint_symbol_info` (SYMBOL • timeframe • exchange, **two-tone unframed**: symbol bright bold `SYMBOL_TEXT #e8eef5` + meta muted `META_TEXT #8a93a6`) + `paint_ohlc` (latest bar; **two-tone fields**: letters muted `OHLC_LABEL_TEXT #5d6778`, values `OHLC_VALUE_TEXT #b7c0cc`, change `+x.xx (+x.x%)` bull/bear tinted; `left_margin` = symbol rect right). Strip backdrop constants: `STRIP_BG #141922`, `STRIP_BORDER #232936` (widget draws band + hairline). **(2) Crosshair-following** (unchanged, framed pills): `paint_price` (right scale, crosshair Y) + `paint_time` (axis strip, crosshair X, timeframe-aware). No state, no events |
| `CandleChartWidget(QWidget)` | `set_model(model)`; wheel = zoom, drag = **2D pan** (horizontal time window + vertical price range, zoom unchanged, pointer grabbed), mouse move = crosshair (`_crosshair_pos`), leave = hide, resize = repaint. Touch: 1 finger crosshair, 2 fingers 2D pan + pinch zoom. Price strip drag = vertical price scaling, double-click = reset. Right-click = context menu (reset view), `Alt+R` = reset view. **Permanent header** (Phase 5I + 5M strip): `_paint_header` har paintEvent mein top bar — solid `OverlayRenderer.STRIP_BG` strip (3px radius) + bottom hairline (`STRIP_BORDER`), `model.symbol • model.timeframe • model.exchange` + `model.bars[-1]` ka OHLC — crosshair se independent, model change par automatic update. Crosshair overlays sirf price + time. **Static-layer paint cache** (Phase 5L): `_static_pixmap` grid+candles+axis ko widget-sized QPixmap mein bake karta hai (key: model id, first, last, price range, volume_max, size) — crosshair-move paints sirf blit hain; `_window_stats()` visible low/high/volume_max single-pass + cached. No bus, no SQL, no loading. **Initial viewport**: fresh lifecycle (first open / naya symbol / timeframe change) → `_initial_count(total)` = `max(MIN_VISIBLE_BARS, min(INITIAL_BARS, total))` — **latest `INITIAL_BARS` (150) trailing window**, right margin pe, follow-latest on; poori history loaded rehti hai (sirf visible window limited). Same symbol+timeframe reload → incremental follow-latest re-anchor / manual-pan shift. `reset_view()` wahi latest `INITIAL_BARS` viewport (pan + zoom + price auto-fit) |
| `SymbolListWidget(QListWidget)` | Stock list widget: `set_symbols(tuple)`, `select_symbol(str)`, `set_quotes(dict[str, SymbolQuote])`, Qt signal `symbol_selected`. **Rows (Phase 5N) = `_SymbolRowDelegate`** (QStyledItemDelegate, quote item ke `UserRole` mein): uniform 2-line height (`2 × font height + 6`); line 1 = symbol (DemiBold, left) + price (`,.2f`, right-aligned), line 2 = change % (11px, bull `#26a69a`/bear `#ef5350` — CandleRenderer.BULL/BEAR, right); bina quote wali row = symbol-only vertically centered. **Selected = midlight bg + 2px `palette(highlight)` accent edge + `palette(Text)` color** (HighlightedText `#0b0f13` midlight par invisible tha — kabhi use nahi; change % colors selected par readable), hover = alternate-base, hairline `palette(midlight)` sab rows. QSS string (12px font, slim 6px scrollbar, `::item` rules) unchanged — delegate override karta hai. `set_quotes` bina quote wale rows ko `None` set karta hai (stale kabhi nahi). No bus, no SQL — pure UI. `WatchlistWidget` ke andar list ke roop mein use hota hai (still public export) |
| `WatchlistWidget(QWidget)` | Watchlist panel (header + sort + list rows). Signals: `symbol_selected(str)`, `reset_requested()`. `set_symbols(tuple)` (universe populate), `set_quotes(tuple[SymbolQuote, ...])` (real quote snapshot — `dict[symbol → quote]` presentation state, **kabhi re-query nahi**; `_refresh_list` = sort/watchlist switch par quotes re-attach), `select_symbol(str)` (highlight), `add_watchlist()`, `remove_active_watchlist()`, `sort_by_name(ascending: bool)`. Properties: `watchlists: tuple[str, ...]`, `active_watchlist: str`, `symbols: tuple[str, ...]` (display order), `current_symbol: str \| None`. `ALL_STOCKS` = default watchlist name. **Header (Phase 5M)**: margins (6,4,6,4), spacing 2, selector fixed height 24 + weight 600, icon buttons (+ ↩ ⋯) fixed 24×24 — **Phase 6D: header chevron button + `collapse_requested` hata diye (panel toggle ab tool rail ke pehle icon se)**; sort row margins (12,3,6,3), uppercase `SYMBOL` muted table-header (11px, weight 600); separators = explicit 1px `palette(midlight)` hairline QFrames (`_SEPARATOR_STYLE`). Watchlists session-local (in-memory) — persistence aage ke phase. No bus, no SQL — pure UI |
| `ChartToolsToolbar(QWidget)` | TradingView-style vertical tools rail, chart canvas ke left edge par pinned (fixed `TOOLBAR_WIDTH = 40`, `BUTTON_SIZE = 30`, icon 16×16 vector QPainter-drawn, koi asset nahi). **EXACTLY 2 rendered buttons (Phase 6K — minimal sidebar)**: watchlist nav (FIRST — checkable, mode group ke bahar, `watchlist_clicked` signal, **default checked**) + download nav (SECOND — checkable, mode group ke bahar, `download_clicked` signal, download glyph, watchlist ke seedha neeche), phir `addStretch(1)` = empty rail space. Cursor button + internal separator `__init__` se hataye (6K); koi extra item nahi. Baaki tool-group defs (`_MODE_TOOLS`…`_SETTINGS_TOOLS`) + `_draw_*` drawers retained (architecture, render nahi hote). Root bg `palette(alternate-base)` + right hairline — widget-level QSS, palette-roles only. QIcon states: Normal/Off=`TEXT`, Normal/On=`ACCENT_TEXT`, Disabled/Off=`PLACEHOLDER` |
| `ChartWindow(QMainWindow)` | Splitter host (**tools rail + watchlist + chart**, isi order mein); ctor `ChartWindow(widget, watchlist, toolbar, tools, bus, limit=None, download=None)`; tools rail splitter item 0 — extreme left, watchlist uske turant right, chart container (timeframe strip + canvas) baaki saari jagah (options column removed — empty strip tha); `on_symbols_listed` → `watchlist.set_symbols`; **`on_quotes_loaded` → `watchlist.set_quotes`** (Phase 5N); `on_chart_ready` → model + title `VAYREN — SYMBOL` + `WindowRendered`. Symbol click: `_current_timeframe` selected → `TimeframeChanged` (timeframe symbol switch par persist rehta hai), warna `LoadSymbol`; dono mein `ListTimeframes`. Timeframe click → `TimeframeChanged` + `_current_timeframe` update. **Watchlist collapse (TradingView-style, Phase 6B)** → **Phase 6D nav architecture**: `_active_panel: str | None` SINGLE state (`None | "watchlist" | ...` — koi multiple booleans nahi); `toggle_panel(panel_id)` = `active == clicked ? null : clicked`; `active_panel` property; `_apply_panel_state()` = panel visible iff active + rail button checked sync + sizes open `[40, 220, rest]` / closed `[40, 0, rest]`. Splitter 3 items: `[tools(0) | watchlist(1) | container(2)]` jab `download=None`; **4 items `[tools | watchlist | download | container]` jab download panel injected (Phase 6F — chart data import nahi karta, bootstrap inject karta hai)**. **Tool rail = permanent navigation**: collapse strip + header `«` chevron + `ChevronToggleButton`/`chevron_toggle.py` sab removed; rail icons (`watchlist_clicked` → `toggle_panel("watchlist")`, `download_clicked` → `toggle_panel("download")` — Phase 6F) panels open/close karte hain; closed state = sirf panel hidden (contents/state intact — selected symbol, scroll, quotes), chart freed space mein expand. **Splitter handles (empirical)**: 3 items hidden middle → 1 handle (chart = w − 40 − 1); 4 items 1 hidden + 1 visible panel → 2 handles (chart = w − 40 − 220 − 2); 4 items dono hidden → 1 handle (w − 40 − 1). **Theme (Phase 5M)**: `setPalette(APP_PALETTE)` + `QApplication.setPalette(APP_PALETTE)` (QSS palette() resolution) + `setFont(Segoe UI 9pt)` + `setStyleSheet(APP_STYLE)`. **`on_timeframes_listed` `_current_timeframe` re-apply** karta hai — TimeframesListed ChartReady ke baad aata hai, isliye active button symbol switch par hamesha sahi checked (visual state fix, data flow untouched). Watchlist reset tool → existing `widget.reset_view()`. Properties `watchlist`, `toolbar`, `tools`, `download` |
| `ChartReady` / `WindowRendered` | Events (catalog dekho) |
| `chart_manifest()` | **Production manifest (Part 4)** — asli chart component: presentation, v1.0.0, capability `chart.render` (contract: model bars ascending), **consumes `data.query.candles`**, deps `core` + `market`, events consumed (DataLoaded) + produced (ChartReady, WindowRendered), resources (qt, display) |

## 5.5 02_data — `data` (Godown ka Writer)

Historical download engine (Zerodha Kite). Library-only — koi CLI nahi. Engine sirf worker thread ke andar; UI → events → worker → events → UI.

| Public cheez | Kaam |
|---|---|
| `DownloadSettings` | Frozen config: `chunk_days=200`, `max_history_years=10`, `max_retries=5`, `retry_delay_s=5`, `max_consecutive_429=3`, market `09:15–12:40` IST, `lock_stale_seconds=120`, `max_passes=5`, `default_interval="15m"`, `data_dir`/`log_dir`/`token_file`/`symbols_csv` paths. `INTERVAL_LABEL`/`INTERVAL_MINUTES` **canonical keys** `("1m","5m","15m","30m","1h")`, `NSE_HOLIDAYS` 2024–2026. Credentials yahan NAHI hain (provider package ke paas) |
| `ZerodhaCredentials` | `data/provider/zerodha/credentials.py` — `from_env()` `VAYREN_ZERODHA_API_KEY/SECRET/USER_ID/PASSWORD/TOTP_SECRET` se, **auth time par**; repr redacts. **Phase 6N layered loader**: `load_zerodha_credentials(settings, store=None)` — per-field: stored value (agar hai) → env → none. Engine/settings/UI se import NAHI hota (isolation test) |
| `CredentialField` / `ProviderConfigError` | `data/provider/credentials.py` — frozen field schema (`key/label/secret/required/help`); validation errors sirf labels ("API Key is required."), kabhi value nahi |
| `EngineLock` | pid + heartbeat file lock, stale 120s (settings) — `acquire()`/`release()` |
| `CandleDB` | Per-symbol `SYMBOL.db`: `ohlcv` (candle_time TEXT PK, OHLC REAL, volume INTEGER), `non_trading`, `history_boundaries`. WAL, `INSERT OR IGNORE`, V9/V9.1 migrations, `upsert/dedupe/count/corruption_count/remove_corruption/get_boundary/set_boundary` |
| `DatabaseScanner` | Head/tail gap detection, LISTING_START boundary `<=` rule; `scan_one(symbol, interval)` public single-symbol scan → `SymbolInfo` (state NOT_STARTED/PARTIAL_DOWNLOAD/DOWNLOAD_COMPLETE, gaps, rows, boundaries) |
| `FetchEngine` | `data/provider/zerodha/fetch.py` — Kite historical_data fetch: 5 retries, 429 par stop @3, `TOKEN_EXPIRED` sentinel; throws on bad input; `RATE_LIMITED` (sentinels re-exported from `data.provider.contract`). **Broker-level: Kite interval ids/token idhar** |
| `AuthEngine` | `data/provider/zerodha/auth.py` — `available()`, `ensure_token()` (token.json + Selenium TOTP auto-login), `renew()`, `_load_token/_save_token` |
| `Provider` (protocol) | **Engine boundary (Phase 6L/6M)** — `data/provider/contract.py`: `available() -> (bool, str)`, `symbols() -> set[str]` (canonical symbols), `fetch_candles(symbol, interval, start, end) -> list[dict] | object` (canonical intervals `1m/5m/15m/30m/1h`), `new_session()`, `renew()`. Sentinels `TOKEN_EXPIRED`/`RATE_LIMITED`; `ProviderError(message, code)` — 7 normalized codes (AUTHENTICATION_FAILED/RATE_LIMITED/INVALID_SYMBOL/NETWORK_ERROR/PROVIDER_UNAVAILABLE/INVALID_REQUEST/UNKNOWN_PROVIDER_ERROR). **Engine core import par koi broker SDK load nahi hota** (subprocess isolation test) |
| `ZerodhaProvider` | `data/provider/zerodha/adapter.py` — the ONLY Zerodha/KiteConnect knowledge: `KITE_INTERVAL_IDS` (canonical → Kite ids), `symbols()` (resolver, cached), `fetch_candles` wrapper (INVALID_REQUEST/INVALID_SYMBOL/AUTHENTICATION_FAILED/NETWORK_ERROR normalization); `auth` optional ctor param (tests inject FakeAuth); lazy `_fetcher` `new_session()`/`renew()` par reset (429-counter per-session semantics preserved). **Phase 6N credential surface** (manager duck-type): `display_name="Zerodha"`, `credential_fields` — 5: `api_key`+`api_secret` required, `user_id`/`password`/`totp_secret` optional (real auto-login ke liye), `build_credentials(values)` classmethod (trim → ZerodhaCredentials), `reload_credentials(creds=None)` → naya AuthEngine + `_fetcher=None`; ctor default = `AuthEngine(settings, load_zerodha_credentials(settings))`. `auth.py`/`fetch.py`/`instruments.py`/`credentials.py` same package |
| `CredentialStore` / stores | `data/provider/credentials_store.py` — protocol `save/load/delete` (JSON blob per `vayren:<provider>`). `FileCredentialStore` (`<data_dir>/credentials/vayren.<provider>.json`, corrupt → None); `WindowsCredentialStore` (ctypes advapi32 CredWriteW/CredReadW/CredDeleteW — TargetName=`vayren:zerodha`, UserName="vayren", CRED_PERSIST_LOCAL_MACHINE; no keyring dependency); `default_store(data_dir)` = win32 → Windows else file |
| `ProviderCredentialsManager` | `data/provider/manager.py` — `(settings, provider, store=None)`. `load_values()`/`has_stored()`/`validate()` (label-only errors)/`apply(values)` (live load, no persist)/`test_connection()`/`save(values)` (validate → raise on failure → trim → store.save → apply)/`clear()` (store.delete + reload)/`reload()`. Provider duck-type: `display_name`, `credential_fields`, `build_credentials`, `reload_credentials`. Priority: store → env → Not Configured. Secrets kabhi status/errors/logs mein nahi |
| `build_provider` / `register_provider` | `data/provider/factory.py` — `settings.provider` dispatch; `ProviderFactory` callable protocol; default registry `{"zerodha": ZerodhaProvider}`; unknown name → ValueError |
| `DownloadQueue` | `DownloadJob` queue, PARTIAL → NOT_STARTED priority, `add/get/peek/clear`; `build_from_scan(infos, known_symbols: set[str] | None, settings)` — provider-universe filter |
| `forward_sweep` | `forward_sweep(fetch_chunk: Callable[[datetime, datetime], list[dict] | object], ...)` — 200-day chunks + jitter, all-chunks-zero boundary detection, `candidate` generator. Broker token/interval id is layer tak NAHI pahunchta |
| `HistoricalDownloadEngine` | `run_download(symbol, interval, from_dt, to_dt)` → dict (ok/new_rows/db_total), `run_batch()`, `scan_symbol(symbol, interval)`, `cancel()`, `reset()`, `set_reporter()`. **`provider` required ctor param (Phase 6L — None → TypeError; bootstrap uses `build_provider`)**. **Phase 6M: canonical intervals only, `provider.symbols()` universe filter (auth-code-aware), `new_session()` per job, `fetch_chunk = partial(provider.fetch_candles, symbol, interval)`**. Reporter protocol (Null/Recording) replaces console printing. Renewal: first sweep fail → renew once → retry once; second fail → `{"ok": False}` no `error`; renewal exception → `{"error": "auth"}` |
| `DownloadWorker(QThread)` | Engine owner (ek hi instance); op queue (download/coverage/cancel); signals: started/progress/completed/failed/coverage/log; `shutdown()` |
| `DownloadPanel(QWidget)` | Inputs: **INTERVAL (QComboBox, canonical ids as userData, default 15m) full-width row + STOCKS label + `StockChecklistWidget` (Phase 6G — single SYMBOL input replaced)**; **From/To vertically stacked (Phase 6F — side-by-side 220px mein fit nahi hota tha)**, QDateEdit calendar popups; **defaults FROM `2017-01-01`, TO = dynamic current date (Phase 6I-bis — hardcode nahi)**, display `dd MMM yyyy` full-date, internal ISO `yyyy-MM-dd` emit; `_DATE_EDIT_STYLE` box-model QSS (`min-width: 0`, `::drop-down` 20px subcontrol, center right) + Expanding/Fixed size policy + `_date_box` margins 0 = FROM/TO equal full-width; `_plan_range` wordWrap (full range kabhi truncate nahi). **Whole panel = flow column, NO inner QScrollArea (Phase 6J — single main scrollbar parent ka)**; actions Download/Coverage/stretch/Cancel flow mein (margins 0). `_COMPACT_STYLE` (11px, padding 2/5). Signals: `download_requested(str, str, str, str)`, `coverage_requested(str, str)`, `cancel_requested()`; `set_busy(bool)` (busy → inputs + checklist disabled, Cancel enabled). **Download/Coverage = first selected stock, empty selection = no-op (Phase 6G)**. `set_symbols(tuple)`/`symbols` passthrough. No bus, no events, no engine |
| `HistoricalDownloadPanel(QWidget)` | **Ex `HistoricalDataWindow` (Phase 6F) — ab embedded side panel** (data/ui/historical_panel.py): header `Historical Download` + `×` close button (`close_requested` signal) + DownloadPanel (**stretch 3 — Phase 6H**, growing middle) + StatusView (stretch 0, fixed) + LogView (stretch 1). **Poora content (header + panel + status + log) EK QScrollArea mein wrap (Phase 6J — SINGLE main scrollbar, widgetResizable, NoFrame, horizontal AlwaysOff)**; stocks list apna internal scrollbar rakhta hai (independent). Panel signals → bus requests (`DownloadRequest`/`CoverageRequest`/`CancelDownload`); `on_download_started/progress/completed/failed/coverage` + `on_log_line` render facts; `set_provider(ready, reason)`; **`set_symbols(tuple)`/`symbols` + `on_symbols_listed(event)` (Phase 6G — `SymbolsListed.symbols` → checklist, bootstrap subscribes)**; **`set_credentials_manager(manager)` (Phase 6N — status_view ko passthrough; status card "Configure Credentials" button → dialog)**. Properties: `panel`, `status`, `log` |
| `StockChecklistWidget(QWidget)` (Phase 6G) | (data/ui/stock_checklist.py) Searchable multi-stock checklist: search QLineEdit ("Search stocks...", clear btn, case-insensitive substring, `item.setHidden`) + bar `[Select All][Clear All]…stretch…Selected: X` (buttons `_SlimButton` + **Preferred policy — QSS box-min bypass**; count `_SlimLabel` Maximum) + QListWidget (NoSelection, mouse tracking, per-pixel scroll, **min 120, uncapped + stretch-absorbing (Phase 6H)** — `_ChecklistList` sizeHint rows×26 cap 16; list `addWidget(..., 1)`, form `addWidget(panel, 3)` in HistoricalDownloadPanel — @1080 list 278 / @1280 428; slim scrollbar, row 26). `_ChecklistDelegate` 14×14 rounded checkbox (selected = Highlight fill + HighlightedText check; hover = AlternateBase; **`option.palette` mat lo, `self._owner.palette()`** — QSS list ka palette app-default rebuild hota hai). API: `set_symbols(tuple)` (reset selection+filter), `symbols`, `is_selected(row)`, `selected_symbols` (universe order), `selected_count`, `select_all` (hidden rows bhi), `clear_all`, `selection_changed` Signal. No bus, no events, no engine |
| `ProviderCredentialsDialog` (Phase 6N) | `data/ui/credentials_dialog.py` — `(manager)`; title `Configure <display_name>`; har `credential_field` ke liye ek row — secret fields `_SecretEdit` (Password echo default, `reveal_changed` Signal, eye QToolButton "👁" checkable, `focusOutEvent` re-mask), plain fields QLineEdit; stored values prefill. Buttons: **Test Connection** (validate required pehle → "✕ <label> is required."; phir `test_connection()` → "● Not tested"/"Testing connection…"/"✓ Connection successful" + "Zerodha provider is ready." / "✕ Connection failed" + "Authentication failed.\nCheck your API credentials." — raw reason/values kabhi nahi), **Save & Connect** (`manager.save()` — ProviderConfigError par dialog open + status line error), **Cancel**, **Clear Credentials** (sirf `has_stored()` par; QMessageBox Remove/Cancel confirm → fields empty + "● Not Configured"). No bus, no events |
| `StatusView` (Phase 6N) | `data/ui/status_view.py` — `set_credentials_manager(manager)`; status card "● Connected"/"● Not Configured" + "Configure Credentials" button (`_show_credentials_dialog` → dialog exec → hamesha `manager.reload()` + `set_provider(ready, reason)` — test-apply/Cancel ke baad persisted state restore); Advanced ▾ mein "Environment Variable Fallback" section (collapsed default, env var names sirf yahan, card label mein kabhi nahi); `_provider_detail` raw reason hidden by default |
| `data_manifest()` | Production manifest — component `historical_data` (storage), capabilities `historical_data.download` (contract: idempotent writes, no fabricated candles, resumes from DB state) + `.coverage` + `.status`, deps sirf `core`, events consumed (requests) + produced (results) |
| Events | `DownloadRequest`, `CoverageRequest`, `CancelDownload`, `DownloadStarted`, `DownloadProgress`, `DownloadCompleted`, `DownloadFailed`, `DownloadCoverage` (catalog dekho) |

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
