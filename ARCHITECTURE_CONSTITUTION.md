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
STATUS: ACTIVE

PRIORITY: MIGRATION COMPLETION
```

The migration is currently **NOT COMPLETE**.

Therefore, VAYREN remains in **MIGRATION MODE**.

---

# 3. HARD MIGRATION RULE

Until the current approved migration is completely finished:

> **ALL PRIMARY DEVELOPMENT PRIORITY MUST REMAIN ON COMPLETING THE MIGRATION.**

The migration must be:

```text
IMPLEMENT
    ↓
INTEGRATE
    ↓
TEST
    ↓
VALIDATE
    ↓
FIX
    ↓
RE-VALIDATE
    ↓
MIGRATION COMPLETE
```

Do not treat partial migration as completed migration.

Do not declare migration complete until the currently approved migration scope has actually been implemented, integrated, tested, and validated.

---

# 4. WHAT MUST NOT HAPPEN DURING MIGRATION

While migration status is:

```text
MIGRATION = ACTIVE
```

DO NOT introduce additional rules or scope.

Specifically:

* Do NOT create new architectural rules.
* Do NOT create additional migration policies.
* Do NOT change the migration strategy.
* Do NOT start a new broad migration.
* Do NOT redesign the architecture.
* Do NOT create unrelated refactors.
* Do NOT rewrite unrelated code.
* Do NOT remove existing functionality.
* Do NOT change existing behavior unnecessarily.
* Do NOT introduce unrelated frameworks.
* Do NOT introduce unrelated languages.
* Do NOT redesign the UI unless it is directly required by the approved migration.
* Do NOT start unrelated feature development.
* Do NOT expand the migration scope merely because another old component exists.
* Do NOT turn the current migration into a repository-wide redesign.

The purpose is to **finish the current migration**, not continuously redefine the project.

---

# 5. MIGRATION SCOPE

Only the **currently approved migration scope** is part of this migration.

```text
CURRENT APPROVED MIGRATION
        ↓
IMPLEMENT
        ↓
INTEGRATE
        ↓
TEST
        ↓
VALIDATE
        ↓
COMPLETE
```

Do not automatically add new modules, new responsibilities, or unrelated components to the migration.

If something is outside the currently approved migration scope, leave it alone unless it is technically required to complete the approved migration.

---

# 6. EXISTING FUNCTIONALITY

Existing functionality must be preserved during migration.

Migration means:

```text
EXISTING IMPLEMENTATION
        ↓
TARGET IMPLEMENTATION
        ↓
SAME INTENDED BEHAVIOR
```

Do not use migration as an excuse to redesign functionality.

Do not intentionally remove functionality simply because it is inconvenient to migrate.

Do not change business logic unless the change is required to correctly complete the approved migration.

---

# 7. MIGRATION PRINCIPLE

The current goal is:

> **Migrate the existing implementation to the approved target architecture without unnecessary changes.**

The migration should prioritize:

1. Existing functionality
2. Existing behavior
3. Existing data semantics
4. Existing interfaces/contracts where required
5. Correct target implementation
6. Testing
7. Validation

The objective is not to create a new system.

The objective is to complete the migration of the existing system.

---

# 8. NO NEW ARCHITECTURAL DECISIONS DURING MIGRATION

During the active migration phase, do not continuously make new architectural decisions.

If a decision is already part of the approved migration, follow it.

If something is not required to complete the migration:

```text
DO NOT EXPAND SCOPE
```

If a completely new architectural question appears:

```text
DEFER IT
```

It can be addressed after the migration is complete.

---

# 9. MIGRATION COMPLETION CRITERIA

The migration can only be marked complete when:

* The approved migration scope has been implemented.
* Required integrations are complete.
* Existing functionality is preserved.
* Tests pass.
* Validation passes.
* No known migration component remains unfinished within the approved scope.
* The migrated implementation is actually being used where required.
* The old implementation is removed only where removal is safe and part of the approved migration.
* No critical migration errors remain.

Then:

```text
MIGRATION STATUS
        ↓
COMPLETE
```

---

# 10. AFTER MIGRATION IS COMPLETE

Only after the migration is fully completed and validated should this constitution be updated.

The post-migration process is:

```text
CURRENT MIGRATION
        ↓
100% COMPLETE
        ↓
VALIDATE
        ↓
MARK MIGRATION COMPLETE
        ↓
UPDATE THIS CONSTITUTION
        ↓
DEFINE FUTURE ARCHITECTURE RULES
```

Until that point, do not prematurely apply future architectural policies as additional migration requirements.

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

# 13. AI CODING AGENT RULE

Every AI coding agent working on VAYREN during the active migration phase must understand:

```text
MIGRATION ACTIVE
        ↓
FINISH CURRENT APPROVED MIGRATION
        ↓
DO NOT EXPAND SCOPE
```

Before making a change, the agent should ask:

```text
1. Is this required for the current approved migration?
2. Is this directly necessary to complete the migration?
3. Will this preserve existing functionality?
4. Can the migration be completed without this change?
```

If the answer is:

```text
NOT REQUIRED
```

then do not include the change in the migration.

---

# 14. NO ACCIDENTAL SCOPE EXPANSION

The following pattern is prohibited during the active migration:

```text
Migration
   ↓
"While we are here..."
   ↓
Another module
   ↓
Another refactor
   ↓
Another architecture change
   ↓
Another framework
   ↓
Another migration
```

Instead:

```text
Approved Migration
       ↓
Complete Migration
       ↓
Validate
       ↓
STOP
```

---

# 15. PRIORITY ORDER

During the active migration, priority is:

```text
1. CURRENT APPROVED MIGRATION
2. REQUIRED DEPENDENCIES
3. REQUIRED INTEGRATION
4. TESTING
5. VALIDATION
6. BUG FIXES REQUIRED FOR MIGRATION
```

Everything else is secondary and should not expand the migration unnecessarily.

---

# 16. MIGRATION MODE ENDS ONLY ON COMPLETION

The migration mode remains active until explicitly confirmed complete.

```text
MIGRATION = ACTIVE
```

means:

```text
FINISH MIGRATION FIRST
```

It does not mean:

```text
START NEW ARCHITECTURE PROJECTS
```

It does not mean:

```text
CONTINUOUSLY EXPAND MIGRATION
```

It means:

```text
COMPLETE THE CURRENT MIGRATION.
```

---

# 17. FINAL MIGRATION RULE

The fundamental rule of the current VAYREN development phase is:

> **DO NOT MOVE ON UNTIL THE CURRENT APPROVED MIGRATION IS COMPLETE.**

The migration is the current priority.

No additional architectural policy is required during this phase.

No additional migration philosophy is required during this phase.

No unrelated redesign is required during this phase.

No unrelated modernization is required during this phase.

No new architecture project is required during this phase.

```text
CURRENT APPROVED MIGRATION
            ↓
        COMPLETE
            ↓
        VALIDATE
            ↓
     MIGRATION COMPLETE
            ↓
   UPDATE CONSTITUTION
            ↓
 FUTURE ARCHITECTURE RULES
```

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
MIGRATION_ACTIVE = TRUE
MIGRATION_COMPLETE = FALSE
```

**Until `MIGRATION_COMPLETE = TRUE`, the current approved migration remains the primary architectural priority.**
