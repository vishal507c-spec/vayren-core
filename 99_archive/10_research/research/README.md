# research/ — Research

## What Is Research?

**Research** is the systematic process of discovering improvements to the trading system. It encompasses:

- **Feature engineering:** Finding predictive patterns in market data
- **Machine learning:** Training models to predict price movements or classify market states
- **Reinforcement learning:** Training agents to optimize execution or portfolio allocation
- **Experiments:** Testing hypotheses in a controlled, repeatable way
- **AI agents:** Autonomous programs that explore data and propose strategies

---

## Purpose

`research/` is where the system improves itself. It contains all AI/ML work, experiment tracking, and autonomous agents.

---

## Why This Folder Exists

Without dedicated research:
- Improvements happen ad-hoc and are not reproducible
- Experiments are not tracked, so you repeat the same failed approaches
- AI/ML code mixes with production code, creating fragility
- The system never systematically improves

`research/` isolates experimentation from production. Production code is stable. Research code is fluid. They never mix.

---

## Visual Diagram

```
    market/                    research/                        strategies/
    
    Market data      ──▶  features/              ──▶  New strategies
    (raw prices)           .definitions()              discovered by agents
                           .transforms()
                           .pipelines()            ──▶  Improved signals
                               │                        from ML models
                               ▼
                        models/
                        .supervised/              ──▶  Better execution
                        .rl/                           from RL agents
                        .registry()
                               │
                               ▼
                        training/
                        .pipeline()               ──▶  knowledge/experiments/
                        .cross_validate()              (all results recorded)
                        .hyperparameter()
                               │
                               ▼
                        agents/
                        .strategy_discovery()
                        .anomaly_detector()
```

---

## Quick Example

```python
from research.features.pipelines import FeaturePipeline
from research.features.definitions import FeatureDefinition

# Define a feature pipeline
pipeline = FeaturePipeline()
pipeline.add(FeatureDefinition(
    name="rsi_14",
    description="14-period RSI",
    category="momentum",
))

# Compute features
features = pipeline.compute_all(price_data)
```

---

## Continue to the Next Lesson

→ `platform/` — How the system runs
