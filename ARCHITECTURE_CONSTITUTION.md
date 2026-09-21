# VAYREN — ARCHITECTURE CONSTITUTION

## 1. PURPOSE

This document defines the temporary architectural priority for VAYREN during the current migration phase.

The current priority is simple:

> **COMPLETE THE APPROVED VAYREN MIGRATION FIRST.**

Until the migration is fully completed and validated, no additional architectural rules, development policies, redesign decisions, or new migration strategies should be introduced.

This document is intentionally minimal during the migration phase.

---

# 2. CURRENT STATUS

```text
VAYREN MIGRATION
STATUS: COMPLETE

PRIORITY: NATIVE OPERATION
```

The migration is **COMPLETE** (zero-legacy audit passed: the production
application is Rust + Slint served by a headless Python backend; no legacy
UI toolkit remains in source, dependencies, tests, scripts or docs).

Therefore, VAYREN operates in **NATIVE MODE**. The migration-mode rules
below are retained as history; §11 target ownership now applies directly.

---

# 3-10. MIGRATION-MODE ARCHIVE (retired - migration is COMPLETE per section 2)

Sections 3-10 held the active-migration procedure (hard priority on finishing
the approved scope; no new rules/scope/redesign mid-migration; preserve
existing behavior; completion criteria; post-migration update path). They are
superseded, not deleted from history: the full text lives in git history
(pre-Phase-4 revisions of this file). The enduring principles survive condensed:

```text
FINISH APPROVED SCOPE -> preserve behavior -> no unrelated rewrites ->
no scope expansion ("while we are here" forbidden) -> validate -> STOP
```

Before any change, the agent still asks (section 13 condensed):

```text
1. Which responsibility/domain is this? 2. Which language owns it (sections 11-12)?
3. Is the change directly required, behavior-preserving, minimal?
NOT REQUIRED -> do not include it.
```

---
# 11. POST-MIGRATION ARCHITECTURE

After migration is complete, the architecture may be formally defined and expanded.

The intended target architecture currently identified for VAYREN is:

```text
                    VAYREN
                       │
          ┌────────────┼────────────┐
          │            │            │
         RUST        PYTHON     RUST + SLINT
          │            │            │
       CORE /       STRATEGY       NATIVE UI
       PERFORMANCE    AI/ML
       MARKET/DATA    RESEARCH
       INDICATORS
       RISK
       EXECUTION
       BACKTEST
```

This section describes the intended target direction.

It does NOT expand the current migration scope.

---

# 12. TARGET TECHNOLOGY OWNERSHIP

The intended target ownership is:

```text
RUST
→ Core
→ Performance
→ Market/Data
→ Indicators
→ Numerical calculations
→ Risk
→ Execution
→ Backtesting
→ Performance-critical processing
```

```text
PYTHON
→ Strategy
→ Strategy experimentation
→ AI
→ Machine Learning
→ Research
→ Statistical experimentation
→ Research workflows
→ Model experimentation
```

```text
RUST + SLINT
→ Native UI
```

These are the intended post-migration architectural responsibilities.

They must not be used to create unrelated migration work before the current approved migration is complete.

---

# 13-17. ACTIVE-MIGRATION AGENT RULES (retired - see 3-10 archive)

Sections 13-17 held the per-agent rules for the active migration phase
(change-gating questions, scope-expansion ban, migration priority order,
mode-exit criteria, final migration rule). Superseded with the migration
marked COMPLETE; full text in git history. What survives is the condensed
discipline in section 3-10 above: decide language from sections 11-12,
keep the change directly required and behavior-preserving, validate, stop.

---
# 18. FINAL PRINCIPLE

```text
┌──────────────────────────────────────┐
│       VAYREN MIGRATION MODE          │
├──────────────────────────────────────┤
│                                      │
│  CURRENT MIGRATION = PRIORITY        │
│                                      │
│  COMPLETE IT                         │
│  TEST IT                             │
│  VALIDATE IT                         │
│                                      │
│  DO NOT EXPAND SCOPE                 │
│  DO NOT ADD NEW RULES                │
│  DO NOT REDESIGN                     │
│  DO NOT START UNRELATED WORK         │
│                                      │
│  AFTER COMPLETE:                     │
│  UPDATE ARCHITECTURE CONSTITUTION    │
│                                      │
└──────────────────────────────────────┘
```

**Current state:**

```text
MIGRATION_ACTIVE = FALSE
MIGRATION_COMPLETE = TRUE
```

**`MIGRATION_COMPLETE = TRUE`: the target ownership (§11–§12) is the active architectural authority.**
