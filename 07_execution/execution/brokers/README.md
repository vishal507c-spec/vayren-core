# execution/brokers/ — Brokers

## What Is a Broker?

A **broker** is an intermediary that executes trades on behalf of a trader. In software, a broker adapter is code that communicates with a broker's API to submit orders, check positions, and manage accounts.

---

## What Is Inside

| Broker | Type | Use Case |
|---|---|---|
| `simulated.py` | SimulatedBroker | Backtesting and paper trading — no real money, no real API calls |
| `alpaca.py` | AlpacaBroker | Live and paper trading via Alpaca Markets API |

---

## The Broker Interface

Every broker follows the same pattern:
- `submit_order(order)` — Send an order to the broker
- `get_positions()` — Get current positions from the broker

The simulated broker mimics real broker behavior using configurable slippage and fill probability.

---

## How to Add a New Broker

1. Create a new file in `execution/brokers/` (e.g., `ibkr.py`)
2. Implement the standard methods
3. Register it in `execution/brokers/__init__.py`
4. Add its configuration to `lib/config/schemas.py`
5. Add integration tests in `execution/tests/`
