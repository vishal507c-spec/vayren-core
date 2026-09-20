# Vayren — Native Trading Platform

Vayren is a native desktop trading platform: Rust + Slint UI served by a
headless Python backend over newline-delimited JSON.

```
Rust + Slint shell (vayren-shell)
        ↕  JSON over stdin/stdout
Headless Python backend (app.headless)
```

Flow: shell → backend snapshot (symbols, market, system, portfolio, live,
research, lab) → Rust state → Slint projection.

- UI: Rust + Slint only (`rust/vayren-shell`). No other UI framework.
- Backend: Python owns market data, strategies, backtest, risk, execution,
  broker SDKs, research (`00_app`–`09_broker`, headless, stdlib threading).
- Launch: `make dev` (debug) — data from `VAYREN_DATA_DIR`, strategies
  from `VAYREN_STRATEGIES`.

Authoritative architecture/rules/contracts are in AGENTS.md,
ARCHITECTURE_CONSTITUTION.md, and 90_brain/.
AI: read those authoritative files before making architectural/code
decisions; do not infer or invent rules from this README.
