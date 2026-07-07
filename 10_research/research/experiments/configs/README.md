# research/experiments/configs/ — Experiment Configurations

## Format

```yaml
# experiment_name.yaml
name: "rsi_threshold_search"
model: "xgboost"
features:
  - "rsi_14"
  - "lagged_return_5"
  - "volume_zscore"
hyperparameters:
  learning_rate: [0.01, 0.05, 0.1]
  max_depth: [3, 5, 7]
  n_estimators: 100
training:
  start_date: "2020-01-01"
  end_date: "2023-12-31"
  validation_split: 0.2
```

YAML is used because it is human-readable and supports comments.
