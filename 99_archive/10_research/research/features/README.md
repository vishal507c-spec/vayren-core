# research/features/ — Feature Pipelines

## What Is a Feature Pipeline?

A **feature pipeline** transforms raw market data into model-ready features. It is the bridge between data engineering and machine learning.

---

## What Is Inside

| Component | Purpose |
|---|---|
| `FeaturePipeline` | Orchestrates a sequence of transformations |
| `FeatureTransformer` | Base class for individual transformations |
| Registered features | Lagged returns, volatility, volume profile, technical indicators, regime flags |

---

## Pipeline Composition

```python
pipeline = FeaturePipeline([
    LagReturns(lags=[1, 5, 21]),
    Volatility(window=20),
    RSIFeature(window=14),
    RegimeFlag(),
    Normalize(),
])
X = pipeline.transform(bars)
```
