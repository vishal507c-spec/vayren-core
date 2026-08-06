# Development Log — Kya Kab Hua

**Naya entry hamesha upar likho.**

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
