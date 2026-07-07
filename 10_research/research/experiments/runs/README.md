# research/experiments/runs/ — Experiment Run Logs

## Directory Structure

```
runs/YYYY-MM-DD_HH-MM-SS/
├── config.yaml         ← Snapshot of the experiment config used
├── metrics.json        ← Per-epoch or per-step metrics
├── checkpoints/        ← Model weight snapshots
├── logs/               ← TensorBoard or text logs
└── artifacts/          ← Charts, confusion matrices, etc.
```

Each run is timestamped and immutable — runs are never modified after completion.
