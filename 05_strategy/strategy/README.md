# Strategy — Strategy Platform (05_strategy)

Registry, definitions, language/compiler, storage, runtime + lab UI. VM-only execution.

- `language/` — `.vstrat` → parser → compiler → IR (`strategy.language.compiler`)
- `storage.py` — `ensure_builtin_strategies` creates `.vstrat` samples (no `builtins/` folder — removed 2026-08-24, VM path only)
- `models/` — definition, parameters, signal, state
- `runtime.py` — IR → VM bar-by-bar signals (no `exec`)
- `events/` — `StrategiesListed`, `StrategySelected`, `PaperTradeRequested`, `LabReset`
- `research/` — intelligence, evidence, lineage, evolution, validation
- `ui/` — Strategy Lab panel

Depends on: core, market. Event-driven; subscriptions in `bootstrap`.
