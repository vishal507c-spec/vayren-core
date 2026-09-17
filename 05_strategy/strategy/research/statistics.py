"""Statistical evidence — confidence intervals, significance, Monte Carlo, benchmark.

All math is real and deterministic. Approximations (normal CI, normal p-value)
are labeled as such in the output. Anything without enough data returns an
explicit INSUFFICIENT status — never a manufactured number.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any


def _pnls_of(trades: list[Any]) -> list[float]:
    out: list[float] = []
    for trade in trades:
        try:
            value = trade.get("pnl", 0) if isinstance(trade, dict) else getattr(trade, "pnl", 0)
            out.append(float(value or 0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _normal_cdf(value: float) -> float:
    return 0.5 * math.erfc(-value / math.sqrt(2.0))


@dataclass(frozen=True)
class EvidenceStats:
    """Observation vs evidence for one trade stream."""

    n: int
    mean: float | None
    ci_low: float | None
    ci_high: float | None
    ci_method: str
    t_stat: float | None
    p_value: float | None
    p_method: str
    cohens_d: float | None
    win_rate: float | None
    win_rate_ci: tuple[float | None, float | None]
    status: str
    notes: tuple[str, ...] = ()


def describe_evidence(trades: list[Any] | tuple[Any, ...]) -> EvidenceStats:
    """Confidence interval + significance + effect size for trade expectancy."""
    pnls = _pnls_of(list(trades or []))
    n = len(pnls)
    if n == 0:
        return EvidenceStats(
            n=0,
            mean=None,
            ci_low=None,
            ci_high=None,
            ci_method="n/a",
            t_stat=None,
            p_value=None,
            p_method="n/a",
            cohens_d=None,
            win_rate=None,
            win_rate_ci=(None, None),
            status="INSUFFICIENT",
            notes=("no trades — nothing to describe",),
        )
    mean = sum(pnls) / n
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / n
    if n < 2:
        return EvidenceStats(
            n=n,
            mean=mean,
            ci_low=None,
            ci_high=None,
            ci_method="n/a",
            t_stat=None,
            p_value=None,
            p_method="n/a",
            cohens_d=None,
            win_rate=win_rate,
            win_rate_ci=(None, None),
            status="INSUFFICIENT",
            notes=("single trade — interval undefined",),
        )
    var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    notes: list[str] = []
    if sd <= 0:
        return EvidenceStats(
            n=n,
            mean=mean,
            ci_low=mean,
            ci_high=mean,
            ci_method="degenerate (zero variance)",
            t_stat=None,
            p_value=None,
            p_method="n/a",
            cohens_d=None,
            win_rate=win_rate,
            win_rate_ci=(win_rate, win_rate),
            status="INSUFFICIENT",
            notes=("zero variance — significance undefined",),
        )
    se = sd / math.sqrt(n)
    ci_low, ci_high = mean - 1.96 * se, mean + 1.96 * se
    t_stat = mean / se
    p_value = 2.0 * (1.0 - _normal_cdf(abs(t_stat)))
    cohens_d = mean / sd
    wr_se = math.sqrt(win_rate * (1.0 - win_rate) / n)
    wr_ci = (max(0.0, win_rate - 1.96 * wr_se), min(1.0, win_rate + 1.96 * wr_se))
    if n < 30:
        notes.append(f"small sample (n={n}) — normal approximation is rough")
        status = "WEAK"
    else:
        status = "OK"
    if ci_low > 0:
        notes.append("95% CI excludes zero — positive expectancy at 5% level (approx)")
    elif ci_high < 0:
        notes.append("95% CI excludes zero — negative expectancy at 5% level (approx)")
    else:
        notes.append("95% CI includes zero — effect not significant at 5% level (approx)")
    return EvidenceStats(
        n=n,
        mean=mean,
        ci_low=ci_low,
        ci_high=ci_high,
        ci_method="normal approximation (mean ± 1.96·SE)",
        t_stat=t_stat,
        p_value=p_value,
        p_method="two-sided normal approximation",
        cohens_d=cohens_d,
        win_rate=win_rate,
        win_rate_ci=wr_ci,
        status=status,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class MonteCarloResult:
    """Trade-sequence sensitivity from deterministic reshuffling."""

    n_paths: int
    seed: int
    total_pnl_p5: float | None
    total_pnl_p50: float | None
    total_pnl_p95: float | None
    max_dd_p50: float | None
    max_dd_p95: float | None
    prob_profit: float | None
    status: str
    note: str = ""


def _max_drawdown_abs(pnls: list[float]) -> float:
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return sorted_values[low]
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def run_monte_carlo(
    trades: list[Any] | tuple[Any, ...], n_paths: int = 1000, seed: int = 7
) -> MonteCarloResult:
    """Reshuffle trade order deterministically; totals are order-invariant.

    Total P&L never changes under reshuffling (same multiset), so the
    informative outputs are the drawdown distribution and the profit
    probability under resampled subsets (bootstrap with replacement models
    trade-sequence risk honestly).
    """
    pnls = _pnls_of(list(trades or []))
    if len(pnls) < 5:
        return MonteCarloResult(
            n_paths=0,
            seed=seed,
            total_pnl_p5=None,
            total_pnl_p50=None,
            total_pnl_p95=None,
            max_dd_p50=None,
            max_dd_p95=None,
            prob_profit=None,
            status="INSUFFICIENT",
            note="fewer than 5 trades — resampling meaningless",
        )
    rng = random.Random(seed)
    paths = max(100, min(int(n_paths), 5000))
    totals: list[float] = []
    drawdowns: list[float] = []
    for _ in range(paths):
        sample = [rng.choice(pnls) for _ in pnls]
        totals.append(sum(sample))
        drawdowns.append(_max_drawdown_abs(sample))
    totals.sort()
    drawdowns.sort()
    prob_profit = sum(1 for t in totals if t > 0) / len(totals)
    return MonteCarloResult(
        n_paths=paths,
        seed=seed,
        total_pnl_p5=_percentile(totals, 5),
        total_pnl_p50=_percentile(totals, 50),
        total_pnl_p95=_percentile(totals, 95),
        max_dd_p50=_percentile(drawdowns, 50),
        max_dd_p95=_percentile(drawdowns, 95),
        prob_profit=prob_profit,
        status="OK",
        note=f"bootstrap with replacement, {paths} paths, seed {seed}",
    )


@dataclass(frozen=True)
class BenchmarkResult:
    """Strategy vs buy & hold over the same experiment window."""

    strategy_return_pct: float | None
    benchmark_return_pct: float | None
    excess_return_pct: float | None
    per_symbol_buy_hold: tuple[tuple[str, float], ...] = ()
    status: str = "OK"
    assumption: str = ""
    note: str = ""


def compare_buy_hold(
    total_pnl: float,
    initial_capital: float,
    windows: dict[str, tuple[Any, ...]] | None,
    traded_symbols: tuple[str, ...] | list[str] = (),
) -> BenchmarkResult:
    """Equal-weighted buy & hold across traded symbols vs strategy return.

    Benchmark assumes capital split equally across traded symbols, fully
    invested from each symbol's first executed open to its last executed
    close. Winners/losers are price returns; the comparison is period-return
    to period-return on the same capital base.
    """
    if not initial_capital or initial_capital <= 0:
        return BenchmarkResult(
            None,
            None,
            None,
            (),
            "UNAVAILABLE",
            "",
            "initial capital missing — return undefined",
        )
    strategy_ret = total_pnl / initial_capital * 100.0
    if not windows or not traded_symbols:
        return BenchmarkResult(
            strategy_ret,
            None,
            None,
            (),
            "UNAVAILABLE",
            "equal-weighted, fully invested per symbol",
            "no executed bar windows — buy & hold unavailable",
        )
    per_symbol: list[tuple[str, float]] = []
    for symbol in sorted({str(s) for s in traded_symbols}):
        window = windows.get(symbol)
        if not window:
            continue
        try:
            first = window[0]
            last = window[-1]
            o0 = float(first.open if not isinstance(first, dict) else first.get("open", 0))
            c1 = float(last.close if not isinstance(last, dict) else last.get("close", 0))
        except (TypeError, ValueError, AttributeError, IndexError):
            continue
        if o0 <= 0:
            continue
        per_symbol.append((symbol, (c1 - o0) / o0 * 100.0))
    if not per_symbol:
        return BenchmarkResult(
            strategy_ret,
            None,
            None,
            (),
            "UNAVAILABLE",
            "equal-weighted, fully invested per symbol",
            "no usable open/close pairs in windows",
        )
    bench = sum(r for _, r in per_symbol) / len(per_symbol)
    return BenchmarkResult(
        strategy_return_pct=strategy_ret,
        benchmark_return_pct=bench,
        excess_return_pct=strategy_ret - bench,
        per_symbol_buy_hold=tuple(per_symbol),
        status="OK",
        assumption="equal capital per symbol, fully invested first-open to last-close",
        note=f"buy & hold over {len(per_symbol)} symbols",
    )


def overfitting_warnings(
    trade_count: int,
    variants_tested: int = 1,
    has_out_of_sample: bool = False,
    unstable_params: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Honest data-snooping warnings — triggers only, never conclusions."""
    warnings: list[str] = []
    if trade_count < 30:
        warnings.append(f"small sample (n={trade_count}) — evidence is exploratory")
    if variants_tested > 10:
        warnings.append(
            f"{variants_tested} configurations tested — multiple-comparison risk; "
            "prefer the pre-registered configuration"
        )
    elif variants_tested > 1:
        warnings.append(
            f"{variants_tested} configurations tested — report selection basis honestly"
        )
    if not has_out_of_sample and trade_count > 0:
        warnings.append("in-sample only — no out-of-sample confirmation yet")
    for param in unstable_params:
        warnings.append(f"parameter region unstable around {param} — avoid fine-tuned values")
    return tuple(warnings)


def evidence_grade_label(stats: EvidenceStats, has_oos: bool, robustness_fail: bool) -> str:
    """One-line evidence grade from real inputs (no invented thresholds beyond n)."""
    if stats.status == "INSUFFICIENT":
        return "INSUFFICIENT — too little data for evidence"
    if robustness_fail:
        return "WEAK — failed robustness dimensions"
    if stats.p_value is not None and stats.p_value < 0.05 and has_oos:
        return "MODERATE — significant in-sample with OOS confirmation"
    if stats.p_value is not None and stats.p_value < 0.05:
        return "WEAK — significant in-sample, unconfirmed out-of-sample"
    return "INCONCLUSIVE — interval includes zero"


@dataclass(frozen=True)
class StatsSummary:
    """JSON-safe statistical summary for persistence/display."""

    fields: dict[str, Any] = field(default_factory=dict)
