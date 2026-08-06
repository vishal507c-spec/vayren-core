# research/services/ — Research Services

## What Is Inside

| Service | Responsibility |
|---|---|
| `ModelRegistry` | Manages model lifecycle — register, version, promote to production, deprecate |
| `FeatureStore` | Stores and retrieves computed features for training and inference |
| `HyperparameterOptimizer` | Searches for optimal model parameters (grid search, random search, Bayesian) |
