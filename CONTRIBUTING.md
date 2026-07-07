# Contributing to Vayren Core

## What This Document Is

This document tells **you** (the human founder) and **future contributors** how to work inside this repository. Even though you are a solo developer today, establishing good practices now means that when you eventually hire people — or when future-you returns to the code after a break — everything makes sense.

---

## The Solo + AI Workflow

This repository is designed for one human + AI collaboration. The AI handles the majority of coding, reviewing, testing, and documentation. You (the human) handle vision, decisions, and final approval.

### Every Session

```
1. READ  13_knowledge/knowledge/memory/current-context.yaml
   → "What was I working on? What's next?"

2. READ  13_knowledge/knowledge/decisions/ (if relevant)
   → "What decisions have been made about this area?"

3. MAKE changes using AI
   → Follow conventions in AGENTS.md

4. RUN  make check
   → "Does everything still work?"

5. UPDATE 13_knowledge/knowledge/memory/current-context.yaml
   → "What did I just do? What's next?"
```

---

## How to Add Code

The repository follows a consistent pattern. Every new piece of functionality follows this order:

```
1. MODEL first    → What IS this thing?          (models/file.py)
2. EVENT next     → What MESSAGE does it send?    (events/file.py)
3. SERVICE last   → What ACTION does it perform?  (services/file.py)
4. TEST always    → Does it WORK correctly?       (tests/test_file.py)
```

### Example: Adding a New Signal

```python
# 1. models/signal_type.py — Define what the signal is
# 2. events/signal_type_updated.py — Define when it changes
# 3. services/signal_type_compute.py — Define how it's calculated
# 4. tests/test_signal_type.py — Verify it works
```

### Rules

- Every new Python file must have a corresponding test file
- Every public function must have type hints
- Every model should be a frozen dataclass (immutable)
- Every event name must be past tense

---

## How to Add Knowledge

| What Happened | Where to Record It |
|---|---|
| Architecture decision | `13_knowledge/knowledge/decisions/NNN-title.md` |
| Experiment completed | `13_knowledge/knowledge/experiments/YYYY-MM-name/summary.md` |
| Trading day complete | `13_knowledge/knowledge/journals/YYYY-MM-DD.md` |
| New pattern discovered | `13_knowledge/knowledge/patterns/name.md` |
| New AI prompt developed | `13_knowledge/knowledge/prompts/name.md` |
| Policy established | `13_knowledge/knowledge/policies/name.yaml` |

---

## Commit Style

Use **conventional commits**:

```
feat:     New feature
fix:      Bug fix
docs:     Documentation only
refactor: Code change that fixes nothing and adds nothing
test:     Adding or fixing tests
chore:    Build process, tooling, dependencies
```

Examples:

```
feat: add RSI momentum strategy
fix: correct bar timestamp timezone handling
docs: update market department README with data flow diagram
refactor: extract commission calculation into separate service
test: add property-based tests for Currency arithmetic
chore: upgrade ruff to v0.6.0
```

---

## What Makes a Good Contribution

| ✅ Good | ❌ Not Good |
|---|---|
| One focused change per commit | Multiple unrelated changes in one commit |
| Tests included | No tests |
| README updated if public API changed | No documentation |
| `make check` passes before committing | Skips validation |
| Follows department conventions | Inconsistent naming or structure |

---

## One Sentence Summary

> CONTRIBUTING.md defines how to work in this repository — whether you're a solo founder, an AI agent, or a future hire.

---

## Continue to the Next Lesson

→ `Makefile` — How to run this repository
