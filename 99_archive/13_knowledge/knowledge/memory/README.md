# knowledge/memory/ — AI Agent Memory

## What Is AI Memory?

**AI memory** is persistent context that AI agents use to maintain continuity across sessions. When an AI works on this repository, it reads `current-context.yaml` to understand:

- What was being worked on last time?
- What decisions were made?
- What should be done next?

---

## Why This Exists

Without persistent memory, every AI session starts from scratch. The AI doesn't know what was done yesterday, what decisions were made, or what the current priorities are.

With memory:
- AI sessions are continuous, not isolated
- Context builds over time
- The AI becomes more effective the longer it works here

---

## Files

| File | Purpose |
|---|---|
| `current-context.yaml` | Current state, focus, next priorities — updated after every session |
| `session-logs/` | Raw logs of AI sessions for reference |

---

## Update After Every Session

```yaml
# current-context.yaml
last_session: "what was just worked on"
current_focus: "what is being worked on now"
active_departments: ["market", "signals", ...]
next_priorities: ["what to do next"]
decisions_pending: []
```
