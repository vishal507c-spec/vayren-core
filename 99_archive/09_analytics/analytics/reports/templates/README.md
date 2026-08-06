# analytics/reports/templates/ — Report Templates

## What Is Inside

| Template | Format | Use Case |
|---|---|---|
| `summary.html` | HTML | Browser-viewable performance summary |
| `summary.md` | Markdown | Inline display, git-friendly |
| `detailed.pdf` | PDF | Formal client/external report |

---

## Template Variables

| Variable | Source | Example |
|---|---|---|
| `{{ metrics }}` | PerformanceMetrics | Sharpe: 1.5, Max DD: -12% |
| `{{ attribution }}` | Attribution | Momentum: +3.2%, Value: -1.1% |
| `{{ period }}` | DateRange | 2024-01-01 to 2024-06-30 |
| `{{ charts }}` | Base64-encoded PNG | Equity curve, drawdown, rolling Sharpe |
