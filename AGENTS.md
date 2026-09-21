# AGENTS.md — AI Agent ke Liye Rules

> **AI-FIRST:** `AI_ENTRY.md → module → code` (startup: entry + current `90_brain/ai_memory.md` only).
> Full rules live below; canonical docs are linked from the entry — read a doc only when the entry routes you there.

**Owns:** AI workflow, coding standards, naming, forbidden, validation. **Not owns:** Language ownership → `AI_ENTRY.md` §1 (machine: `90_brain/ownership_policy.json`); detailed boundaries/events/contracts/state → `90_brain/architecture.md`, `module_contracts.md`, `event_catalog.md`, `ai_memory.md`.
**When to read:** ALWAYS first, before any code change. **Related:** `AI_ENTRY.md` (entry + language map), `90_brain/` (contracts/state).

## Pehle Padho, Phir Code Karo

Ye repository ka **Brain** `90_brain/` folder mein hai. AI agent ko code chhune se pehle ye sab padhna **zaroori** hai:

| File | Isme kya hai |
|---|---|
| `AI_ENTRY.md` §1 (repo root) | **SABSE UPAR** — Rust/Python/Slint ownership, fixed rules (har decision par lagega) |
| `architecture.md` | Module map, layers, event flow, future modules |
| `event_catalog.md` | Events + owner + payload |
| `module_contracts.md` | Har module ka public API + SQLite schema |
| `ai_memory.md` | Abhi kya state hai, kya baaki hai |

> `AI_ENTRY.md` §1 language ownership ka single source hai — Rust→Core/Perf, Python→Strategy/AI, Rust+Slint→UI. Naya code wahi se decide karo. Ownership duplicate mat karo, reference karo.
>
> **FAST PATH (default):** `AI_ENTRY.md` se route mil jaye to poora `90_brain/` har task par mat padho — entry jahan bheje (module + contract + policy), wahi padho. Neeche wali table full-reference list hai, har-task checklist nahi.

Code badalne ke baad `ai_memory.md` update karo.

**Standards (consolidated):** Legacy `project_rules`/`coding_standards`/`naming_conventions`/`roadmap` docs → ye file + `90_brain/architecture.md` mein merge ho chuke hain. Detail neeche.

## Repository Structure

Numbered chapters = **development story order** (numbers organizational hain, strict dependency nahi):

```
00_app/   app/     composition (headless backend + services)   depends on: all chapters (partly unwired — see ai_memory.md)
01_core/  core/    Event marker, AI guardrails, native loader  depends on: stdlib only
02_data/  data/    provider SDK boundary, settings, bridge     depends on: core, broker
03_market/ market/ Bar vocabulary + native bridges             depends on: core
05_strategy/ strategy/ registry, runtime, research, lab        depends on: core, market
06_backtest/ backtest/ native bridges only (Rust owns engine)  depends on: — (via FFI)
07_risk/   risk/   native bridges only (Rust owns engine)      depends on: — (via FFI)
08_execution/ execution/ AI-adjacent logic + bridges (Rust owns core)  depends on: — (via FFI)
09_broker/ broker/ registry, selection, vocab (UBL)            depends on: stdlib only
rust/     kernels (vayren-core, std-only) + Slint shell       depends on: std / Slint
90_brain/  (docs) knowledge — ai_memory.md active, current state only
```

Aage ke modules usi order mein: `05_strategy, 06_backtest, 07_risk, 08_execution, 09_portfolio` (phir `10_scanner, 11_indicator, 12_drawing, 13_replay, 14_workspace, 15_plugin`).

## Architecture Rules

- **Event-driven only (target wiring).** Modules talk via public surface + bridge/snapshot only — direct internal calls forbidden. (Python bus subscription is future rewire; current mechanism status: `ai_memory.md`.)
- **Single composition root.** `00_app` (+ Rust shell) owns wiring. UI layers never touch bus/SQL/loading.
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

# (event-bus/loader/chart rows = target-wiring vocabulary; current live names:
#  core.Event, market.Bar, broker.* UBL, strategy.* — see AI_ENTRY routing)
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
□ 90_brain/ padho (architecture, event_catalog, module_contracts, ai_memory) + AI_ENTRY.md §1 (ownership)
□ Feature ka target language/module decide karo (AI_ENTRY.md §1) → naya code target mein; directly related legacy slice usi feature mein migrate (AI_ENTRY.md §1 fixed rules)
□ Module contracts ke hisaab se implement karo
□ Coding-time forensics: `python scripts/forensics/__main__.py mark --phase PHASE --action ...` evidence do,
  `run --phase PHASE -- <cmd>` lambi commands wrap karo; task ka session apne aap open/close hota hai
□ make check chalao (lint + format + typecheck + test + validators)
  ↳ Fast path (measured): iterate par pehle impact-scope tests (`pytest <touched-module>/tests`, ~5s),
    phir full gate (~105s: pytest ~82s + pyright ~23s). Benchmarks: `python scripts/benchmark.py gate|record|scoreboard`.
□ Task end par LAST command: `python scripts/forensics/__main__.py report --name "..."` (report auto-append bhi hota hai next task par)
□ 90_brain/ai_memory.md update karo
```

## Language & Migration — Ownership Reference (Invisible Feature-Driven)

**Naya code banate waqt pucho:**
```
1. Kya bana raha hu? 2. Kaunsi responsibility? 3. Kaunsa domain? 4. Kaunsi language?
→ Core/Perf/Market/Data/Indicator/Risk/Execution/Backtest → Rust
→ Strategy/AI/Research → Python
→ Native UI → Rust+Slint
```
**Core rule (per `AI_ENTRY.md` §1 fixed rules + language map):**
```
NEW FEATURE → target language mein implement
  → directly related legacy slice identify karo
  → usi feature mein quietly migrate (smallest useful slice)
  → integrate → test → validate → finish
```
- **Invisible:** Feature request hi migration ka context hai — 5 sawal (legacy hai? safely move? directly related? scope bina badhaye? behavior preserve?) → YES toh migrate karo.
- **Unrelated mat chhuno:** Sirf feature se directly required/blocking/adjacent code. Koi repo-wide rewrite nahi.
- **No migration debt:** Naya feature kabhi legacy mein mat banao jab target already defined hai.
- No big-bang, no new language bina approval. Detail: `AI_ENTRY.md` §1 (fixed rules + language map).

## Language Enforcement — Machine-Checked (NO silent skip)

Migration rules sirf instructions nahi — `make check` / CI mein **hard gate** se enforce hote hain:

- **Policy:** `90_brain/ownership_policy.json` = machine-readable mapping (`file → domain → required language`). Pehle-match order mein evaluate hota hai. Naya rule add karo toh specific paths general parent se PEHLE rakho.
- **Validator:** `python scripts/validate_language_ownership.py` — Rust-owned domain mein har Python file ko `90_brain/language_retention.json` mein per-file entry chahiye (state + reason + migration_target + migration_condition). Bina entry = **HARD FAIL**, chahe file baseline mein ho.
- **Baseline = history only:** `90_brain/language_baseline.json` ko validator kabhi read nahi karta. Baseline mein hona koi exemption nahi deta.
- **States:** `MIGRATED` (file gayab honi chahiye) / `MIGRATION_REQUIRED` (active legacy, touch par migrate) / `TEMPORARILY_RETAINED` (justified, tracked) / `EXEMPT_WITH_JUSTIFICATION` (permanent, proof ke saath). Blanket domain-level exemption ka koi effect nahi.
- **Feature flow:** Rust-owned file ko touch karo → usi feature mein related slice migrate karo → retention entry update karo → `validate_language_ownership.py` PASS hona chahiye. Sirf tests pass hona enough nahi.
- **"Smallest useful slice" = minimum migration scope, migration skip karne ka excuse nahi.** Slice bada lage toh scope feature tak limited rakho, lekin Python mein naya wrong-language code mat likho — validator fail karega.

## Commit Style

Conventional commits, ek commit = ek kaam: `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`
```
feat: add candle zoom for the chart widget
fix: correct bar timestamp ordering in the repository
```
Public API badla → README update karo, tests saath mein, `make check` pass kiye bina commit nahi.

## GitHub Push — Hard Rule (NON-NEGOTIABLE)

**Bina user ke explicit command ke GitHub par KUCH bhi push/release/tag/PR nahi.**

- **LOCAL ≠ REMOTE, COMMIT ≠ PUSH, VERSION ≠ TAG, TAG ≠ RELEASE** — ek operation dusre se permission nahi deta
- Explicit permission hi push kara sakti hai: "GitHub par push karo", "main par push karo", "changes push karo", "commit aur push karo", "version release karo" (release workflow explicitly manga ho)
- Tests pass / task complete / commit bana / version update — **ye koi bhi push permission NAHI hai**
- Ambiguous commands ("kar do", "update kar do", "save kar do", "complete kar do") → **sirf local rakho**; agar remote intent possible ho to pucho: "GitHub par push karna hai ya sirf local changes rakhne hain?"
- Local work allowed: edit, tests, builds, validation, git status/diff, local commits (jab task appropriate ho)
- Push karte waqt: branch check → git status → git diff review → sirf requested changes push → **force-push kabhi nahi** (jab tak user explicitly na maange), branch switch/merge push ke liye nahi

## Forbidden

- `from x import *`, relative cross-module, dusre module ka internal import
- Widget ke andar EventBus/SQL, loader ke andar drawing, UI ke andar business logic
- Placeholder / mock / sample trading logic, TODO/FIXME, dead code
- Big-bang migration, naya language/framework bina approval
- **Bina explicit user command ke GitHub push/release/tag/PR** (upar wala Hard Rule)

## Commands

| Command | Kaam |
|---|---|
| `make setup` | Sab kuch install karo |
| `make dev` | Charting app kholo (`python -m app`) |
| `make check` | Poora verification gate |
| `make format` | Formatting auto-fix |

## Ek Line Mein

> Pehle Brain padho, code event-driven rakho, kaam ke baad Brain update karo — bas ye 3 baatein.
