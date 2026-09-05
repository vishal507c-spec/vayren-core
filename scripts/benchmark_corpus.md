# VAYREN Benchmark Corpus — realistic task specifications

Measurement harness: `scripts/benchmark.py` (`gate` = validation-cost stats,
`record` = task sample, `scoreboard` = regenerate
`scripts/benchmark_scoreboard.md`). Runs log: `scripts/benchmark_runs.jsonl`.

## Protocol (reproducibility)

- Same repository state: clean worktree at the `main` HEAD recorded with the
  sample (`starting_revision`). Same task spec. Same machine/OS where
  practical (record OS + Python version with every sample).
- Same validation requirements: the `required_validation` listed per task.
  `make check` (or the harness `gate` steps) is the default gate.
- Cold run = first sample after a clean checkout/boot (caches cold).
  Warm run = subsequent samples. Record both; never mix them into one average
  without labeling.
- Multiple samples when practical (n≥3 for machine timings). Report sample
  count, median, p95 (nearest-rank), minimum, maximum. p95 with n<20 is
  reported as measured but labeled weak.
- One lucky run never proves a speedup. BEFORE vs AFTER must use the same
  task, same state, same validation.
- Any metric that cannot actually be measured is recorded as NOT MEASURED —
  never estimated and presented as fact.
- Operational speedup (e.g. "codemod: 1000 edits → 1 command") is reported
  separately from end-to-end task wall-clock improvement.

## Task classes and specifications

Reference state for already-executed samples: `main` @ `3b383e9` plus the
recorded worktree. Future samples pin their own HEAD.

### 1. BENCH-MICRO-01 — micro feature (validator error detail)

- Objective: `scripts/validate_imports.py` error lines include the offending
  import statement text (not just the file path).
- Starting state: clean worktree at main HEAD.
- Success criteria: violating import produces `file:line: <statement>`;
  existing fixtures/validators unchanged in behavior; all current violations
  (none) still reported identically apart from the added detail.
- Required validation: `ruff check`, `ruff format --check`, `pyright`,
  `python scripts/validate_imports.py`, targeted test run.
- Risk: LOW.

### 2. BENCH-SMALL-01 — small feature (`vayren --describe` CLI)

- Objective: read-only `vayren --describe` command printing the
  architecture snapshot (`SystemModel` summary: components, capabilities,
  unresolved consumers) to stdout; no runtime mutation.
- Starting state: clean worktree at main HEAD.
- Success criteria: exit 0 with no display; output matches
  `build_snapshot()` content; `--help` documents it; ≥3 new tests.
- Required validation: full `make check` equivalent.
- Risk: MEDIUM (new public surface; must not start the app on import).

### 3. BENCH-UI-01 — UI change (watchlist search/filter)

- Objective: case-insensitive substring filter box above the watchlist stock
  list; filters rows only, never mutates watchlist membership, selection, or
  quotes; follows `chart/theme.py` palette-only QSS (no hardcoded colors).
- Starting state: clean worktree at main HEAD.
- Success criteria: typing filters to matching symbols; clearing restores
  full list with selection intact; geometry unchanged at 1280×760.
- Required validation: `04_chart` tests + app tests + `ruff` + validators.
- Risk: MEDIUM (Qt layout behavior).

### 4. BENCH-BUGFIX-01 — bugfix (DeprecationWarning)

- Objective: eliminate the `QTableWidgetItem.setTextAlignment(int)` deprecation
  warning emitted from `06_backtest/backtest/ui/analytics_views.py:240`
  (observed in the test run warnings summary).
- Starting state: clean worktree at main HEAD.
- Success criteria: full `pytest` warnings summary no longer contains the
  deprecation; rendered alignment byte-identical; no behavior change.
- Required validation: backtest UI tests + full suite.
- Risk: LOW.

### 5. BENCH-STRATEGY-01 — strategy change (SMA volume filter)

- Objective: add an integer `min_volume` parameter (default 0 = disabled) to
  `05_strategy/strategy/strategies/sma.py`; bars below it are skipped without
  touching `prev_*` state; param spec registered with bounds.
- Starting state: clean worktree at main HEAD.
- Success criteria: default 0 reproduces current signals exactly on the
  existing SMA fixtures; new tests cover skip + disabled paths.
- Required validation: strategy tests + backtest tests + gate.
- Risk: LOW (Python-owned strategy domain per constitution).

### 6. BENCH-BACKTEST-01 — backtest change (config field)

- Objective: add `max_position_size: float | None = None` to `BacktestConfig`
  with `0 < size ≤ initial_capital` validation in `validate_backtest_form`;
  `None` preserves current behavior exactly.
- Starting state: clean worktree at main HEAD.
- Success criteria: invalid values rejected with label-only errors; valid
  configs run unchanged; roundtrip (`to_dict/from_dict` style) preserved.
- Required validation: backtest tests + gate.
- Risk: MEDIUM (public config contract).

### 7. BENCH-REFACTOR-01 — refactor (public-surface imports)

- Objective: replace cross-module internal-path imports
  (`from market.models.bar import Bar` style) with public-surface imports
  (`from market import Bar`) in `05_strategy`/`06_backtest`; no runtime
  behavior change.
- Starting state: clean worktree at main HEAD.
- Success criteria: `grep` finds no remaining internal cross-module imports
  in the two modules; full suite green; validator still passes.
- Required validation: full gate.
- Risk: LOW (import binding only; verify no import cycles).

### 8. BENCH-TEST-01 — test change (gate-coverage regression test) — EXECUTED

- Objective: add a regression test proving every `tests/` directory is
  executed by BOTH default `pytest` (testpaths) and `scripts/run_tests.py`
  (PARTS).
- Starting state: `main` @ `3b383e9` + validation-gate restoration worktree.
- Success criteria: test fails when a tests dir is uncovered (demonstrated
  red), passes after wiring; test itself is covered by both runners.
- Required validation: new tests + full gate. Risk: LOW.
- Sample recorded in `scripts/benchmark_runs.jsonl` (`BENCH-TEST-01`).

### 9. BENCH-ARCH-01 — architecture/layering change

- Objective: eliminate remaining backtest→strategy internal-path imports
  (`strategy.models.*`, `strategy.registry`, `strategy.runtime`) in favor of
  the `strategy/__init__.py` public surface, or document each exception with
  a contract reason in `module_contracts.md`.
- Starting state: clean worktree at main HEAD.
- Success criteria: no internal-path cross-module imports remain (or each is
  contract-documented); validator extended only if it can check this
  mechanically without false positives.
- Required validation: full gate.
- Risk: MEDIUM (import graph + potential cycles).

### 10. BENCH-MIGRATE-01 — migration/transformation (Kite interval IDs) — OBSOLETE

- Finding (verified 2026-09-05): the objective is ALREADY satisfied.
  `KITE_INTERVAL_IDS` lives in `02_data/data/provider/zerodha/adapter.py:42`;
  `settings.py` holds only canonical broker-agnostic `INTERVAL_LABEL` /
  `INTERVAL_MINUTES` with an explicit comment that the adapter maps them.
  There is nothing left to migrate — executing this task would be fake work.
- Verification (instead of migration): provider isolation + settings tests,
  21 passed. Recorded as NOT MEASURED-as-migration with this evidence.

## Executed samples

All 10 specs executed 2026-09-05 (see `scripts/benchmark_runs.jsonl`):

- AEOS-PYRIGHT-01 (refactor, prior turn): 22 pyright errors → 0.
- BENCH-TEST-01 (test, prior turn): gate-coverage regression test.
- BENCH-MICRO-01, BENCH-SMALL-01, BENCH-UI-01, BENCH-BUGFIX-01,
  BENCH-STRATEGY-01, BENCH-BACKTEST-01, BENCH-REFACTOR-01, BENCH-ARCH-01:
  implemented + impact-validated + recorded with wall-clock.
- BENCH-MIGRATE-01: OBSOLETE (objective already satisfied — verified, not
  executed as migration).
- AEOS-QT-FIX (bugfix, investigation): live-QThread teardown root cause
  established by probes; autouse fixture fix; full suite RC=0, 25/25.
