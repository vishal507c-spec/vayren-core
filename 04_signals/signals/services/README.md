# signals/services/ — Signal Services

## What Is Inside

| Service | Responsibility |
|---|---|
| `SignalRegistry` | Maps signal names to their compute functions (plugin registry pattern) |
| `SignalScheduler` | Triggers signal computation on a cadence (every bar, every N minutes, etc.) |

---

## The SignalRegistry Pattern

```python
registry = SignalRegistry()
registry.register("rsi_14", compute_rsi)
registry.register("macd", compute_macd)
registry.register("bb_width", compute_bollinger_width)

result = registry.compute("rsi_14", bars)
print(result.value, result.confidence, result.timestamp)
```
