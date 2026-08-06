# analytics/ — Analytics

## What Is Analytics?

**Analytics** is the measurement and evaluation of trading performance. It answers:

- **Performance:** "What was the return? The Sharpe ratio?"
- **Attribution:** "Which strategies contributed most?"
- **Reporting:** "What happened this day/week/month?"

---

## Purpose

`analytics/` computes performance metrics, analyzes attribution across strategies, and generates reports.

---

## Why This Folder Exists

Without analytics, you cannot answer the most important question: **"Is this working?"**

Analytics provides objective measurement. It tells you if a strategy is actually profitable or just lucky, if risk-adjusted returns are acceptable, and where improvements are needed.

---

## Visual Diagram

```
    portfolio/              risk/                  analytics/
    
    Portfolio P&L    ──▶  Stress tests    ──▶  PerformanceAnalyzer
    Position data     │   Risk metrics          .calculate(returns)
                      │                         → Sharpe, Sortino, max drawdown
                      ▼
                  AttributionAnalyzer
                  .calculate_strategy_attribution()
                  → "Strategy A contributed 8%, B contributed 3%"
                      │
                      ▼
                  ReportGenerator
                  .generate(title, metrics)
                  → Report (daily/weekly/monthly)
                      │
                      ▼
                  ReportGenerated event
```

---

## Quick Example

```python
from analytics.services.performance import PerformanceAnalyzer

returns = [0.01, 0.02, -0.005, 0.015, 0.03, -0.01, 0.025]
analyzer = PerformanceAnalyzer()
metrics = analyzer.calculate(returns)

print(f"Total Return: {metrics.total_return_pct:.2f}%")
print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
print(f"Max Drawdown: {metrics.max_drawdown_pct:.2f}%")
print(f"Win Rate: {metrics.win_rate_pct:.1f}%")
```

---

## Continue to the Next Lesson

→ `research/` — How improvements are discovered
