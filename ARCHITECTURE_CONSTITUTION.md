# VAYREN — ARCHITECTURE CONSTITUTION

**Owns:** Language ownership (Rust→Core/Perf, Python→Strategy/AI, Rust+egui→UI), migration principles, final architecture direction. **Not owns:** Detailed module maps/events/contracts/state → `90_brain/`; AI workflow → `AGENTS.md`.
**When to read:** Before choosing a language/framework for any new code, before touching legacy code that may migrate.
**Related:** `AGENTS.md` (how to apply), `90_brain/architecture.md` (current map).

## TASK CONTEXT

Create a NEW file at the repository root:

```text
ARCHITECTURE_CONSTITUTION.md
```

## IMPORTANT

This task is ONLY to create the architectural constitution.

### DO NOT migrate the existing codebase now.

Do NOT:

* rewrite the existing Python code
* migrate the entire repository
* convert all existing UI
* change existing functionality
* delete existing code
* perform a big-bang migration
* introduce or modify unrelated code

The purpose of this file is to permanently define how VAYREN will be developed from this point forward.

---

# VAYREN LANGUAGE & DEVELOPMENT ARCHITECTURE

The future architecture has exactly these primary technology responsibilities:

```text
RUST
→ Core + Performance

PYTHON
→ Strategy + AI/ML + Research

RUST + EGUI
→ Native UI
```

## 1. RUST — CORE & PERFORMANCE

All NEW functionality belonging to these responsibilities MUST be written in Rust:

* Core Engine
* Engine infrastructure
* Market/Data processing
* Indicators
* Numerical calculations
* Risk management
* Execution
* Backtesting
* Performance-critical processing
* High-throughput processing
* Latency-sensitive processing
* Memory/performance-critical systems

Rule:

> If a NEW feature belongs to the Rust-owned core/performance domain, implement it in Rust.

Do not choose Python for convenience.

---

# 2. PYTHON — STRATEGY, AI & RESEARCH

All NEW functionality belonging to these responsibilities MUST remain in Python:

* Trading Strategy
* Strategy logic
* Strategy experimentation
* AI
* Machine Learning
* Research
* Statistical experimentation
* Research workflows
* Model experimentation

Rule:

> If a NEW feature belongs to the Python-owned strategy/AI/research domain, implement it in Python.

Do not move these responsibilities to Rust merely because Rust is faster.

---

# 3. RUST + EGUI — UI

All NEW native UI development MUST use:

```text
Rust + egui
```

This includes:

* New screens
* New panels
* New controls
* New windows
* New UI components
* New native UI functionality
* New UI state/interaction logic

Rule:

> From now onward, every NEW native UI feature must be developed using Rust + egui.

Do not introduce another UI framework or UI technology without explicit architectural approval.

---

# 4. EXISTING CODE MUST NOT BE MASS-MIGRATED NOW

The existing VAYREN codebase may contain Python implementations of functionality that will eventually belong to Rust or egui.

Do NOT migrate all of that code now.

Existing code should continue working.

The migration will happen gradually.

---

# 5. CONTINUOUS / OPPORTUNISTIC MIGRATION — INVISIBLE FEATURE-DRIVEN

Existing code must migrate gradually when it is naturally touched by future development. Migration is **not a separate project** — it is a natural consequence of feature development.

**Core flow for every new feature:**

```text
NEW FEATURE
  → Identify correct target module/language (§1-§3, §8)
  → Implement NEW functionality in target architecture
  → Identify EXISTING legacy code directly related to that feature
  → Migrate that relevant legacy slice as part of the SAME feature
  → Integrate → Test → Validate → Finish
```

This means:

```text
EXISTING CODE
→ KEEP WORKING (until touched)

NEW FEATURE
→ USE FINAL ARCHITECTURE (§8)

FUTURE CHANGE TO OLD COMPONENT
→ IDENTIFY ITS RESPONSIBILITY

IF IT BELONGS TO RUST
→ MIGRATE THE RELEVANT PART TO RUST

IF IT BELONGS TO PYTHON
→ KEEP IT IN PYTHON

IF IT IS UI
→ MIGRATE THE RELEVANT UI PART TO RUST + EGUI
```

**Invisible migration principle — ask automatically:**

1. Is there existing legacy code responsible for this feature?
2. Can that code safely move toward the target architecture?
3. Is it directly related to the feature?
4. Can it be migrated without unnecessary scope expansion?
5. Can the migrated result preserve existing behavior?

If YES → migrate it **as part of the feature**. Do NOT leave the old implementation untouched merely because the user did not explicitly say "migrate this". The feature request itself is sufficient context.

**Do NOT migrate unrelated code** — only code directly required by, blocking, adjacent to, or necessary to remove the legacy implementation of that feature. See §13 for scope limits.

**No migration debt by default:** Do NOT create new legacy code around a feature when the target architecture already defines where the feature belongs. Prefer `NEW FEATURE → target architecture immediately` over `→ legacy → "migrate later"`.

**Speed principle:** `FEATURE → NEW CODE → RELATED LEGACY REMOVED → TARGET EXPANDS`. Each feature must leave the touched area more migrated than before. Over time `Legacy ██████████ → ██`, `Target ██ → ██████████` — incrementally, never big-bang.

Do NOT migrate unrelated components just because they are old.

---

# 6. UI MIGRATION EXAMPLE

If an existing UI component is currently implemented in Python:

```text
Old Python UI
     ↓
Do NOT migrate immediately
```

Later, when that UI component naturally needs a feature/change:

```text
Old Python UI
     ↓
Feature request
     ↓
Relevant UI work
     ↓
Migrate affected portion
     ↓
Rust + egui
```

Eventually:

```text
Old Python UI → Rust + egui
```

The same principle applies to Rust-owned core functionality.

---

# 7. CORE MIGRATION EXAMPLE

If an existing Python component performs a Rust-owned responsibility:

```text
Existing Python
      ↓
Keep working for now
```

When future development naturally touches that component:

```text
Existing Python
      ↓
Identify Rust-owned responsibility
      ↓
Migrate affected functionality
      ↓
Rust
```

Do not rewrite unrelated Python code.

---

# 8. NEW DEVELOPMENT ALWAYS USES THE FINAL ARCHITECTURE

This is the most important rule.

From the adoption of this constitution:

```text
NEW CORE FEATURE
→ Rust

NEW PERFORMANCE FEATURE
→ Rust

NEW MARKET/DATA FEATURE
→ Rust

NEW INDICATOR
→ Rust

NEW RISK FEATURE
→ Rust

NEW EXECUTION FEATURE
→ Rust

NEW BACKTEST FEATURE
→ Rust

NEW STRATEGY
→ Python

NEW AI/ML FEATURE
→ Python

NEW RESEARCH FEATURE
→ Python

NEW NATIVE UI
→ Rust + egui
```

Do not create new functionality in the old architecture simply because similar legacy code exists.

---

# 9. LANGUAGE IS DETERMINED BY RESPONSIBILITY

Never select a language based on:

* personal preference
* convenience
* familiarity
* shorter code
* AI preference
* existing legacy language

Instead:

```text
WHAT IS BEING BUILT?
        ↓
WHAT RESPONSIBILITY DOES IT HAVE?
        ↓
WHICH DOMAIN OWNS IT?
        ↓
WHICH LANGUAGE OWNS THAT DOMAIN?
        ↓
IMPLEMENT
```

Therefore:

```text
Core / Performance → Rust
Strategy / AI / Research → Python
Native UI → Rust + egui
```

---

# 10. NO LANGUAGE CREEP

Do not introduce another programming language for new VAYREN functionality.

Do not introduce another UI framework.

Do not replace the defined technology stack because another technology appears easier or newer.

Any change to the language architecture requires explicit architectural approval.

---

# 11. NO BIG-BANG REWRITE

Never perform:

```text
Entire Python repository
        ↓
Entire Rust rewrite
```

Instead:

```text
Existing system
      ↓
New work follows new architecture
      ↓
Old component is naturally touched
      ↓
Relevant part migrates
      ↓
Validate
      ↓
Continue
```

Migration is intentionally incremental.

---

# 12. PRESERVE EXISTING BEHAVIOR

When gradually migrating an old component:

* preserve existing functionality
* preserve intended behavior
* preserve data semantics
* preserve user workflows
* preserve important edge cases

Do not combine migration with unrelated redesign unless explicitly requested.

---

# 13. MIGRATION SHOULD BE SMALL AND NATURAL — BUT FAST THROUGH FEATURES

When touching legacy code:

> Migrate only the part that belongs in the new architecture and is relevant to the current task — **smallest useful slice**, not 50k LOC.

Do not turn every feature request into a repository-wide migration.

**Migration priority for a feature:**

1. Direct feature implementation
2. Its immediate dependencies
3. Its direct callers/consumers
4. Its obsolete compatibility layer
5. Only then nearby cleanup

**Completed feature checklist:**

- [ ] New functionality uses target architecture (§8)
- [ ] Directly related legacy code was identified (§5)
- [ ] Relevant legacy migrated where practical, redundant old removed where safe
- [ ] No unrelated migration performed
- [ ] Tests + `scripts/validate_imports.py` / `validate_structure.py` pass
- [ ] Existing behavior preserved (§12)

**When NOT to migrate:** unrelated to feature, would massively expand scope, unsafe architectural change, target not ready, risk of data loss.

Example:

```text
User asks:
"Add a new feature to old chart."

Correct:
→ Work on chart
→ Implement new chart feature in Rust+egui (§3)
→ Migrate directly related legacy chart/UI slice
→ Preserve behavior → Keep unrelated legacy untouched

Incorrect:
→ Rewrite the entire application
→ Migrate unrelated strategy/database/execution
```

---

# 14. AI AGENT RULE

Every future AI coding agent must follow this constitution.

**Before creating new code, determine:**

```text
1. What am I building?
2. What responsibility does it have?
3. Which domain owns it?
4. Which language is mandatory for that domain? (§9)
5. Is this new functionality or legacy code modification?
6. If legacy code is being touched, is migration appropriate for this specific work? (§5, §13)
```

**During feature implementation (invisible migration):**

1. Is there legacy code for this feature? → Can it safely move to target? → Is it directly related? → Without scope explosion? → Preserving behavior? → **Migrate it in the same feature** (§5).
2. Do NOT create new legacy code when target already defined.
3. Do NOT migrate unrelated modules.

**Decision model:**

- NEW functionality → follow final architecture (§8)
- Modification to EXISTING code → preserve behavior unless naturally migrating (§12)
- Existing code meaningfully touched → consider gradual migration (§5, §13)
- Would require large unrelated changes → do NOT auto-migrate
- Large-scale migration explicitly approved → follow scope + §5-§13

Then implement and verify with `make check`.

---

# 15. WHEN UNCERTAIN

If the responsibility is unclear:

```text
DO NOT GUESS.
DO NOT INVENT A NEW LANGUAGE.
DO NOT INVENT A NEW UI FRAMEWORK.
DO NOT MASS-MIGRATE.
```

First determine the correct architectural ownership.

---

# 16. FINAL ARCHITECTURE

```text
                         VAYREN
                            │
             ┌──────────────┼──────────────┐
             │              │              │
           RUST           PYTHON       RUST + EGUI
             │              │              │
        CORE / SPEED     STRATEGY          UI
        MARKET/DATA      AI/ML
        INDICATORS       RESEARCH
        RISK
        EXECUTION
        BACKTEST
```

---

# 17. FINAL PRINCIPLE

## BUILD NEW, MIGRATE GRADUALLY — FEATURE IS THE VEHICLE

```text
OLD CODE
→ Keep working

NEW CODE
→ Final architecture immediately

OLD CODE WHEN TOUCHED BY A FEATURE
→ Migrate directly related slice as part of that feature (§5)

NO BIG-BANG REWRITE
```

**Long-term model — feature development IS migration:**

```text
FEATURE = NEW FUNCTIONALITY + TARGET-ARCHITECTURE ADOPTION + RELEVANT LEGACY MIGRATION
```

The fundamental rule is:

> **Do not migrate everything today. Build everything new in the correct architecture today, and let every feature automatically carry the directly related legacy toward the target — incrementally, invisibly, and fast enough that the legacy steadily shrinks without a dedicated migration project. Think: FEATURE → NEW CODE → RELATED LEGACY REMOVED → TARGET EXPANDS.**

---

# 18. AI QUICK REFERENCE

Whenever a future AI agent needs to decide what technology to use, use this:

```text
CORE / PERFORMANCE
→ RUST

STRATEGY
→ PYTHON

AI / ML
→ PYTHON

RESEARCH
→ PYTHON

NATIVE UI
→ RUST + EGUI
```

This mapping is mandatory for NEW development.

Existing legacy code is migrated gradually and only when appropriate.

---

# 19. FILE AUTHORITY

This file:

```text
ARCHITECTURE_CONSTITUTION.md
```

is the highest-level architectural reference for language ownership and future development direction.

AI agents must read and follow it before making architectural decisions.

Do not modify this constitution as part of ordinary feature development.

Changes to this constitution require explicit architectural approval.
