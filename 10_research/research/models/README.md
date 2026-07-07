# research/models/ — ML Models

## What Is Inside

| Subfolder | Purpose |
|---|---|
| `supervised/` | Classification and regression models (XGBoost, LSTM, Transformers) |
| `rl/` | Reinforcement learning models (PPO, SAC for trade execution) |

---

## Model Registry

Every model is registered with metadata:
- **name** — unique identifier
- **version** — semantic version
- **architecture** — model type
- **features** — feature pipeline used
- **trained_on** — training data range
- **metrics** — validation performance
- **status** — development/staging/production/deprecated
