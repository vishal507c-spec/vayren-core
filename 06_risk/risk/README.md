# risk/ — Risk

## What Is Risk?

In trading, **risk** is the possibility of losing money. Risk management is the practice of limiting losses to acceptable levels.

Risk answers questions like:
- **Pre-trade:** "Is this order within our limits?"
- **Post-trade:** "Is our total exposure acceptable?"
- **Scenario:** "What happens if the market drops 10%?"

---

## Purpose

`risk/` enforces risk limits before trades execute and monitors exposure after trades execute. It acts as a **safety system** that prevents the trading system from exceeding defined boundaries.

---

## Why This Folder Exists

Without explicit risk management:
- A single strategy could lose all capital
- The portfolio could become concentrated in one asset
- No one would know if limits were breached until it was too late

`risk/` is the **guardrail** that keeps the system within safe operating parameters.

---

## Visual Diagram

```
    Portfolio                    risk/                         Execution

    OrderRequested    ──▶  PreTradeRisk.check_order()  ──▶  Approved → Execute
    (SPY, buy, 100)        .add_limit(RiskLimit(...))        Rejected → Alert
                                  │
                                  ▼
    PositionUpdate     ──▶  RiskMonitor.check_limits()
    (SPY, +100)              .update_exposure()
                                  │
                                  ▼
                            LimitApproaching event (warning)
                            LimitBreached event (critical)
                            StressResult event (what-if analysis)
```

---

## Quick Example

```python
from risk.models.limit import RiskLimit
from risk.services.pre_trade import PreTradeRisk

# Define a limit
max_order = RiskLimit(name="max_order_value", max_value=50000.0)

# Check before executing
risk = PreTradeRisk()
risk.add_limit(max_order)

approved, failures = risk.check_order(symbol="SPY", quantity=100, price=450.50, capital=100000.0)
if approved:
    print("Order approved")
else:
    print(f"Order rejected: {failures}")
```

---

## Continue to the Next Lesson

→ `analytics/` — How performance is measured
