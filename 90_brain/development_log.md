# Development Log — Kya Kab Hua

**Nya entry hamesha upar likho.**

## 2026-08-13 — Right-Click Context Menu (Reset Chart View)

### Kya hua tha?

Chart par right-click kuch nahi karta tha (default OS menu ke bajaye kuch nahi), aur viewport ko fresh-chart state (fit-all) par wapas lane ka koi ek-jagah command nahi tha — `_reset_price_scale` sirf price strip ka manual scale reset karta tha.

### Decision

- **Ek hi reset command — do trigger.** `CandleChartWidget.reset_view()` naya public method (viewport-only: `_first`/`_last` → `_fit_all_count` + `_anchor_first`, `_price_manual = None`, `_follow_latest = True`, crosshair clear, grid cache invalidate). Dono paths — menu click aur `Alt+R` — ek hi `QAction` (`↩ Reset chart view`, shortcut `QKeySequence(Alt+R)`) se `triggered → reset_view` chalte hain. Koi duplicate reset logic nahi.
- **QAction widget par add** (`addAction`) — default `WindowShortcut` context, isliye `Alt+R` poore main window mein active hai (focus kisi bhi child par ho). No new dependency, no focus-policy change.
- **Right-click press par menu** (`mousePressEvent` RightButton branch) — `QMenu` ek hi action ke saath, cursor position par `exec()` (Qt khud click-outside/Escape/selection par band karta hai, screen bounds clamp bhi Qt ka). `contextMenuEvent` override = default OS/Qt menu suppress.
- **Scope pakka: sirf widget UI.** Reset sirf viewport (pan + zoom + visible/logical range + price auto-fit) — data reload, symbol/timeframe/candles/indicators sab untouched. `set_model` fresh-chart lifecycle, pan/zoom/crosshair/touch — sab waisa hi.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/candle_chart_widget.py` | `QAction` `↩ Reset chart view` (Alt+R) + `addAction`; `reset_view()`; `mousePressEvent` right-button → `_show_context_menu`; `_context_menu`/`_show_context_menu`/`contextMenuEvent` (suppress default); class docstring interactions update |
| `03_chart/chart/tests/test_chart_context_menu.py` | Naya — 10 tests: action text/shortcut, single-action menu, contextMenuEvent suppressed, right-click wiring (menu stub ke saath — offscreen `QMenu.exec` auto-trigger quirk), crosshair/drag state untouched, action trigger → viewport reset, price auto-fit restore, model identity untouched, no-model noop |

### Verification

- `pytest` = **182 passed** (172 + 10 naye); chart 113, app+core+market 69
- `ruff check` ✓ + `ruff format --check` ✓ (1 file formatted)
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — TradingView-Style Free Chart Panning

### Kya hua tha?

Chart drag sirf **horizontal** pan karta tha (`_pan_from_drag` sirf x use karta tha); vertical (price) pan nahi tha aur pointer capture nahi tha. User chahta tha TradingView jaisa free pan: press+hold karke chart ko left/right **aur** up/down le jaao, zoom change kiye bina.

### Decision

- **Ek hi existing system mein integrate kiya** — naya panning system nahi banaya. Existing `mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent` drag flow ko extend kiya.
- **Vertical pan = price range shift.** Chart ki vertical dimension price hai. Vertical drag `_price_range()` ko same span (zoom) rakh ke shift karta hai, existing `_price_manual` override ke through — span badalta nahi → zoom unchanged. Sirf jab user vertically drag karta hai tab `_price_manual` set hota hai; horizontal-only drag pe auto-fit wahi rehta hai (existing behaviour preserved).
- **Pointer capture.** `grabMouse()`/`releaseMouse()` — mouse press ke baad drag chart se bahar nikalne par bhi active rehta hai (web `setPointerCapture` ka Qt equivalent). Touch `TouchEnd`/`TouchCancel` pehle se hi state reset karta hai.
- **Touch:** 2-finger pan ab vertical bhi karta hai (`_pan_price_delta_px`) — horizontal + vertical dono. 1-finger crosshair aur pinch zoom wahi. Dedicated interactions (price-strip drag scaling, double-click reset) untouched.
- **Zoom unchanged:** time zoom `_first`/`_last` ke beech `count` fixed rehta hai; price zoom `span` fixed rehta hai. Siraf viewport position badalti hai. Candle data/OHLC/timeframe/indicator — kuch nahi chhua.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/candle_chart_widget.py` | `_drag_origin_y`/`_drag_price_low`/`_drag_price_high` state; `mousePressEvent` me `grabMouse()`; `mouseMoveEvent` me x+y dono; `_pan_from_drag(x, y)` vertical price pan; naya `_pan_price_delta_px(y)`; `mouseReleaseEvent` me `releaseMouse()`; 2-finger touch vertical pan |
| `03_chart/chart/tests/test_chart_viewport.py` | 4 naye tests: vertical pan (span/zoom unchanged), horizontal-only keeps auto price, diagonal pans both axes, touch 2-finger vertical pan |

### Verification

- `pytest` = **172 passed** (168 + 4 naye)
- `ruff check` + `ruff format --check` ✓ (2 files auto-formatted)
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-10 — Release v1.1.0: Version Control Setup

### Kya hua?

Version control system setup hua: single source of truth + pehla proper semantic release tag.

### Decisions

- **Single source of truth**: `pyproject.toml` `[project] version` — version ab wahi ek jagah hai (0.1.0 stale tha aur git tags se mismatch karta tha). Runtime code koi version read nahi karta (koi `__version__` nahi), isliye koi duplication add nahi ki.
- **Version**: Phase 5A–5C ka kaam (timeframe system + UI, symbol list, crosshair + overlay renderers, load-all fix) foundation ke upar **naya feature** hai, breaking nahi → MINOR bump → **v1.1.0**. Previous: `v1.0-architecture` tag (1.0.0).
- **Editable install fix**: environment ab `Downloads\vayren-core` (purana path) ki jagah Desktop wale repo ko point karta hai — `pip install -e ".[dev]"` se.

### Verification

- Formatting: 4 files auto-formatted (test_chart_viewport.py, test_probe_qt.py, candle_chart_widget.py, chart_window.py).
- Full gate: ruff ✓, pyright 0 errors ✓, pytest **168 passed** ✓, structure + import validators ✓.
- Git: commit `v1.1.0` tag `v1.1.0`, origin/main pe push.

## 2026-08-07 — Phase 5B Fix: Toolbar Layout Bug (Blank Space Above Chart)

### Kya hua tha?

Timeframe toolbar add karne ke baad chart neeche dhakal gaya tha — toolbar aur chart ke beech ek bada blank white area aata tha. Root cause: `ChartWindow` ke `QVBoxLayout` mein toolbar aur chart widget dono ka vertical policy `Preferred` tha aur **koi stretch factor nahi tha** — Qt ne extra vertical space dono mein barabar baant diya (toolbar 28px hint ke bajaye 380px ho gaya), chart ko neeche push karke.

### Decision

- **`ChartWindow`**: `layout.setStretchFactor(toolbar, 0)` + `layout.setStretchFactor(widget, 1)` — toolbar apni minimum height pe rehta hai, chart baaki sab vertical space leta hai.
- Koi EventBus, Market, SQLite, architecture change nahi — sirf `03_chart/chart/windows/chart_window.py`.
- Koi hardcoded height/margin/spacer nahi — pure Qt stretch.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/windows/chart_window.py` | `setStretchFactor(toolbar, 0)` + `setStretchFactor(widget, 1)` |
| `03_chart/chart/tests/test_chart_window.py` | 2 naye regression tests: toolbar min-height + chart fills remaining space; resize (1200x760 / 1200x1500 / 900x500) pe koi gap nahi |

### Verification

- Geometry probe: 1280x760 → toolbar h=28, chart y=28 h=732, gap=0; 2000x3000 → gap=0. Har size pe chart pure vertical space bharata hai.
- pytest 00_app + 02_market + 03_chart = **162 passed** (2 naye).
- ruff ✓, pyright 0 errors ✓.

## 2026-08-07 — Phase 5C: Load-All Fix (Poori History Dikhana)

### Kya hua tha?

Naya symbol click karne par chart sirf latest **5000 rows** dikhata tha — TCS jaisi stock ki asli SQLite history (61,676 rows, 2016-06-09 → 2026-06-10) truncate ho jaati thi. User ko poora history chahiye: `limit` default ab `None` = "poori available history", explicit `--limit N` hi cap karta hai. Chain event → DB fetch → repository → loader → window → widget ke har level pe `limit: int | None` pass hua.

### Decision

- **Events**: `LoadSymbol.limit` + `TimeframeChanged.limit` ab default `None` (`int | None`) — docstring "None = entire available history".
- **DB layers**: `SqliteCandleDatabase.fetch_candles(symbol, limit)` aur `OhlcvCandleDatabase.fetch_candles(symbol, limit)` — `limit is None` pe **ascending full table scan** (koi DESC + LIMIT subquery nahi). `LIMIT ?` pe `None` bind nahi kiya — SQLite `datatype mismatch` error deta hai (empirically verified).
- **Repositories**: `CandleRepository.get_candles`/`get_candles_timeframe` + `SymbolRepository` same signatures — `None` = full history; aggregation (`get_candles_timeframe`) fir full rows aggregate karke full list return karta hai (koi window-drop nahi).
- **App/window**: `Bootstrap.__init__(data_dir, limit=None)`, `ChartWindow.__init__(..., limit=None)` (pehle `5000` default tha). App `--limit` default `None`.
- **Widget initial viewport**: naya symbol → `_fit_all_count(total)` — poore dataset pe fit (`ceil((total-0.5)/(1-RIGHT_MARGIN_FRACTION))`, min `MIN_VISIBLE_BARS`); first bar index 0 par, latest bar right margin pe. Same-symbol reload → follow-latest `INITIAL_BARS` trailing window. Naya `_log_data_range(model)` log karta hai first/last ts + total + visible window.

### Kya kiya?

| File | Change |
|---|---|
| `02_market/market/events/load_symbol.py`, `timeframe_changed.py` | `limit: int \| None = None` default |
| `02_market/market/database/sqlite.py`, `ohlcv.py` | `fetch_candles(symbol, limit)` — `None` = ascending full scan, koi `LIMIT ?` NULL bind nahi |
| `02_market/market/repository/candle_repository.py`, `symbol_repository.py` | `get_candles`/`get_candles_timeframe` `int \| None` — `None` pe full history / full aggregation |
| `00_app/app/__init__.py`, `app/bootstrap/bootstrap.py`, `03_chart/chart/windows/chart_window.py` | `limit=None` plumbing (default cap `5000` hata diya agar tha) |
| `03_chart/chart/widgets/candle_chart_widget.py` | `_fit_all_count(total)` helper + initial fit-whole-dataset view (first index 0) + `_log_data_range` |
| `02_market/market/tests/{test_loader,test_repository,test_ohlcv}.py` | Naye tests: `limit=None` → saari history |
| `00_app/app/tests/test_main.py` | `--limit` default `None` |
| `03_chart/chart/tests/test_chart_viewport.py` | `test_new_symbol_viewport_spans_entire_history` (500) + `test_new_symbol_large_history_shows_first_candle` (10000) + `_trailing` helper |
| `03_chart/chart/tests/test_overlay_renderer.py` | Un-ended `QPainter` ko `painter.end()` deke shutdown abort fix |
| `03_chart/chart/tests/test_probe_qt.py` | `_paint` return annotation fix (pyright 0 errors) |

### Consequence

- Naya symbol poora 10 saal ka chart kholta hai — pehli bar index 0, latest bar right margin pe.
- Timeframe change `limit=None` pe full aggregation (saare raw rows se).
- Test count: **160 pass** (ruff ✓, pyright 0 errors ✓; test_suite 160 pass).

### Verification

- `pytest 00_app 02_market 03_chart` = **160 passed** (offescreen).
- ruff ✓, format ✓, pyright 0 errors ✓.

## 2026-08-06 — Phase 5A Bug Fix: Bottom Time Label Missing Time

### Kya hua tha?

Bottom crosshair label sirf date dikhata tha — time (HH:MM) missing tha. Root cause: `LabelRenderer.paint_centered` ne multi-line text ko single-line height ke saath size kiya, isliye doosri line (time) crop ho jaati thi. Additionally, format timeframe-aware nahi tha (daily/weekly/monthly ke liye alag format chahiye).

### Decision

- **`LabelRenderer`**: multi-line text (newline-separated) ko properly support karo — `text_h = fm.height() * line_count` + `drawText` with `AlignLeft | AlignTop` so both lines render inside the label rect.
- **`OverlayRenderer._format_timestamp`**: timeframe parameter add kiya — intraday `m`/`h` → `Tue 04 Aug '26\n13:00` (date + time), daily `d` → `Tue 04 Aug '26` (date only), weekly `w` → `Week 32\n2026`, monthly `mo` → `Aug 2026`. Exact timestamp SQLite se read hota hai, koi estimation nahi.
- **`paint_time`**: now takes `timeframe` arg; widget passes `model.timeframe`.

### Kya kiya?

| File | Change |
|---|---|
| `chart/renderer/label_renderer.py` | Multi-line support in all paint methods (`paint`, `paint_left`, `paint_centered`); `drawText` with `AlignLeft | AlignTop` |
| `chart/renderer/overlay_renderer.py` | `paint_time` takes `timeframe`; `_format_timestamp` timeframe-aware (intraday/daily/weekly/monthly) |
| `chart/widgets/candle_chart_widget.py` | Pass `model.timeframe` to `paint_time` |
| `chart/tests/test_overlay_renderer.py` | Updated format tests for timeframe param; added daily/weekly/monthly/intraday tests |
| `chart/tests/test_overlay_integration.py` | Updated `paint_time` mock signature |

### Verification

- ruff ✅, format ✅, pyright 0 errors ✅, structure ✅, imports ✅, **117 tests pass** ✅

### Kya hua tha?

Phase 5A ke labels thhe lekin alignment loose thi — bottom time label right-edge pe fixa tha, right price label fixed Y pe, OHLC neeche ki strip mein tha, symbol info double-line ho sakta tha. UI polish pass: **sirf visual alignment** — EventBus/Market/SQLite/Bootstrap architecture koi bhi change nahi.

### Decision

- **Bottom time label** → `LabelRenderer.paint_centered`: horizontally centered at crosshair X, clamped to axis strip center. Perfectly follows the vertical crosshair line.
- **Right price label** → vertically centered at crosshair Y (clamped to chart rect top/bottom). Exactly aligns with the horizontal crosshair line.
- **OHLC readout** → moved into the top info bar (TradingView style): single line `O H L C +change (+pct%)` placed immediately right of the symbol info label, sharing the same top bar row.
- **Symbol info** → single clean line `SYMBOL • timeframe • exchange` at top-left; OHLC starts at `symbol_rect.right() + 6px` so no overlap.
- **All labels** constrained within `chart_rect` or `axis_rect`; `LabelRenderer` clamps Y positions to prevent clipping/overflow.
- Pens/brushes still class-level cached; overlays only paint when crosshair active (no extra repaint overhead).

### Kya kiya?

| File | Change |
|---|---|
| `chart/renderer/label_renderer.py` | Added `paint_centered` (horizontally centered label); refactored `_draw_label` shared helper; removed unused `text_width` |
| `chart/renderer/overlay_renderer.py` | `paint_symbol_info` returns QRect (for OHLC placement); `paint_ohlc` now single-line in top bar with `left_margin` param; `paint_price` takes crosshair_y + chart_rect (vertical centering); `paint_time` takes crosshair_x (horizontal centering) |
| `chart/widgets/candle_chart_widget.py` | `_paint_overlays` passes crosshair_pos to price/time; OHLC placed at `symbol_rect.right()`; uses top_bar for both symbol + OHLC |
| `chart/tests/test_overlay_integration.py` | Updated all mocks for new signatures; added `test_price_label_y_aligns_with_crosshair`, `test_time_label_x_centers_on_crosshair`, `test_ohlc_positioned_in_top_bar` |
| `chart/tests/test_overlay_renderer.py` | Updated smoke tests for new signatures |

### Verification

- ruff ✅, format ✅, pyright 0 errors ✅, structure ✅, imports ✅, **113 tests pass** ✅
# Development Log — Kya Kab Hua

**Naya entry hamesha upar likho.**

## 2026-08-06 — Phase 5B: Market Timeframes (Detection + Aggregation)

### Kya hua tha?

Chart pe sirf base data (15m) dikhta tha. Ab market engine asli SQLite se **available timeframes detect** karta hai (kabhi hardcode nahi) aur user ke timeframe select par real aggregation karke `DataLoaded` publish karta hai. Scope pakka: **sirf Market + SQLite** — Chart Renderer/Widget/Crosshair/Labels/OHLC sab untouched; koi app reload nahi, sirf data reload.

### Decision

- **Detection (`market/timeframe/`)** — naya pure domain: canonical `TIMEFRAME_LADDER` (candidates, hardcode nahi) + `available_timeframes(base)` = sirf wo entries jo DB ki base-duration ke whole multiple hain ("Only show timeframes that exist"). Hamesha SQLite se detect — koi cached list nahi.
- **Base duration** = histogram (mode) of bar-to-bar deltas from actual rows (`detect_bar_duration`).
- **Aggregation (`aggregate.py`)** — `Rows` ko bucket: intraday = **session-anchored** (session start = mode of first-bar-per-day, partial windows se robust), daily = local midnight, weekly = Monday midnight. OHLCV sab raw rows se: open = first, high = max, low = min, close = last, volume = sum — zero fabrication.
- **Sharding bug + fix**: `_detect_session_start` pehle mode-of-all-rows tha — chhote (partial) windows pe galat session (10:00) nikal aata. Fix: full sample (5000) se `detect_session_start` (first-bar-per-day mode) + windowed fetch (limit+1)×ratio → aggregate → last `limit`. Sirf 2 queries per timeframe change.
- Loading window = `(limit+1) × ratio` raw rows; oldest partial bucket naturally drop hoke `bars[-limit:]` milta hai.
- Naye events: `TIMEFRAME_CHANGED` (request, class `TimeframeChanged`), `ListTimeframes` (request), `TimeframesListed` (result) — exact `ListSymbols`/`SymbolsListed` pattern.
- Subscriptions sirf bootstrap mein (architecture same): `TimeframeChanged` → `MarketDataLoader.on_timeframe_changed`; `ListTimeframes` → naya `TimeframeListLoader.on_list_timeframes`.

### Kya kiya?

| File | Change |
|---|---|
| `02_market/market/timeframe/__init__.py` | Naya package |
| `02_market/market/timeframe/timeframe.py` | Ladder + conversion (`1m..1W` + generated `90m`/`2D`) + `available_timeframes` |
| `02_market/market/timeframe/aggregate.py` | `detect_bar_duration` + `detect_session_start` + `aggregate_bars` |
| `02_market/market/events/timeframe_changed.py` | Naya `TimeframeChanged(symbol, timeframe, limit=5000)` |
| `02_market/market/events/list_timeframes.py` | Naya `ListTimeframes(symbol)` |
| `02_market/market/events/timeframes_listed.py` | Naya `TimeframesListed(symbol, timeframes)` |
| `02_market/market/repository/candle_repository.py` | `detect_bar_duration`, `detect_timeframes`, `get_candles_timeframe` (sample + window fetch) |
| `02_market/market/repository/symbol_repository.py` | `detect_timeframes`, `get_candles_timeframe` (per-stock file open) |
| `02_market/market/loader/market_data_loader.py` | `on_timeframe_changed` → aggregation → `DataLoaded` |
| `02_market/market/loader/timeframe_list_loader.py` | Naya `TimeframeListLoader` → `TimeframesListed` |
| `02_market/market/events/__init__.py`, `loader/__init__.py`, `market/__init__.py` | Exports |
| `00_app/app/bootstrap/bootstrap.py` | 2 naye subscriptions + `timeframe_list_loader` service (sirf wiring) |
| `00_app/app/tests/test_smoke.py` | Service list mein `timeframe_list_loader` |
| `02_market/market/tests/conftest.py` | `seed_ohlcv_intraday_database` helper + fixture |
| `02_market/market/tests/test_timeframe.py` | 12 tests (ladder + availability) |
| `02_market/market/tests/test_timeframe_repository.py` | 12 tests (detection + aggregation OHLCV exactness) |
| `02_market/market/tests/test_timeframe_loader.py` | 4 tests (event flows + failure → nothing) |

### Consequence

- `TIMEFRAME_CHANGED → Market loader → SQLite → DATA_LOADED → Chart updated` — chart pe koi line nahi badli; `DataLoaded` wahi old flow se chart ko update karta hai.
- Real `AMBUJACEM.db` (15m base): detect hota hai `('15m','30m','45m','1h','2h','4h','1D','1W')` — `1m/3m/5m` sahi excluded.
- Aggregated OHLCV raw rows ke SQL merge ke **counter-exact** (14:45 bar: 417.7/418.55/416.6/417.3/607720).

### Verification

- ruff ✓ format ✓ pyright 0 errors ✓ **110 tests pass** (28 naye) ✓ structure + import validators ✓
- Live boot (offscreen): `ListTimeframes` → `TimeframesListed` (8 timeframes); `TimeframeChanged "1D"` → **2475 daily bars** (2016-06-09 → 2026-06-05); `TimeframeChanged "30m"` → 5000 bars, chart widget model ab `('AMBUJACEM', 5000 bars, '30m', ...)` — chart bina touch update ✅

## 2026-08-06 — Phase 5A: Chart UI Labels + Snapping Crosshair

### Kya hua tha?

Crosshair sirf lines dikhata tha — koi candle info, price, timestamp, ya symbol details nahi. Ab crosshair snaps to nearest candle and four floating UI labels follow it: bottom time label (exact timestamp), right price label (current crosshair price), symbol info bar (SYMBOL • timeframe • exchange), and OHLC bar (Open/High/Low/Close + change + %). Scope: **sirf Chart UI** — EventBus, Market Engine, SQLite, Repository, Database, Loader, Bootstrap sab untouched.

### Decision

- **`CrosshairValue`** (model) — frozen dataclass: bar_index, price, timestamp, open, high, low, close. Widget computes it; renderers only consume.
- **`infer_timeframe`** (models/timeframe.py) — pure function: median bar-to-bar gap → "30m", "1h", "1d" etc. ChartEngine populates ChartModel.timeframe from this.
- **`ChartModel`** extended with `timeframe: str` and `exchange: str` — engine populates from bar spacing + default "NSE".
- **`LabelRenderer`** — stateless framed label painter (semi-transparent bg, cached pen/brush). Shared by all overlay labels. Zero paint-time allocation.
- **`OverlayRenderer`** — stateless painter of 4 labels: `paint_symbol_info`, `paint_ohlc`, `paint_price`, `paint_time`. Timestamp formatted as two-line `Tue 04 Aug '26\n10:15`.
- **Crosshair snap** — mouse X → nearest candle slot; vertical crosshair line snaps to that candle's center; horizontal line shows interpolated price at mouse Y; bottom time label + OHLC bar always follow the selected candle.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/models/chart_model.py` | Added `timeframe` + `exchange` fields |
| `03_chart/chart/models/crosshair_value.py` | Naya — frozen CrosshairValue dataclass |
| `03_chart/chart/models/timeframe.py` | Naya — `infer_timeframe()` from median gap |
| `03_chart/chart/models/__init__.py` | Export CrosshairValue + infer_timeframe |
| `03_chart/chart/engine/chart_engine.py` | Populate timeframe (infer) + exchange (default NSE) |
| `03_chart/chart/renderer/label_renderer.py` | Naya — stateless framed label painter |
| `03_chart/chart/renderer/overlay_renderer.py` | Naya — 4 overlay label painters + timestamp formatter |
| `03_chart/chart/renderer/__init__.py` + `chart/__init__.py` | Export LabelRenderer, OverlayRenderer, CrosshairValue, infer_timeframe |
| `03_chart/chart/widgets/candle_chart_widget.py` | Snap crosshair to nearest candle, compute CrosshairValue, paint overlays after candles |
| `03_chart/chart/tests/test_crosshair_renderer.py` | Updated _model() for new ChartModel fields; plot_rect → chart_rect |
| `03_chart/chart/tests/test_crosshair_value.py` | Naya — 2 tests |
| `03_chart/chart/tests/test_timeframe.py` | Naya — 7 tests |
| `03_chart/chart/tests/test_overlay_renderer.py` | Naya — 9 tests |
| `03_chart/chart/tests/test_overlay_integration.py` | Naya — 7 tests |

### Performance

- All label pens/brushes cached at class level — zero allocation per paint call.
- Crosshair snap is O(1) arithmetic (no search).
- Overlay labels only paint when crosshair is active (no crosshair → no overlay paint).
- `_clear_crosshair` only calls `update()` when state actually changed.
- 60 FPS target: paint path unchanged for candles/time-axis; overlays add <1ms.

### Verification

- ruff ok, format ok, pyright 0 errors, **110 tests pass** (25 naye), structure + import validators ✅

### Kya hua tha?

Chart par mouse le jaane se kuch nahi dikhta tha. Ab TradingView-style crosshair hai — mouse position par ek vertical (poori chart height) + ek horizontal (poori width) line, jo smooth follow karti hai aur mouse nikalte hi chhup jaati hai. Scope pakka tha: **sirf crosshair** — EventBus, Market engine, SQLite, candle rendering sab untouched.

### Decision

- Naya **`CrosshairRenderer`** — alag class, `CandleRenderer` mein kuch nahi ghused. Pen class-level cached hai — paintEvent mein **zero allocation**.
- Color: `RGBA(180,180,180,120)` semi-transparent gray, 1px, crisp (AA off — lines, TextAntialiasing waale text ko nahi chhute).
- Widget: `setMouseTracking(True)` + `_crosshair_pos` state. `mouseMoveEvent` pe update (pan logic waise hi chalta hai), `leaveEvent` pe hide.
- Render order: candles → time axis → crosshair (always last, requirement 4).
- Lines plot area tak limit hain — axis strip (labels) mein mouse jaye to crosshair hidden (TradingView jaisa).
- Redraw sirf `update()` — koi reload nahi, 60 FPS safe.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/renderer/crosshair_renderer.py` | Naya! Stateless `CrosshairRenderer` — cached pen, `paint(painter, position, plot_rect)` |
| `03_chart/chart/widgets/candle_chart_widget.py` | Mouse tracking, `_crosshair_pos` state, move/leave handlers, paintEvent mein render (candles ke baad) |
| `03_chart/chart/renderer/__init__.py` + `chart/__init__.py` | `CrosshairRenderer` export |
| `03_chart/chart/tests/test_crosshair_renderer.py` | Naya! 7 tests |

### Testing battles (worth remembering)

- Widget pe `grab()` offscreen platform mein **unreliable** — hidden widget pe crash (0xC0000005), shown widget pe flaky (partial transparent image), `render()` pe bhi transparent. Sirf event-loop + top-level window grab stable. Isliye widget tests **pixel-free**: state transitions + `CrosshairRenderer.paint` monkeypatch (recording args). Renderer pixel-exact tests QImage pe hain (449 → 448 = intersection double-blend wala 1 pixel).
- Pan test pehle fail: 200 bars mein right edge par pan space nahi tha (clamp), aur `_mouse_event` press mein button arg galat tha (NoButton) — dono fixed.
- `QWidget` bina `QApplication` create karo to hard crash — widget tests mein `_app()` helper zaroori.

### Verification

- ruff ok, format ok, pyright 0 errors, **59 tests pass** (7 naye), structure + import validators ✅
- Standalone window-grab pixel check: 795 diffs = 296 (plot height) + 500 (width) − 1, **sab exactly crosshair lines par**, leave ke baad pixel-perfect baseline
- Live boot `D:\ZerodhaTradingData` (AMBUJACEM): crosshair (400,150) → (100,260) follow, leave → hidden, pan intact ✅

## 2026-08-06 — Phase 3: TradingView-style Time Axis

### Kya hua tha?

Chart ka X-axis (time axis) sirf khali strip thi. Ab TradingView style adaptive date/time labels aaye — zoom ke hisaab se format aur tick spacing automatically badalti hai. Scope pehle se pakka tha: **sirf X-axis**, EventBus/Market/Chart engine/database kuch nahi chhuna.

### Decision

- Naya **`TimeAxisRenderer`** class — stateless QPainter, `CandleRenderer` jaisa style. `CandleRenderer` ke andar kabhi nahi ghuse (single responsibility).
- Widget ne 24px strip reserve ki (chart + volume ke neeche) — `_chart_rects()` ab 3 rects deta hai.
- Tick step = "sabse chhota step jiske spacing >= 96px" — 1/2/5×10^k calendar-friendly ladder (15min, 1h, 1d, 1w, 1M, 1Y...).
- Ticks **calendar-aligned**: intraday = local clock grid, daily = midnight, weekly = Monday(simplified: 7d), monthly = month start, yearly = Jan 1.
- Format ladder zoom se: `10:15` → `10 Apr` → `Apr` → `Apr 2024` → `2024`.
- Labels kabhi overlap nahi hote: pixel-gap guard (min 96px ya label width) + gridline/label at tick center.
- Perf: paint mein sirf ~2 timestamp parses + binary search per tick (O(log n)), bahar ke lakhon candles touch nahi hote — 60 FPS safe.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/renderer/time_axis_renderer.py` | Naya! Stateless `TimeAxisRenderer` — `select_step()`, `format_for_step()`, `tick_times()`, `paint()` (+ `_nearest_bar` binary search) |
| `03_chart/chart/widgets/candle_chart_widget.py` | `TIME_AXIS_HEIGHT=24`; `_chart_rects()` → 3 rects; paintEvent mein axis render; wheel/drag unpack fix |
| `03_chart/chart/renderer/__init__.py` | `TimeAxisRenderer` export |
| `03_chart/chart/__init__.py` | `TimeAxisRenderer` export |
| `03_chart/chart/tests/test_time_axis_renderer.py` | Naya! 11 tests |

### Details worth remembering

- First bug: sub-day ticks epoch (UTC) aligned the gaye — IST (+5:30) pe ugly times (08:30, 11:30...) ban gaye aur gaps 90px tak gir gaye. Fix: **local-clock alignment** (`seconds_since_midnight % step`).
- Second bug: antialiased 1px lines half-pixel pe — gridlines blend hoke mil gaye. Fix: shapes ke liye `Antialiasing=False` (crisp 1px), text ke liye `TextAntialiasing=True` — 0 blended pixels.
- Test expectation: 3-day step weekly tier mein hain (`%b`), 2-day tak daily (`%d %b`).

### Verification

- ruff ok, format ok, pyright 0 errors, **52 tests pass** (11 naye), structure + import validators ✅
- Live boot `D:\ZerodhaTradingData` (offscreen pixel census): labels render (792 text px), 6 crisp gridlines at 169-176px spacing (>= 96 floor), 0 blended pixels

## 2026-08-06 — Phase 2: Real Zerodha Databases + Stock Sidebar

### Kya hua tha?

Phase 1 sample DB (`data/vayren.db`, SPY) se chalta tha — ek file, ek symbol. Ab asli data aaya:

| Asli data | Detail |
|---|---|
| Location | `D:\ZerodhaTradingData` — 527 SQLite files, har stock ka apna DB |
| Schema | Table `ohlcv` (`candle_time` PK, open, high, low, close, volume) — `candles` table nahi, `symbol` column nahi |
| Requirement | Startup pe folder scan → sidebar mein stocks → click → load → chart. Koi preload nahi |

### Decision

Market engine (`MarketDataLoader`) aur Chart engine (`ChartEngine`) **rewrite nahi kiye** — contract vahi rakha, nayi cheezein add ki:

- Ek naya DB adapter jo asli schema padhta hai — `CandleRepository` vahi mapping karta hai (row → Bar)
- Ek naya repository jo per-stock file discover/load karta hai
- Ek naya event pair: `ListSymbols` (request) / `SymbolsListed` (result)
- Sidebar = naya widget (`SymbolListWidget`), window mein splitter layout

### Kya kiya?

| File | Change |
|---|---|
| `02_market/market/database/ohlcv.py` | Naya `OhlcvCandleDatabase` — asli Zerodha schema (`ohlcv`/`candle_time`) |
| `02_market/market/repository/symbol_repository.py` | Naya `SymbolRepository` — `list_symbols()`, `get_candles()` (per-stock file) |
| `02_market/market/events/list_symbols.py` | Naya `ListSymbols` request event |
| `02_market/market/events/symbols_listed.py` | Naya `SymbolsListed` result event |
| `02_market/market/loader/symbol_list_loader.py` | Naya `SymbolListLoader` — scan → publish |
| `02_market/market/repository/candle_repository.py` | Type widening: `SqliteCandleDatabase \| OhlcvCandleDatabase` |
| `02_market/market/loader/market_data_loader.py` | Type widening: `CandleRepository \| SymbolRepository` |
| `03_chart/chart/widgets/symbol_list_widget.py` | Naya sidebar widget (Qt signal `symbol_selected`) |
| `03_chart/chart/windows/chart_window.py` | Splitter layout (sidebar + chart), title `VAYREN — SYMBOL`, click → `LoadSymbol` |
| `00_app/app/bootstrap/bootstrap.py` | `data_dir` based; 7 subscriptions (3 naye); window show at start |
| `00_app/app/lifecycle/lifecycle.py` | `AppStarted` → `ListSymbols` (preload hata diya) |
| `00_app/app/__init__.py` | `--symbol`/`--db` hata; `--data-dir` (default `D:\ZerodhaTradingData`) |

### Naya event flow

```
AppStarted → ListSymbols → SymbolsListed → (sidebar filled)
click AMBUJACEM → LoadSymbol → DataLoaded → ChartReady → VAYREN — AMBUJACEM
click BPCL      → LoadSymbol → DataLoaded → ChartReady → VAYREN — BPCL
```

### Consequence

- Har click pe sirf usi stock ka DB khulta hai — koi preload nahi, koi memory waste nahi
- Chart engine, EventBus, folder structure — sab untouched
- Purana `SqliteCandleDatabase` (sample schema) apni jagah hai — tests usi pe chalte hain

### Verification

- `make check` equivalent: ruff ✓, pyright 0 errors ✓, **41 tests pass** ✓, structure + import validators ✓
- Live boot `D:\ZerodhaTradingData`: 527 symbols listed; click AMBUJACEM → `VAYREN — AMBUJACEM`; click BPCL → `VAYREN — BPCL`; wapas AMBUJACEM ✓

---

## 2026-08-06 — Repository Refactor: Charting Platform Ki Neev

### Kya hua tha?

Repository ek 13-chapter trading scaffold thi — signals, strategies, risk, execution, portfolio... sab kuch tha, par:

| Problem | Detail |
|---|---|
| GUI nahi tha | Na Qt, na tkinter — kuch nahi |
| Chart nahi tha | Koi rendering code nahi |
| SQLite nahi tha | Na file, na schema, na code |
| Scope bahut bada | 13 modules, koi bhi complete nahi |

### Decision

Naya architecture — **numbering = startup flow**:

```
00_app → 01_core → 02_market → 03_chart
```

Future modules fixed: `04_indicator … 14_plugin`.

Permanent rules bani (`90_brain/project_rules.md`): event-driven only, one module one responsibility, no circular dependencies, no placeholder code.

### Kya kiya?

| Kaam | Detail |
|---|---|
| Purane chapters archive | `git mv` → `99_archive/` (history safe) |
| `EventBus` nikala | `02_platform` → `01_core/core/event_bus/` (verbatim) |
| Logger nikala | `lib/logging` → `01_core/core/logger/` |
| `Registry` nikala | → `01_core/core/registry/` (instances ke liye generalize kiya) |
| `Bar` nikala | → `02_market/market/models/bar.py` (verbatim) |
| `02_market` banaya | `SqliteCandleDatabase` → `CandleRepository` → `MarketDataLoader`; events `LoadSymbol`/`DataLoaded` |
| `03_chart` banaya | `ChartModel`, `ChartEngine`, `CandleRenderer`, `CandleChartWidget` (zoom/pan/resize), `ChartWindow`; events `ChartReady`/`WindowRendered` |
| `00_app` banaya | `App` (args, QApplication, Qt loop), `Bootstrap` (sirf subscription site), `AppLifecycle` |
| Tooling | `pyproject.toml` trim → sirf PySide6 + dev tools; `Makefile`; validators; AGENTS.md/README |

### Consequence

- Synchronous bus → poora chain `Bootstrap.start()` mein — deterministic, test easy
- Wiring ek jagah → event topology ek file mein dikhti hai
- Archive packaging/pyright/pytest/validators se bahar

### Verification

Live boot `python -m app --symbol SPY --db data/vayren.db --limit 1200` (sample DB, 1200 bars):

```
Application started → Loaded 1200 candles for SPY
→ Chart ready for SPY → Chart window shown → Window rendered
```

Qt loop running raha. Ek fix laggi: `__main__.py` ko `sys.argv[1:]` dena padta hai (`python -m app` argv[0] = module path).

**Compliance:** `make check` green — ruff, pyright (0 errors), 26 tests pass, structure + import validators pass.

---

## Template — Naye Entry Ke Liye

```markdown
## YYYY-MM-DD — <Kya hua>

### Kya hua tha?        → context
### Decision            → kya decide hua
### Kya kiya?           → changes ki list
### Consequence         → isse kya asar hua
### Verification        → kaise check kiya
```

> Har session ka record. 6 mahine baad koi padhe — sab samajh aaye.
