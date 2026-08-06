# research/agents/ — AI Agents

## What Are AI Agents?

AI agents in this context are autonomous programs that perform research tasks:
- **StrategyDiscoveryAgent** — Generates new strategy ideas from market data patterns
- **ParameterOptimizationAgent** — Finds optimal strategy parameters via search
- **HypothesisTestingAgent** — Tests specific hypotheses with statistical rigor

---

## Agent Interface

Every agent follows the same pattern:
```python
class Agent:
    name: str
    async def run(self, context: AgentContext) -> AgentResult
    async def report(self) -> AgentReport
```
