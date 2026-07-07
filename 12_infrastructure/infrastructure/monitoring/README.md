# infrastructure/monitoring/ — Monitoring

## What Is Inside

| Tool | Purpose |
|---|---|
| `prometheus.yml` | Prometheus configuration — scrapes metrics from all services |
| `grafana/` | Grafana dashboards (if present) |
| `alerts.yml` | Alerting rules (if present) |

---

## Key Metrics

| Metric | Source | What It Tracks |
|---|---|---|
| `order_latency_seconds` | Execution | Time from order request to submission |
| `signal_lag_seconds` | Signals | Time from bar to signal computation |
| `portfolio_total_value` | Portfolio | Current portfolio value |
| `risk_limit_usage_ratio` | Risk | Current usage / max limit (alerts at 0.8) |
