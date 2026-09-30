# vayren-core — Rust-owned kernels

Single authoritative implementation of VAYREN's Rust-owned numeric and
lifecycle logic (AI_ENTRY.md §1: Core, Performance,
Market/Data, Execution, Backtest). Zero dependencies (std only).

## Modules

- `order_state` — the order lifecycle table + terminal set. The Python
  execution layer materializes its view from this table at import; it
  holds no independent copy.
- `metrics` — backtest numeric kernels: max drawdown, equity-curve
  accumulation, Sharpe ratio (bit-for-bit Python-reference semantics).
- `aggregate` — timeframe bucketing + single-pass OHLCV accumulation.
- `stats` — small statistics kernels (`mode`, Counter-compatible).

## C ABI

`lib.rs` exposes `vy_*` symbols consumed by Python via `ctypes`
(`01_core/core/native/loader.py`). Rules: fixed-width ints, `f64`,
caller-allocated buffers, no panics across the boundary, unknown inputs
fail closed. `vy_abi_version()` handshakes every load.

## Verification

- `cargo test -p vayren-core` — 27 unit tests (tables, kernels, edges).
- Python parity suites (`test_native_*`) cross-check against frozen
  references + seeded fuzz on every gate run.
- `cargo fmt --check` is part of `make check`.
