# knowledge/decisions/ — Architecture Decision Records

## What Is an Architecture Decision Record?

An **Architecture Decision Record (ADR)** is a short document that captures an important architectural decision. It answers:

- **Context:** What was the problem?
- **Decision:** What did we choose?
- **Alternatives:** What else did we consider?
- **Consequences:** What trade-offs did we accept?

---

## Why ADRs Exist

Without ADRs, decisions are made and forgotten. Six months later, no one knows *why* a particular approach was chosen. Future developers (or future you) might reverse a decision without understanding the reasoning, repeating past mistakes.

ADRs make decisions **explicit, searchable, and reviewable**.

---

## Template

```
knowledge/decisions/000-template.md
```

Copy this file to create a new ADR:

```bash
make adr NAME=my-decision-title
```

---

## Index

| # | Title | Date | Status |
|---|---|---|---|
| 000 | (Template) | — | — |

Status values: Proposed, Accepted, Deprecated, Superseded
