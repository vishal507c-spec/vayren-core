# AGENTS.md — AI Agent ke Liye Rules

## Pehle Padho, Phir Code Karo

Ye repository ka **Brain** `90_brain/` folder mein hai. AI agent ko code chhune se pehle ye sab padhna **zaroori** hai:

| File | Isme kya hai |
|---|---|
| `project_rules.md` | Pakke rules — kabhi mat todo |
| `architecture.md` | Module map, layers, event flow |
| `event_catalog.md` | 5 events aur unke owner |
| `module_contracts.md` | Har module ka public API + SQLite schema |
| `coding_standards.md` | Code kaise likhna hai |
| `naming_conventions.md` | Naam kaise rakhne hain |
| `roadmap.md` | Platform kahan jaa raha hai |
| `ai_memory.md` | Abhi kya state hai, kya karna baaki hai |
| `development_log.md` | Kis din kya hua |

Code badalne ke baad **dono** update karo: `development_log.md` aur `ai_memory.md`.

## Repository Structure

Numbered chapters = **development story order** (numbers organizational hain, strict dependency nahi):

```
00_app/   app/     bootstrap, lifecycle, entry point      depends on: core, data, market, chart
01_core/  core/    EventBus, events, logger, registry     depends on: kuch nahi
02_data/  data/    historical download engine (write)     depends on: core
03_market/ market/ SQLite candles (database→repository→loader)  depends on: core
04_chart/ chart/   ChartEngine, renderer, widgets, windows  depends on: core, market
90_brain/  (docs) permanent knowledge
99_archive/ (retired modules — kabhi import mat karo)
```

Aage ke modules usi order mein: `05_strategy, 06_backtest, 07_risk, 08_execution, 09_portfolio` (phir `10_scanner, 11_indicator, 12_drawing, 13_replay, 14_workspace, 15_plugin`).

## Architecture Rules

- **Event-driven only.** Har module sirf EventBus se baat karta hai. Seedha call — mana hai.
- **Subscriptions sirf `00_app/app/bootstrap/bootstrap.py` mein.** Widgets kabhi EventBus chhunte nahi.
- **One module = one responsibility.** Naya feature = naya module. Purana kabhi expand nahi hota.
- **No circular dependencies.** Import hamesha numbering ke neeche ki taraf.
- **Layers alag-alag:** UI mein SQL nahi, loader mein drawing nahi, UI mein business logic nahi.
- **No placeholder code. No mock logic. No sample trading logic.**

## Import Rules

```python
# ✅ SAHI
from core.event_bus import EventBus
from core.events import AppStarted
from market import Bar, MarketDataLoader, LoadSymbol, DataLoaded
from chart import ChartEngine, ChartWindow, ChartModel, ChartReady
from .services.order_manager import OrderManager   # apne module ke andar

# ❌ GALAT
from market.database.sqlite import SqliteCandleDatabase   # market ke andar ki cheez, bahar se nahi
from ..market import Bar                                    # relative cross-module
from chart import *                                          # star import
```

## Naming Conventions

| Cheez | Rule | Example |
|---|---|---|
| Chapters | Numbered, lower_snake | `00_app`, `04_chart` |
| Packages/files | snake_case, singular | `market/`, `candle_repository.py` |
| Classes | PascalCase | `CandleChartWidget` |
| Events | Past tense, PascalCase | `DataLoaded`, `ChartReady` |
| Requests | Imperative, PascalCase | `LoadSymbol` |
| Handlers | `on_<event>` | `on_load_symbol` |

## Workflow

```
□ 90_brain/ padho (project_rules, architecture, event_catalog, module_contracts, coding_standards, naming_conventions, ai_memory)
□ 99_archive/ sirf reference ke liye dekho
□ Module contracts ke hisaab se implement karo
□ Coding-time forensics: `python scripts/forensics/__main__.py mark --phase PHASE --action ...` evidence do,
  `run --phase PHASE -- <cmd>` lambi commands wrap karo; task ka session apne aap open/close hota hai
□ make check chalao (lint + format + typecheck + test + validators)
□ Task end par LAST command: `python scripts/forensics/__main__.py report --name "..."` (report auto-append bhi hota hai next task par)
□ 90_brain/development_log.md aur 90_brain/ai_memory.md update karo
```

## Commands

| Command | Kaam |
|---|---|
| `make setup` | Sab kuch install karo |
| `make dev` | Charting app kholo (`python -m app`) |
| `make check` | Poora verification gate |
| `make format` | Formatting auto-fix |

## Ek Line Mein

> Pehle Brain padho, code event-driven rakho, kaam ke baad Brain update karo — bas ye 3 baatein.
