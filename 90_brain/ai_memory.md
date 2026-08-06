# AI Memory — Abhi Kya State Hai

**Last update:** 2026-08-06 (refactor session)

## 1. Ye kya hai?

Ye document AI ko batata hai ki **abhi platform kahan hai** — kya bana, kya baaki. Naya session shuru karte hi ye padho.

## 2. Cheezein Kahaan Hain

| Module | Chapter | Andar kya hai |
|---|---|---|
| app | `00_app/app/` | `App`, `Bootstrap`, `AppLifecycle`, `__main__.py` |
| core | `01_core/core/` | `EventBus`, `Event`, `AppStarted`, logger, `Registry` |
| market | `02_market/market/` | `Bar`, `SqliteCandleDatabase`, `CandleRepository`, `MarketDataLoader`, `LoadSymbol`, `DataLoaded` |
| chart | `03_chart/chart/` | `ChartModel`, `ChartEngine`, `CandleRenderer`, `CandleChartWidget`, `ChartWindow`, `ChartReady`, `WindowRendered` |
| brain | `90_brain/` | Permanent knowledge — code chhune se pehle padho |
| archive | `99_archive/` | Purane modules, sirf reference — kabhi import nahi |

## 3. Verified Facts (is session mein pakke kiye)

| Fact | Detail |
|---|---|
| Bus synchronous hai | Poora chain `Bootstrap.start()` mein hi khatam ho jata hai |
| Dispatch exact type se | `type(event)` match — subclass nahi |
| Subscriptions ek jagah | Sirf `00_app/app/bootstrap/bootstrap.py` |
| `Bar.timestamp` string hai | ISO-8601 — lexicographic sort sahi chalta hai |
| SQLite contract | `data/vayren.db` + `candles` table (schema `module_contracts.md`) |
| Path override | `--db` arg ya `VAYREN_DB` env; `data/` gitignored |
| Sample DB | `scripts/seed_sample_db.py` se bana — asli database aane par replace karo |
| Python | Local 3.11; `requires-python >= 3.11` |
| Validators | 4-module map enforce; `99_archive` + `90_brain` excluded |

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
| Asli `data/vayren.db` | Abhi nahi aaya — sample DB (1200 SPY bars) khada hai. Apni file wahan daalo (schema `module_contracts.md`) |
| Zoom/pan/resize ka visual QA | Code offscreen smoke test se verified — asli desktop session par ek baar haath se check karo |

## 6. Known Oddities (Koi Problem Nahi)

| Cheez | Detail |
|---|---|
| Offscreen Qt warnings | `propagateSizeHints`, font directory — sirf headless mein aate hain, desktop par nahi |
| `python -m app` quirk | `__main__.py` `sys.argv[1:]` slice karta hai; `vayren` console script mein ye problem nahi |

## 7. Future

Naya kaam shuru karo toh pehle `roadmap.md` dekho — kaunsa phase, kaunsa chapter.

> Brain state bataata hai: kya bana, kahan hai, kya baaki. Update karte rehna.
