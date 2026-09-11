# Vayren — Desktop Charting Platform

Vayren is a desktop charting platform whose current job is ONLY to display candlestick charts from SQLite candle data.
Current release: v1.8.0.

Flow: App → EventBus → Data → SQLite → Chart.

Vayren currently owns ONLY chart display and the minimum infrastructure required to load and render candles.
It must NOT contain indicators, strategies, trading logic, broker/execution, portfolio, scanner, replay, backtest, drawings, or other future features.
Rule: every new feature belongs to its own future module; never expand an existing module to absorb unrelated features.
Authoritative architecture/rules/contracts are in AGENTS.md, ARCHITECTURE_CONSTITUTION.md, and 90_brain/.
AI: read those authoritative files before making architectural/code decisions; do not infer or invent rules from this README.