# VAYREN Speed Instrumentation (Phase 18, measurement only)

Evidence-based engineering-speed system. No product code is touched; this
package only appends marker/journal JSONL under `scripts/` (git-tracked
evidence, unlike `.forensics/` traces).

## Principles

- **Measure, don't fabricate** — unmeasurable = `UNMEASURED`/`NOT MEASURED`.
- **Append-only** — baselines are never overwritten; BEFORE/CURRENT/DELTA
  pairs are derived from history.
- **Equivalent tasks only** — speedups compare shared fingerprints, never
  unrelated tasks.
- **Negligible overhead** — `mark` is one JSONL line; no daemons, no hooks.

## Quick start

```
# 1. mark the loop as you work (milestones once, phases repeatedly)
python scripts/speed/__main__.py mark --task T --kind milestone --name TASK_START
python scripts/speed/__main__.py mark --task T --kind phase --name DISCOVERY
python scripts/speed/__main__.py mark --task T --kind milestone --name PLAN_READY
python scripts/speed/__main__.py mark --task T --kind milestone --name FIRST_EDIT
python scripts/speed/__main__.py mark --task T --kind milestone --name IMPLEMENTATION_COMPLETE
python scripts/speed/__main__.py mark --task T --kind milestone --name FIRST_VALIDATION
python scripts/speed/__main__.py mark --task T --kind milestone --name FINAL_GREEN
python scripts/speed/__main__.py mark --task T --kind milestone --name TASK_END

# 2. close the task into the journal (segments derived from markers)
python scripts/speed/__main__.py record --task T --class SMALL --spec "..." \
  --validation-s 17.0 --tests 82 --failures 0 --repairs 0 --human 0

# 3. plan the next task with minimal context (fresh proven plans only)
python scripts/speed/__main__.py compile-context --spec "..." --class SMALL \
  --files 00_app/app/bootstrap/bootstrap.py

# 4. dashboard + recommendations (also embedded in benchmark scoreboard)
python scripts/speed/__main__.py dashboard --format md
python scripts/speed/__main__.py recommend
```

## Layout

```
scripts/speed/
  __main__.py   CLI (mark, record, show, fingerprint, compile-context,
                recommend, dashboard)
  markers.py    phases DISCOVERY..DONE + loop milestones + segment derivation
  journal.py    append-only journal, BEFORE/CURRENT/DELTA pairs, aggregates
  context.py    fingerprints, staleness guard, context compiler, proxies,
                complexity signals
  edits.py      edit efficiency, REPAIR_TAX, first-pass quality
  bottleneck.py bottleneck ranking + evidence-gated recommendations
scripts/speed_marks.jsonl    marker stream (append-only evidence)
scripts/speed_journal.jsonl  task journal (append-only evidence)
```

## Metrics (kept separate, never mixed)

`TASK_WALL_TIME`, `VALIDATION_TIME`, `FIRST_PASS_RATE`, `REPAIR_TAX`,
`REUSE_SPEEDUP`, `HUMAN_INTERVENTIONS`. AI utilization stays in the
development log — it is not a speed multiplier.

## Tests

```
python -m pytest scripts/tests/test_speed_loop.py scripts/tests/test_speed_metrics.py
```
