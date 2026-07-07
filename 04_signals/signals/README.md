# signals/ — Signals

## What Is a Signal?

A **signal** is a value that indicates something about market conditions. It answers a question like:

- "Is the market overbought?" (RSI signal: 70+ = overbought)
- "Is there momentum?" (MACD signal: positive = bullish)
- "Is volatility high?" (Volatility signal: elevated = risky)

Signals transform **raw data** (prices, volumes) into **meaning** (overbought, trending, volatile).

---

## Purpose

`signals/` transforms raw market data into actionable trading signals. It computes indicators, detects market regimes, and normalizes results into a consistent format that strategies can use.

---

## Why This Folder Exists

Without `signals/`, every strategy would need to implement its own RSI calculation, MACD calculation, volatility estimation, and so on. This would mean:

1. Duplicated code across strategies
2. Inconsistent implementations (same indicator, different results)
3. No single source of truth for "what does the RSI say right now?"

`signals/` computes everything once, correctly, and publishes the results for any strategy to use.

---

## What Problem Does It Solve?

**Signal inconsistency.** If two strategies both compute RSI for SPY, they should get the same value. `signals/` ensures this by computing each signal exactly once and sharing the result.

---

## Mental Model

Think of `signals/` as a **news wire service** (like Reuters or Bloomberg). Reporters (indicators) analyze raw data and produce stories (signals). The news wire publishes these stories, and any subscriber (strategy) can read them.

---

## Visual Diagram

```
    market/                           signals/                     strategies/
    
    Bar(Timestamp, OHLCV)     ──▶  rsi_indicator.compute(bars)    ──▶  "RSI > 70, sell"
    Trade(price, size, time)  ──▶  macd_indicator.compute(bars)   ──▶  "MACD crossed, buy"
    Bar(high, low, close)     ──▶  volatility.compute(bars)       ──▶  "Volatility high, reduce size"
                                      │
                                      ▼
                                SignalRegistry
                                (stores latest signal per symbol)
                                      │
                                      ▼
                                SignalUpdated event
                                (announces: "SPY RSI is now 72.3")
```

---

## Where It Fits in the Pipeline

```
Position:   2nd of 8 processing departments
Before:     market/ (provides the raw data)
After:      strategies/ (consumes signals to make decisions)
```

---

## Folder Structure

```
signals/
├── __init__.py      ← Public API: Signal, Indicator, Regime, SignalRegistry
├── README.md        ← This lesson
├── models/          ← Things: Signal, Indicator, Regime
├── services/        ← Actions: compute, normalize, registry
├── events/          ← Messages: SignalUpdated, RegimeDetected
├── library/         ← Implementations: RSI, MACD, Volatility
├── tests/           ← Checks
└── conftest.py      ← Shared test data
```

---

## What Every Subfolder Means

| Subfolder | Purpose |
|---|---|
| `models/` | Signal (a computed value), Indicator (a computation definition), Regime (market state) |
| `services/` | SignalComputeService (run indicators), SignalNormalizer (combine/normalize), SignalRegistry (store/catalog) |
| `events/` | SignalUpdated (signal changed), RegimeDetected (market regime identified) |
| `library/` | Concrete indicator implementations: RSI, MACD, Volatility |

---

## Quick Example

```python
from signals import Signal
from signals.library.rsi import compute_rsi

# Calculate RSI for a price series
prices = [450.0, 452.0, 448.0, 455.0, 458.0]
rsi_value = compute_rsi(prices, period=14)

# Create a signal
signal = Signal(name="rsi_14", value=rsi_value, timestamp="2025-07-05T20:00:00Z", symbol="SPY")
print(f"RSI: {signal.value}")       # e.g., 65.3
print(f"Bullish: {signal.is_bullish}")  # True if > 50
```

---

## Common Mistakes

| Mistake | Why |
|---|---|
| Computing indicators inside strategies | Put signals in signals/. Strategies consume them. |
| Using different RSI periods for the same symbol | Standardize. Use consistent parameters. |
| Ignoring signal normalization | Signals from different sources need normalization before combination. |
| Not caching computed signals | Computing RSI for 1000 bars every tick is wasteful. Cache and update incrementally. |

---

## Continue to the Next Lesson

→ `strategies/` — How signals become trading decisions
