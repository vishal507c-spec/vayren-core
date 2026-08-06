# knowledge/ — Knowledge

## What Is Knowledge?

**Knowledge** is everything the company has learned. It is the company's memory — the accumulated wisdom that makes future work faster and better.

Unlike application code (which tells computers what to do), knowledge tells **humans** why things were done, what was tried, what worked, and what didn't.

---

## Purpose

`knowledge/` is the most important folder in this repository. It contains:

- **Decisions** — Why was a certain approach chosen?
- **Experiments** — What was tried and what were the results?
- **Journals** — What happened on a given day?
- **Memory** — What is the AI currently working on?
- **Prompts** — How should AI agents behave?
- **Policies** — What are the operating rules?
- **Patterns** — How do we solve common problems?
- **References** — What external resources inform our work?

---

## Why This Folder Exists

Knowledge is the company's **competitive advantage**.

A strategy that returns 15% is valuable. The knowledge of *why* it works, *when* it fails, and *how* it was discovered is more valuable — because it lets you find the next strategy.

For a solo developer, knowledge is even more critical. You cannot remember everything. When you return to the code after a month away, `knowledge/` tells you what was happening and why decisions were made.

---

## The Compounding Effect

```
Year 1:  Sparse notes, a few ADRs, basic prompts
Year 2:  More experiments recorded, journals weekly, refined prompts
Year 3:  Rich decision history, hundreds of experiments, AI agents use context
Year 5:  Knowledge is worth more than the code. AI can operate semi-autonomously.
```

---

## What Is Inside

| Section | Purpose | When to Write |
|---|---|---|
| `decisions/` | Architecture Decision Records (ADRs) | After every major design choice |
| `experiments/` | Experiment configurations, results, summaries | After every experiment run |
| `journals/` | Trading and development daily journals | Daily or weekly |
| `memory/` | AI agent persistent context (current-context.yaml) | After every session |
| `prompts/` | AI system prompts for different tasks | When refining AI behavior |
| `policies/` | Operational rules (risk limits, data retention) | When establishing policy |
| `patterns/` | Reusable templates for common tasks | When discovering a pattern |
| `references/` | External resources, papers, articles | When finding a valuable resource |

---

## How to Create Knowledge

```bash
# Architecture decision
make adr NAME=use-postgres-for-market-data

# Daily journal
make journal

# Experiment record
make experiment NAME=rsi-xgboost-feature-search
```

---

## The Most Important Rule

> If it was learned, record it in `knowledge/`.

Every decision, every experiment result, every mistake, every insight — record it. The recording takes 5 minutes. The benefit compounds forever.

---

## Continue to the Next Lesson

This is the final department in the reading order.

You have now read all 13 departments. You understand the complete system.

→ Return to `README.md` for a refresher on the full architecture
