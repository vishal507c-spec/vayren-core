# strategies/library/ — Strategy Library

## What Is Inside

| Strategy | Type | Entry Signal | Exit Signal |
|---|---|---|---|
| `momentum.py` | Momentum | RSI > 70 (strong trend) | RSI < 30 (trend fading) |
| `mean_reversion.py` | Mean Reversion | RSI < 30 (oversold) | RSI > 70 (overbought) |

---

## How to Add a New Strategy

1. Create a new file in `strategies/library/`
2. Implement the `Strategy` abstract base class
3. Register it in `strategies/services/registry.py`
4. Add tests in `strategies/tests/`

---

## Strategy Interface

Every strategy must implement:
- `name` property — unique identifier
- `should_enter(context)` → bool
- `should_exit(context)` → bool
- `generate_orders(context)` → list[OrderRequested]
- `on_start()` / `on_stop()` — lifecycle hooks
