# Execution — Live/Paper Strategy Execution with Safety Gates

**Owns:** Normalized market data, strategy runtime + contracts, order planning,
execution state machine, broker boundary (paper real, live NOT_CONFIGURED),
portfolio ledger, reconciliation, journal/replay, regime, adaptive layer.
**Not owns:** Risk policy evaluation → `07_risk`; strategy logic → `05_strategy`.
**When to read:** Before touching any live-trading path. **Related:** `90_brain/module_contracts.md`.

LIVE_BROKER_INTEGRATION = NOT_CONFIGURED. Default mode is PAPER; LIVE needs
all five explicit gates. Strategies never call brokers.
