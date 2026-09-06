# Risk — Fail-Closed Pre-Order Gates

**Owns:** RiskPolicy, RiskEngine, KillSwitch, session/clock rules. **Not owns:** Order planning/execution → `08_execution`.
**When to read:** Before allowing any order path. **Related:** `90_brain/module_contracts.md`.

`RiskEngine.evaluate(request)` denies unless every applicable check passes.
Any engine error denies too. Denials always carry reasons.
