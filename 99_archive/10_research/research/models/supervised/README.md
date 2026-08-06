# research/models/supervised/ — Supervised Learning Models

## What Is Supervised Learning?

**Supervised learning** trains a model on labeled data to predict an output from inputs. In trading:
- **Classification** — predict direction (up/down/flat)
- **Regression** — predict magnitude (expected return)

---

## Implemented Algorithms

| Algorithm | Type | Use Case |
|---|---|---|
| XGBoost | Gradient boosting | Tabular features, strong baseline |
| LSTM | Recurrent neural net | Time series prediction |
| Transformer | Attention-based | Sequence modeling with long dependencies |
