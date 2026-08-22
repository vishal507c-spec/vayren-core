# strategy — Strategy Platform

Registry, definitions, runtime and lab UI for trading strategies.

- `registry.py` — kinds (logic factories) + definitions (configured instances)
- `models/` — definition, parameters, signal, state
- `runtime.py` — bar-by-bar execution of a logic into signals
- `builtins/` — explicitly registered production strategies
- `events/` — StrategiesListed, StrategySelected, PaperTradeRequested, LabReset
- `ui/` — Strategy Lab control panel, strategy list, configure dialog

Depends on: core, market. Event-driven only; subscriptions live in bootstrap.
