# knowledge/experiments/ — Experiments

## What Is an Experiment?

An **experiment** is a structured test of a hypothesis. It answers: "If I try X, will Y happen?"

In trading, experiments test things like:
- "Does adding RSI as a feature improve model accuracy?"
- "Is strategy X profitable after transaction costs?"
- "Does regime detection improve risk-adjusted returns?"

---

## Why Experiments Exist

Without systematic experimentation:
- You cannot be sure what works
- You repeat failed approaches
- You cannot reproduce past results

Every experiment is a record of: hypothesis, methodology, results, and conclusions.

---

## Directory Structure

```
experiments/YYYY-MM-experiment-name/
├── config.yaml      ← Experiment parameters
├── results.json     ← Raw results data
└── summary.md       ← Narrative summary (using template)
```

---

## Create a New Experiment

```bash
make experiment NAME=my-experiment-name
```
