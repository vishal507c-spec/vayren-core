# portfolio/ — Portfolio

## What Is a Portfolio?

A **portfolio** is a collection of positions held by a trading account. It answers:

- **Allocation:** How much capital is assigned to each strategy/symbol?
- **P&L:** Are we making or losing money overall?
- **Exposure:** Where is our capital deployed?

---

## Purpose

`portfolio/` manages capital allocation across strategies, tracks profit and loss, and determines when rebalancing is needed.

---

## Why This Folder Exists

A single strategy can track its own P&L. But when you have multiple strategies running simultaneously, you need a central view:

- How much capital does each strategy control?
- What is the total portfolio return?
- Are we overexposed to any sector?

`portfolio/` provides this central view.

---

## Visual Diagram

```
    strategies/            execution/              portfolio/
    
    Strategy A      ──▶   Position(SPY, 100) ──▶  Portfolio("main")
    Strategy B      ──▶   Position(AAPL, 50)      .strategies = ["A", "B"]
    Strategy C      ──▶   Position(GLD, 200)      .total_capital = 100000
                                                      │
                                                      ▼
                                                 PortfolioTracker
                                                 .update_pnl(name, pnl)
                                                      │
                                                      ▼
                                                 AllocationUpdated
                                                 RebalanceTriggered events
```

---

## Quick Example

```python
from portfolio.models.portfolio import Portfolio

portfolio = Portfolio(name="main", total_capital=100000.0, cash=100000.0)
print(f"Equity: {portfolio.equity}")  # 100000.0

# After trades execute:
# portfolio.allocated_capital += 50000
# portfolio.cash -= 50000
# portfolio.total_pnl += 1500.50
# print(f"Return: {portfolio.return_pct}%")  # 1.5%
```

---

## Continue to the Next Lesson

→ `risk/` — How risk is managed
