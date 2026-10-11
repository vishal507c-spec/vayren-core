# Phase 3: Slint Codegen, Cross-Crate Build, and All-Strategy Backtest Optimization Report

**Audit Date:** 2026-10-10  
**Environment:** Windows 11 Home (10.0.26200), 12 logical cores, 16 GB RAM, `rustc 1.98.1`, `python 3.11.9`  
**Author:** Principal Rust Performance & Agent Optimization Audit

---

## 1. Executive Summary

This Phase 3 audit focuses on three high-impact developer and computational feedback bottlenecks:
1. **Slint Incremental Build Latency (~51s):** Why small UI changes cause large recompilation cycles, and how architectural isolation reduces feedback latency.
2. **Cross-Crate Propagation (~45s):** Why changes in `vayren-domain` trigger downstream checks across `vayren-shell`.
3. **526-Stock Complete Backtest Pipeline (Target: 60s):** Comprehensive profiling and algorithmic optimization across 24.7 million historical OHLCV bars.

---

## 2. Slint Build Architecture & Incremental Optimization

### A. Root Cause Analysis of the ~51s Latency
- `crates/vayren-shell` compiles `ui/app.slint`, which imports all 7 screens (`market.slint`, `lab.slint`, `live.slint`, `research.slint`, `portfolio.slint`, `broker.slint`, and `broker_connection.slint`).
- `slint_build` translates this entire visual hierarchy into a single **31.6 MB generated Rust file (`app.rs`)**.
- `rustc` takes ~51s on 12 cores to parse, macro-expand, borrow-check, and generate code for this monolithic module.
- Decoupling test harnesses (`live_harness.slint`, `research_harness.slint`) in Phase 2 eliminated unnecessary triggers, but any edit to an actual screen still invalidates `app.rs`.

### B. The Modular Architecture Solution: Dedicated View Crates
The repository architecture establishes isolated embeddable view crates (`crates/vayren-*-view`):
- `crates/vayren-portfolio-view`: Compiles only `ui/portfolio_host.slint` -> **2.99 MB generated Rust** -> **11.42s build** (4.5x faster).
- `crates/vayren-strategy-lab-view`: Compiles only `ui/lab_host.slint` -> **6.12 MB generated Rust** -> **22.55s build** (2.3x faster).
- `crates/vayren-live-view`: Compiles only `ui/live_host.slint` -> **6.17 MB generated Rust** -> **24.10s build**.
- `crates/vayren-market-view`: Compiles only `ui/market_host.slint` -> **6.87 MB generated Rust** -> **30.55s build**.
- `crates/vayren-research-view`: Compiles only `ui/research_host.slint` -> **8.13 MB generated Rust** -> **32.20s build**.
- `crates/vayren-system-view`: Compiles only `ui/system_host.slint` -> **2.36 MB generated Rust** -> **9.80s build**.

**Developer Workflow Protocol:**
Targeting the specific view crate for incremental UI iterations (`cargo check -p vayren-portfolio-view`) provides feedback in **~11.4s** instead of waiting **51s** on the shell monolith.

---

## 3. Cross-Crate Compilation Analysis

### A. Why `vayren-domain` Changes Recompile `vayren-shell`
`vayren-shell` directly re-exports all domain modules in `src/lib.rs`:
```rust
pub use vayren_domain::broker_connection;
pub use vayren_domain::lab;
pub use vayren_domain::live;
pub use vayren_domain::market;
pub use vayren_domain::portfolio;
pub use vayren_domain::research_state;
pub use vayren_domain::view_model;
pub use vayren_domain::viewport;
```
When `vayren-domain` changes:
1. `rustc` rechecks `vayren-domain` in **3.7s**.
2. Downstream `vayren-shell` rechecks its 5,100-line `shell.rs` projection logic in **45.48s**.
3. **Key Finding:** Slint codegen is **not** re-run (the FNV-1a content-hash cache holds `app.rs` valid). The 45s cost is purely `rustc` typechecking the 31.6 MB `app.rs` AST alongside `shell.rs`.

---

## 4. All-Strategy Backtest Optimization & 526-Stock Profiling

### A. Dataset Dimensions & Discovery
- Total symbols: **526 stocks**
- Total historical OHLCV bars: **24,719,146 bars**
- Range: Min 2,797 bars, Max 62,824 bars, Median 61,588 bars (~47,000 bars per stock average)

### B. Computational Bottleneck Profile
1. **Indicator Calculation Inefficiency:**
   In `src/strategy/strategies/indicators.py`, `calc_sma` previously converted deques to lists on every single bar:
   ```python
   # BEFORE: Allocates a new list and slices it for every bar
   return sum(list(closes)[-period:]) / period
   ```
   Over 24.7 million bars, this created tens of millions of heap allocations.
2. **Optimization Implemented:**
   Updated `calc_sma` and `calc_range` to use zero-copy iterator consumption:
   ```python
   # AFTER: Zero-allocation reversed islice
   return sum(islice(reversed(closes), period)) / period
   ```
   - Microbenchmark (`RELIANCE`, 61,673 bars): **0.2701s -> 0.0526s (5.13x faster)**.
   - 10-Symbol SMA Backtest: **14.53s -> 12.96s** with 8,163 trades produced.

### C. Honest Evaluation of the 60-Second Target across Full 24.7M Bars
- **Arithmetic Reality:** 24.7 million bars across 526 stocks means processing ~411,000 bars per second to finish in 60s.
- In pure Python, evaluating strategy logic + maintaining position states + order accounting runs at ~36,000 bars/sec single-threaded (~680s total runtime).
- Multiprocessing is bounded by inter-process serialization overhead for 24.7 million bar objects across process boundaries.
- **Conclusion:** While indicator calculations achieved a **5.13x speedup**, processing all 24.7 million historical bars across all 526 stocks within 60s in Python would require migrating the strategy evaluation loop into native Rust kernels (per `AI_ENTRY.md` §1 architecture roadmap).

---

## 5. Verification Gate Status

- `make check-fast`: **PASS (5.00s warm)**
- `cargo test -p vayren-core --lib`: **PASS (403/403 in 0.16s)**
- `cargo test -p vayren-domain --lib`: **PASS (247/247 in 0.19s)**
- `cargo test -p vayren-shell --lib`: **PASS (49 passed, 17 bench ignored in 0.10s)**
- `cargo test -p vayren-strategy-lab-view --lib`: **PASS (1/1 in 0.00s)**
- `pytest src/app/tests`: **PASS (115/115 in 15.45s)**
- `pytest src/strategy/tests`: **PASS (196/196 in 13.34s)**
- `pytest src/broker/tests`: **PASS (197/197 in 32.25s)**
- `repo_graph.py --build`: **PASS (5,733 entities, 0 unresolved, within 3.31MB cap)**
