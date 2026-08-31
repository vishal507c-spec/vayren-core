# Strategy — Strategy Platform (05_strategy)

Registry, definitions, Python-native runtime + lab UI.

- `strategies/` — native Python strategies (`base.PythonStrategy` + `indicators` + `obr.py`/`sma.py`)
- `language/` — Python compiler (`compile_strategy` → `PythonStrategy`), storage (`StrategyRecord` → `.py`)
- `storage.py` — `ensure_builtin_strategies` creates Python samples (no VM)
- `models/` — definition, parameters, signal, state
- `runtime.py` — Python bar-by-bar signals (`StrategyLogic.on_bar`)
- `events/` — `StrategiesListed`, `StrategySelected`, `PaperTradeRequested`, `LabReset`
- `research/` — intelligence, evidence, lineage, evolution, validation
- `ui/` — Strategy Lab panel (edits Python code)

Depends on: core, market. Event-driven; subscriptions in `bootstrap`.
