# Strategy — Strategy Platform (05_strategy)

Registry, definitions, Python-native runtime + lab UI.

- `strategies/` — native Python strategies (`base.PythonStrategy` + `indicators` + `sma.py`)
- `language/` — Python compiler (`compile_strategy` → `PythonStrategy`), storage (`StrategyRecord` → `.py` library files)
- `models/` — definition, parameters, signal, state
- `runtime.py` — Python bar-by-bar signals (`StrategyLogic.on_bar`)
- `events/` — `StrategiesListed`, `StrategySelected`, `PaperTradeRequested`, `LabReset`
- `research/` — intelligence, evidence, lineage, evolution, validation

Depends on: core, market. Event-driven; composition root `00_app` ke through wired.
