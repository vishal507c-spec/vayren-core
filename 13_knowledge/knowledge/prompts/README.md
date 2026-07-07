# knowledge/prompts/ — AI Prompts

## What Are Prompts?

**Prompts** are instructions that tell AI agents how to behave for specific tasks. They define the AI's role, context, and expected output format.

---

## Why This Exists

Different tasks require different AI behavior:
- Writing strategy code requires different instructions than reviewing architecture
- Research requires different instructions than debugging

By storing prompts in `knowledge/prompts/`, they become:
- Reusable (use the same prompt every time)
- Refinable (improve prompts based on results)
- Auditable (see what instructions were used)

---

## Available Prompts

| Prompt | Purpose |
|---|---|
| `strategy-dev.md` | Instructions for developing new trading strategies |
| `research-agent.md` | Instructions for AI research agents |
| `code-reviewer.md` | Instructions for code review |
| `architect-review.md` | Instructions for architecture review |
