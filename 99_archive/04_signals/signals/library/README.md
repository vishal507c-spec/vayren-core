# signals/library/ — Indicator Library

## What Is Inside

| Indicator | What It Measures | Typical Use |
|---|---|---|
| `RSI` | Relative Strength Index — overbought/oversold | Buy when RSI < 30, sell when RSI > 70 |
| `MACD` | Moving Average Convergence Divergence — trend strength | Buy when MACD crosses above signal line |
| `Volatility` | Standard deviation of returns | Reduce position size when volatility is high |

---

## How to Add a New Indicator

1. Create a new file in `signals/library/` (e.g., `bollinger.py`)
2. Implement the compute function
3. Create an `Indicator` instance with the compute function
4. Register it in `signals/services/compute.py`
5. Add tests in `signals/tests/`
