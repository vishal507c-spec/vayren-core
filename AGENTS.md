# AGENTS.md — AI Agent ke Liye Rules

> **AI-FIRST:** `AGENTS.md → ARCHITECTURE_CONSTITUTION.md → relevant 90_brain doc → module → code`. AI ko har MD padhne ki zaroorat nahi — ye path enough hai.

**Owns:** AI workflow, coding standards, naming, forbidden, validation. **Not owns:** Language ownership → `ARCHITECTURE_CONSTITUTION.md`; detailed boundaries/events/contracts/state → `90_brain/architecture.md`, `module_contracts.md`, `event_catalog.md`, `ai_memory.md`.
**When to read:** ALWAYS first, before any code change. **Related:** `ARCHITECTURE_CONSTITUTION.md` (languages), `90_brain/` (contracts/state).

## Pehle Padho, Phir Code Karo

Ye repository ka **Brain** `90_brain/` folder mein hai. AI agent ko code chhune se pehle ye sab padhna **zaroori** hai:

| File | Isme kya hai |
|---|---|
| `../ARCHITECTURE_CONSTITUTION.md` (repo root) | **SABSE UPAR** — Rust/Python/egui ownership, migration rules (har decision par lagega) |
| `architecture.md` | Module map, layers, event flow, future modules |
| `event_catalog.md` | Events + owner + payload |
| `module_contracts.md` | Har module ka public API + SQLite schema |
| `ai_memory.md` | Abhi kya state hai, kya baaki hai |
| `development_log.md` | Kis din kya hua |

> `ARCHITECTURE_CONSTITUTION.md` language ownership ka single source hai — Rust→Core/Perf, Python→Strategy/AI, Rust+egui→UI. Naya code wahi se decide karo. Constitution duplicate mat karo, reference karo.

Code badalne ke baad **dono** update karo: `development_log.md` aur `ai_memory.md`.

**Standards (consolidated):** Legacy `project_rules`/`coding_standards`/`naming_conventions`/`roadmap` docs → ye file + `90_brain/architecture.md` mein merge ho chuke hain. Detail neeche.

## Repository Structure

Numbered chapters = **development story order** (numbers organizational hain, strict dependency nahi):

```
00_app/   app/     bootstrap, lifecycle, entry point      depends on: core, data, market, chart
01_core/  core/    EventBus, events, logger, registry     depends on: kuch nahi
02_data/  data/    historical download engine (write)     depends on: core
03_market/ market/ SQLite candles (database→repository→loader)  depends on: core
04_chart/ chart/   ChartEngine, renderer, widgets, windows  depends on: core, market
90_brain/  (docs) permanent knowledge
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

> Detail: `on_<event>` → bootstrap subscribe karta hai, seedha call kabhi nahi. `set_model(model)` → widget ko data milta hai, bus nahi. Layers ke naam = ek kaam: `database` SQL, `repository` rows→Bar, `loader` event, `engine` model, `renderer` painting, `widgets` viewport, `windows` host.

## Standards — Code Kaise Likhna Hai (Consolidated from 90_brain)

**Tools:** Python 3.11+, `ruff format` (100 cols, double quotes), `ruff check` (rules `E,F,I,N,W,UP,B,SIM,ARG,C4,T10`), `pyright` 0 errors, `make check` = gate (lint+format+typecheck+test+validators) — pass hone se pehle merge nahi.

**Structure:**
```
NN_chapter/chapter/           # package, __init__.py public API
    models/ | events/ | database/ | repository/ | loader/ | engine/ | renderer/ | widgets/ | windows/
    tests/test_*.py           # har module ke saath, har naye file ka test chahiye
```
- Chapter folder mein `__init__.py` nahi. Tests `NN_name/name/tests/test_x.py`. Har public function mein type hints, har model/event frozen dataclass (immutable).

**Classes:** One responsibility (docstring mein 2 verbs → split), constructor injection (dependencies bahar se), frozen dataclass for models/events.

**UI Rules:**
```
Widgets: model lete hain, EventBus/SQL/loading kabhi nahi
Painting: sirf renderer mein, event handler mein nahi
Windows: terminal event publish kar sakte hain, subscribe sirf bootstrap mein
Business logic kabhi UI mein nahi
```

**Events:** Frozen dataclass `Event` base se, payload sirf data (strings, numbers, tuples of models) — kabhi connection/widget/callable nahi. Module ko kaam karwana → event bhejo.

**Error Handling:** Bus handler fail → log, app chalta rahe. Services → log+return, UI mein raise nahi. Database contract toota → raise. Loader raise ko log mein badalta hai.

**Black List (mana hai):**
```
from x import *  ❌
from ..market import ...  ❌ (relative cross-module)
from market.database import ...  ❌ (dusre module ka internal)
TODO/FIXME / dead code / mock logic / sample trading logic  ❌
```

**One Module = One Responsibility:** Naya kaam → naya module (`05_strategy`...), purana expand nahi. Trading logic chart mein nahi, market sirf store, chart sirf dikhata hai. Har public class ka ek clear kaam + docstring.

## Workflow

```
□ 90_brain/ padho (architecture, event_catalog, module_contracts, ai_memory) + ARCHITECTURE_CONSTITUTION.md
□ Feature ka target language/module decide karo (CONSTITUTION §9) → naya code target mein; directly related legacy slice usi feature mein migrate (CONSTITUTION §5, §17)
□ Module contracts ke hisaab se implement karo
□ Coding-time forensics: `python scripts/forensics/__main__.py mark --phase PHASE --action ...` evidence do,
  `run --phase PHASE -- <cmd>` lambi commands wrap karo; task ka session apne aap open/close hota hai
□ make check chalao (lint + format + typecheck + test + validators)
□ Task end par LAST command: `python scripts/forensics/__main__.py report --name "..."` (report auto-append bhi hota hai next task par)
□ 90_brain/development_log.md aur 90_brain/ai_memory.md update karo
```

## Language & Migration — Constitution Reference (Invisible Feature-Driven)

**Naya code banate waqt pucho:**
```
1. Kya bana raha hu? 2. Kaunsi responsibility? 3. Kaunsa domain? 4. Kaunsi language?
→ Core/Perf/Market/Data/Indicator/Risk/Execution/Backtest → Rust
→ Strategy/AI/Research → Python
→ Native UI → Rust+egui
```
**Core rule (per `ARCHITECTURE_CONSTITUTION.md` §5, §13, §17):**
```
NEW FEATURE → target language mein implement
  → directly related legacy slice identify karo
  → usi feature mein quietly migrate (smallest useful slice)
  → integrate → test → validate → finish
```
- **Invisible:** Feature request hi migration ka context hai — 5 sawal (legacy hai? safely move? directly related? scope bina badhaye? behavior preserve?) → YES toh migrate karo.
- **Unrelated mat chhuno:** Sirf feature se directly required/blocking/adjacent code. Koi repo-wide rewrite nahi.
- **No migration debt:** Naya feature kabhi legacy mein mat banao jab target already defined hai.
- No big-bang, no new language bina approval. Detail: `CONSTITUTION` §5, §8, §13, §14.

## Commit Style (from CONTRIBUTING, consolidated)

Conventional commits, ek commit = ek kaam: `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`
```
feat: add candle zoom for the chart widget
fix: correct bar timestamp ordering in the repository
```
Public API badla → README update karo, tests saath mein, `make check` pass kiye bina commit nahi.

## Forbidden

- `from x import *`, relative cross-module, dusre module ka internal import
- Widget ke andar EventBus/SQL, loader ke andar drawing, UI ke andar business logic
- Placeholder / mock / sample trading logic, TODO/FIXME, dead code
- Big-bang migration, naya language/framework bina approval

## Commands

| Command | Kaam |
|---|---|
| `make setup` | Sab kuch install karo |
| `make dev` | Charting app kholo (`python -m app`) |
| `make check` | Poora verification gate |
| `make format` | Formatting auto-fix |

## Ek Line Mein

> Pehle Brain padho, code event-driven rakho, kaam ke baad Brain update karo — bas ye 3 baatein.
