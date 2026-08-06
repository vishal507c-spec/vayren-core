# signals/events/ — Signal Events

## What Is Inside

| Event | When It Is Published |
|---|---|
| `SignalGenerated` | A new signal value has been computed and is available for consumption |

---

## Event Payload

```python
SignalGenerated(
    signal_name="rsi_14",
    symbol="SPY",
    value=32.5,
    confidence=0.85,
    timestamp=datetime(2024, 6, 15, 9, 30, 0),
    bar=bar  # optional reference to source bar
)
```

Strategies subscribe to `SignalGenerated` to decide whether to enter or exit.
