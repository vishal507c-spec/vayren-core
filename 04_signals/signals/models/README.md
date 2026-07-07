# signals/models/ — Models

## What Is Inside

| Model | What It Represents |
|---|---|
| `Signal` | A computed value indicating market conditions. Has a name, value, timestamp, and confidence. |
| `Indicator` | A definition of HOW to compute a signal. Has a name, parameters, and a compute function. |
| `Regime` | A classification of market state (bull, bear, volatile, sideways). |

---

## Quick Example

```python
from signals.models.signal import Signal
from signals.models.regime import Regime

# A signal
signal = Signal(name="rsi_14", value=72.5, timestamp="2025-07-05T20:00:00Z", symbol="SPY")
print(signal.is_bullish)  # True (72.5 > 50)

# A regime
regime = Regime(name="bull_market", value=1.0, timestamp="2025-07-05T20:00:00Z", trend="bullish")
```
