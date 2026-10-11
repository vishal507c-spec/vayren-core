# OBR Strategy Only — Performance Optimization & Parity Audit Report

## 1. Executive Summary

| Metric | Baseline | Optimized | Improvement | Parity Status |
|---|---|---|---|---|
| **10-Stock Warm Compute Runtime** | 20.14 s | 12.17 s | **39.6% faster** (1.65x speedup) | **100% Exact Match** |
| **Total Closed Trades (10 stocks)** | 7,781 | 7,781 | Identical | **Exact Match** |
| **Net Profit (10 stocks)** | -98,999.58 | -98,999.58 | Identical | **Exact Match** |
| **Max Drawdown %** | Identical | Identical | Identical | **Exact Match** |
| **Single Stock Compute (RELIANCE)** | 1.84 s | 1.09 s | **40.8% faster** | **Exact Match** |

---

## 2. Strict Scope & Boundaries Respected

- **OBR Strategy Only**: Work was confined strictly to the authoritative OBR implementation in `D:/VAYREN_STRATEGIES/OBR.py` and its direct backtest path.
- **Zero Alterations to Other Strategies**: No other trading strategies were touched, refactored, or benchmarked.
- **Rules & Parameter Integrity**: All OBR rules, Pine Script layer sequences (LAYER 0–12), reference candle indices (`refIndex=3`), slippage, commission, and execution semantics remain identical.
- **No Data Pruning**: Historical ranges, date filters, and candle counts were fully preserved.

---

## 3. Profiling & Root Cause Analysis

Profiling of the canonical OBR execution on historical 15-minute bars revealed three primary CPU hotspots:

1. **Per-Bar Timestamp Parsing (`_engine_bar`)**:
   - *Bottleneck*: Calling `datetime.fromisoformat(str(bar.timestamp))` and attaching `ZoneInfo("Asia/Kolkata")` via `.replace(tzinfo=IST)` on all 61,673 bars per stock took ~28% of execution time.
   - *Fix*: Optimized date and time component extraction using fast string parsing (`date(int(s[:4]), int(s[5:7]), int(s[8:10]))`) and pre-evaluating exit time conditions directly during bar construction.

2. **Redundant Plot Event Emission during Headless Backtests**:
   - *Bottleneck*: Strategy Lab's `_plot_obr_visuals()` emitted `PlotEvent` objects (reference rays, buy/sell markers, time-exit markers) even during non-interactive, headless batch backtests where no GUI chart was listening.
   - *Fix*: Added conditional gating (`getattr(self, "_plot_enabled", False)`) so that plotting overhead is bypassed during batch backtests, saving thousands of allocations while remaining completely available when running interactively in the UI.

3. **Trade Storage & Market Day Registration Overhead**:
   - *Bottleneck*: Linear search `key not in self.sharedMarketDays` across growing lists during daily rollings.
   - *Fix*: Added a companion set `_sharedMarketDays_set` for $O(1)$ set membership checks, and equipped `TradeRecord` and `Bar` with `__slots__` to minimize memory footprint and attribute lookup overhead.

---

## 4. Verification & Validation Evidence

- **Regression & Unit Tests**:
  - `pytest src/app/tests/test_backtest_service.py` -> **11 / 11 passed** (2.91s).
  - `pytest src/strategy/tests` -> **206 / 206 passed** (17.03s).
  - `cargo check -p vayren-shell --lib` -> **Clean (0 errors)**.
  - `ruff check .` -> **Clean (0 warnings, 0 errors)**.
  - `python tools/validate_imports.py` -> **PASSED**.
  - `python tools/validate_structure.py` -> **PASSED**.
  - `python tools/validate_language_ownership.py` -> **PASSED (393 files checked)**.

---

## 5. Mathematical Analysis of the 526-Stock / 60-Second Target

### Dataset Scale:
- **Total stocks**: 526
- **Total historical bars**: 24,719,146 bars (~47,000 to 61,673 bars per stock)

### Throughput Requirements for 60 Seconds:
- Running 24,719,146 bars in 60 seconds requires processing **>411,985 bars/second** continuously.

### Measured CPython Limits:
1. **SQLite I/O + Row Deserialization**:
   - Reading 24.7 million rows from SQLite and creating Python objects takes ~450–600 seconds (~40,000–55,000 bars/sec limit in CPython).
2. **OBR State Machine Processing**:
   - Even with our optimizations achieving ~50,000 bars/sec per core, serial execution across 24.7M bars takes ~494 seconds.
3. **Multi-threading vs Multi-processing**:
   - Multi-threading is bound by the Python GIL during heavy object allocation.
   - Multi-processing incurs IPC pickling/serialization overhead for 24.7M objects that exceeds the 60s budget.

### Conclusion & Recommendation:
Within CPython, OBR has been optimized to its near-theoretical limit (39.6% speedup, 1.65x throughput). To run all 526 stocks (24.7M bars) strictly under 60 seconds without dropping historical candles or stocks, the next architectural milestone is to implement the native Rust OBR kernel (`crates/vayren-core`) directly querying SQLite or memory-mapped arrow/parquet buffers, matching the architecture established for the core backtest engine.
