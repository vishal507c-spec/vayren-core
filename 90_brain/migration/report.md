# VAYREN Migration Report

## Repository Migration Summary

- total_units: 12
- by_state: {'RUST_CANONICAL': 7, 'BLOCKED': 5}
- parity_verified: 7
- shadow_validated: 7
- rust_authoritative: 7
- python_authoritative: 5
- quarantined: 0
- migrated: 0
- blocked: 5
- stale_or_failed: 0

## Top Blockers

### core.event_bus.dispatch

- Authority: python
- State: BLOCKED
- Blocked by: none (own evidence missing)
- Parity: INCONCLUSIVE / Shadow: INCONCLUSIVE
- Required: define the Rust target for core.event_bus.dispatch, then implement it

### data.download.orchestration

- Authority: python
- State: BLOCKED
- Blocked by: none (own evidence missing)
- Parity: INCONCLUSIVE / Shadow: INCONCLUSIVE
- Required: define the Rust target for data.download.orchestration, then implement it

### market.storage.sqlite

- Authority: python
- State: BLOCKED
- Blocked by: none (own evidence missing)
- Parity: INCONCLUSIVE / Shadow: INCONCLUSIVE
- Required: run differential suite: migration parity market.storage.sqlite

### execution.session.lifecycle

- Authority: python
- State: BLOCKED
- Blocked by: none (own evidence missing)
- Parity: INCONCLUSIVE / Shadow: INCONCLUSIVE
- Required: run differential suite: migration parity execution.session.lifecycle

### chart.viewport.math

- Authority: python
- State: BLOCKED
- Blocked by: none (own evidence missing)
- Parity: INCONCLUSIVE / Shadow: INCONCLUSIVE
- Required: define the Rust target for chart.viewport.math, then implement it

## Safety

- parity_coverage: 7/12
- shadow_coverage: 7/12
- gate_failures: 0

## Integrity

- orphaned_manifests: []
- retention_missing: []
- rust_unused: []
- python_authoritative: ['chart.viewport.math', 'execution.session.lifecycle']

