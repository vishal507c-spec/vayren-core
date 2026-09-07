"""Migration benchmarks — Python reference kernels vs Rust kernels (stdlib only).

Compares the pre-migration Python math (frozen reference copies, labeled
BASELINE) against the Rust-owned kernels (RUST) on identical inputs.
Kernel-vs-kernel only (no IO/parsing in the timed region) unless noted.
Prints median-of-N timings; never claims more than measured.
"""

from __future__ import annotations

import math
import random
import statistics
import time

REPS = 7


def _median(fn) -> float:
    samples = []
    for _ in range(REPS):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


# ── transition legality (old dict literal vs materialized frozenset) ────────

_OLD_TABLE = {
    "CREATED": frozenset({"VALIDATED", "REJECTED"}),
    "VALIDATED": frozenset({"SUBMITTED", "REJECTED", "EXPIRED"}),
    "SUBMITTED": frozenset({"ACKNOWLEDGED", "REJECTED", "EXPIRED", "UNKNOWN"}),
    "ACKNOWLEDGED": frozenset(
        {
            "PARTIALLY_FILLED",
            "FILLED",
            "REJECTED",
            "CANCEL_PENDING",
            "MODIFY_PENDING",
            "EXPIRED",
            "UNKNOWN",
        }
    ),
    "PARTIALLY_FILLED": frozenset(
        {"PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "MODIFY_PENDING", "EXPIRED", "UNKNOWN"}
    ),
    "CANCEL_PENDING": frozenset({"CANCELLED", "FILLED", "UNKNOWN"}),
    "MODIFY_PENDING": frozenset({"MODIFIED", "FILLED", "UNKNOWN"}),
    "MODIFIED": frozenset(
        {
            "PARTIALLY_FILLED",
            "FILLED",
            "REJECTED",
            "CANCEL_PENDING",
            "MODIFY_PENDING",
            "EXPIRED",
            "UNKNOWN",
        }
    ),
    "UNKNOWN": frozenset(),
    "FILLED": frozenset(),
    "REJECTED": frozenset(),
    "CANCELLED": frozenset(),
    "EXPIRED": frozenset(),
}
_STATES = list(_OLD_TABLE)


def bench_transition_checks() -> tuple[float, float]:
    pairs = [(a, b) for a in _STATES for b in _STATES]

    def baseline() -> None:
        for a, b in pairs:
            _ = b in _OLD_TABLE[a] or (b == "UNKNOWN" and a != "UNKNOWN")

    from execution.models.order_state import OrderState
    from execution.native_order_state import transition_allowed

    rust_pairs = [(OrderState(a), OrderState(b)) for a, b in pairs]

    def rust() -> None:
        for a, b in rust_pairs:
            _ = transition_allowed(a, b)

    return _median(baseline), _median(rust)


# ── drawdown / equity / sharpe (reference loops vs native) ─────────────────


def _ref_drawdown(equities: list[float]) -> tuple[float, float]:
    if not equities:
        return 0.0, 0.0
    peak = equities[0]
    max_pct = 0.0
    max_abs = 0.0
    for equity in equities:
        if equity > peak:
            peak = equity
        dd_abs = peak - equity
        dd_pct = (dd_abs / peak * 100.0) if peak else 0.0
        if dd_pct > max_pct:
            max_pct = dd_pct
        if dd_abs > max_abs:
            max_abs = dd_abs
    return max_pct, max_abs


def _ref_equity(initial: float, pnls: list[float]) -> tuple[list[float], list[float]]:
    equity = initial
    peak = initial
    eqs: list[float] = []
    dds: list[float] = []
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dds.append((peak - equity) / peak * 100.0 if peak else 0.0)
        eqs.append(equity)
    return eqs, dds


def _ref_sharpe(pnls: list[float], bars: list[float], initial: float) -> float | None:
    if len(pnls) < 2:
        return None
    equity = initial
    returns: list[float] = []
    for pnl in pnls:
        before = equity
        equity += pnl
        if before <= 0.0:
            continue
        returns.append(equity / before - 1.0)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0.0:
        return None
    std = math.sqrt(variance)
    mean_bars = sum(bars) / len(pnls) if pnls else 1
    annual = math.sqrt((252.0 * 25.0) / max(1.0, mean_bars))
    return (mean / std) * annual if std else None


def bench_numeric() -> dict[str, tuple[float, float]]:
    from backtest import native_metrics

    rng = random.Random(7)
    curve = [1000.0]
    for _ in range(100_000):
        curve.append(curve[-1] + rng.uniform(-8.0, 8.2))
    pnls = [rng.uniform(-500.0, 500.0) for _ in range(20_000)]
    bars = [float(rng.randint(1, 50)) for _ in range(20_000)]
    out: dict[str, tuple[float, float]] = {}
    out["drawdown_100k"] = (
        _median(lambda: _ref_drawdown(curve)),
        _median(lambda: native_metrics.max_drawdown(curve)),
    )
    out["equity_20k"] = (
        _median(lambda: _ref_equity(1_000_000.0, pnls)),
        _median(lambda: native_metrics.equity_curve_points(1_000_000.0, pnls)),
    )
    out["sharpe_20k"] = (
        _median(lambda: _ref_sharpe(pnls, bars, 1_000_000.0)),
        _median(lambda: native_metrics.sharpe(pnls, bars, 1_000_000.0)),
    )
    return out


# ── aggregation accumulation loop ──────────────────────────────────────────


def _ref_bucket_accum(
    days: list[int],
    secs: list[int],
    o: list[float],
    h: list[float],
    low: list[float],
    c: list[float],
    v: list[float],
    tf: int,
    session: int,
) -> dict:
    buckets: dict = {}
    for i in range(len(days)):
        if tf < 86400:
            index = (secs[i] - session) // tf if secs[i] >= session else secs[i] // tf
            key = (days[i], index)
        elif tf == 86400:
            key = (days[i], 0)
        else:
            key = (days[i] - (days[i] - 1) % 7, 0)
        acc = buckets.get(key)
        if acc is None:
            buckets[key] = [o[i], h[i], low[i], c[i], v[i]]
        else:
            if h[i] > acc[1]:
                acc[1] = h[i]
            if low[i] < acc[2]:
                acc[2] = low[i]
            acc[3] = c[i]
            acc[4] += v[i]
    return buckets


def bench_aggregate() -> tuple[float, float]:
    from market.native_aggregate import aggregate

    rng = random.Random(11)
    n = 60_000
    days = [100 + (i // 25) for i in range(n)]
    secs = [33300 + (i % 25) * 900 + rng.randint(0, 30) for i in range(n)]
    o = [100.0 + rng.uniform(-2, 2) for _ in range(n)]
    h = [x + rng.uniform(0, 1) for x in o]
    low = [x - rng.uniform(0, 1) for x in o]
    c = [x + rng.uniform(-1, 1) for x in o]
    v = [float(rng.randint(100, 5000)) for _ in range(n)]
    return (
        _median(lambda: _ref_bucket_accum(days, secs, o, h, low, c, v, 900, 33300)),
        _median(lambda: aggregate(days, secs, o, h, low, c, v, 900, 33300)),
    )


def main() -> int:
    print("migration benchmarks (median of 7, seconds; smaller is better)")
    base, rust = bench_transition_checks()
    print(f"  transition_check  baseline={base * 1e3:.3f}ms  rust={rust * 1e3:.3f}ms")
    for name, (b, r) in bench_numeric().items():
        print(f"  {name:14s} base={b * 1e3:8.2f}ms rust={r * 1e3:8.2f}ms x{b / max(r, 1e-12):.2f}")
    b, r = bench_aggregate()
    print(f"  aggregate_60k   base={b * 1e3:8.2f}ms rust={r * 1e3:8.2f}ms x{b / max(r, 1e-12):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
