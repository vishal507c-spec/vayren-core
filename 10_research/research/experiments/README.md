# research/experiments/ — ML Experiments

## What Is Inside

| Subfolder | Purpose |
|---|---|
| `configs/` | Experiment configuration files (hyperparameters, dataset references) |
| `results/` | Experiment result logs (metrics, trained model references) |
| `runs/` | Per-run logs (metrics per epoch, checkpoints, tensorboard logs) |

---

## Experiment Lifecycle

```
1. Define config (configs/experiment_name.yaml)
2. Run experiment → saves to runs/YYYY-MM-DD_HH-MM-SS/
3. Evaluate results → saves to results/experiment_name.json
4. Compare across experiments → research/evaluation/
```
