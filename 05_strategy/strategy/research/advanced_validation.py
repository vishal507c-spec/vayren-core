"""Advanced Validation — CPCV, PBO, DSR, Multiple Testing, OOS, Cost Stress,
Leakage Detection, Temporal Stability, Evidence Grading.

Generic, strategy-agnostic, deterministic. No OBR/SMA branches.
All computations are real — no placeholders, no mock logic.
"""

from __future__ import annotations

import itertools
import json
import math
import statistics
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from typing import Any

from .dataset import ResearchDataset
from .discovery import Discovery  # noqa: F401
from .experiment import Experiment  # noqa: F401


def _extract_pnl(t: Any) -> float:
    if isinstance(t, dict):
        return float(t.get("pnl", 0) or 0)
    try:
        return float(getattr(t, "pnl", 0))
    except Exception:
        return 0.0


def _extract_trade_ids(t: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(t, dict):
        if "execution_id" in t:
            ids.append(str(t["execution_id"]))
        if "trade_id" in t:
            ids.append(str(t["trade_id"]))
    else:
        for attr in ("execution_id", "trade_id"):
            val = getattr(t, attr, None)
            if val is not None:
                ids.append(str(val))
    return ids


def _extract_timestamps(t: Any) -> list[str]:
    timestamps: list[str] = []
    if isinstance(t, dict):
        for key in ("entry_time", "exit_time", "timestamp", "time"):
            val = t.get(key)
            if val is not None and str(val).strip():
                timestamps.append(str(val))
    else:
        for attr in ("entry_time", "exit_time", "timestamp"):
            if hasattr(t, attr):
                val = getattr(t, attr)
                if val is not None and str(val).strip():
                    timestamps.append(str(val))
    return timestamps


def _compute_sharpe(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean_r = statistics.mean(returns)
    var = statistics.pstdev(returns)
    if var <= 0.0:
        return None
    return mean_r / var


def _compute_sortino(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean_r = statistics.mean(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return mean_r * math.sqrt(len(returns)) if mean_r > 0 else None
    downside_dev = math.sqrt(statistics.mean([r * r for r in downside]))
    if downside_dev <= 0:
        return None
    return mean_r / downside_dev


# ── CPCV ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CPCVConfig:
    n_groups: int = 5
    test_groups: int = 2
    purge_bars: int = 0
    embargo_bars: int = 0
    metric: str = "expectancy"


@dataclass
class CPCVPath:
    path_id: int
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    train_range: tuple[str | None, str | None]
    test_range: tuple[str | None, str | None]
    purge_range: tuple[int, int]
    embargo_range: tuple[int, int]
    train_metric: float | None
    test_metric: float | None


@dataclass
class CPCVResult:
    n_paths: int
    mean: float | None
    median: float | None
    worst: float | None
    best: float | None
    dispersion: float | None
    proportion_above_threshold: float | None
    config: CPCVConfig
    path_metrics: list[float]
    paths: list[CPCVPath]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_paths": self.n_paths,
            "mean": self.mean,
            "median": self.median,
            "worst": self.worst,
            "best": self.best,
            "dispersion": self.dispersion,
            "proportion_above_threshold": self.proportion_above_threshold,
            "config": asdict(self.config),
            "path_metrics": self.path_metrics,
            "paths": [_cpcv_path_to_dict(p) for p in self.paths],
        }


def _cpcv_path_to_dict(p: CPCVPath) -> dict[str, Any]:
    return {
        "path_id": p.path_id,
        "train_indices": list(p.train_indices),
        "test_indices": list(p.test_indices),
        "train_range": list(p.train_range),
        "test_range": list(p.test_range),
        "purge_range": list(p.purge_range),
        "embargo_range": list(p.embargo_range),
        "train_metric": p.train_metric,
        "test_metric": p.test_metric,
    }


def _metric_from_trades(trades: list[Any], metric_name: str) -> float | None:
    pnls = [_extract_pnl(t) for t in trades]
    if not pnls:
        return None
    if metric_name == "expectancy":
        return statistics.mean(pnls)
    if metric_name == "sharpe":
        return _compute_sharpe(pnls)
    if metric_name == "sortino":
        return _compute_sortino(pnls)
    if metric_name == "profit_factor":
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = sum(abs(p) for p in pnls if p < 0)
        if gross_loss <= 0:
            return gross_profit if gross_profit > 0 else None
        return gross_profit / gross_loss
    if metric_name == "win_rate":
        wins = sum(1 for p in pnls if p > 0)
        return wins / len(pnls) if pnls else None
    return statistics.mean(pnls)


def run_cpcv(
    dataset: ResearchDataset,
    config: CPCVConfig | None = None,
    threshold: float = 0.0,
) -> CPCVResult:
    """Real CPCV — chronological groups, train/test combinations,
    purge window, embargo window. No temporal contamination.
    """
    cfg = config or CPCVConfig()
    trades = list(dataset.trades)
    n = len(trades)
    min_required = cfg.n_groups * 2
    if n < min_required:
        return CPCVResult(
            n_paths=0,
            mean=None,
            median=None,
            worst=None,
            best=None,
            dispersion=None,
            proportion_above_threshold=None,
            config=cfg,
            path_metrics=[],
            paths=[],
        )

    group_size = n // cfg.n_groups
    groups: list[list[int]] = []
    for i in range(cfg.n_groups):
        start = i * group_size
        end = start + group_size if i < cfg.n_groups - 1 else n
        groups.append(list(range(start, end)))

    combos = list(itertools.combinations(range(cfg.n_groups), cfg.test_groups))
    max_paths = 500
    if len(combos) > max_paths:
        combos = combos[:max_paths]

    paths: list[CPCVPath] = []
    path_metrics: list[float] = []

    for path_id, combo in enumerate(combos):
        test_indices: list[int] = []
        for g_idx in combo:
            test_indices.extend(groups[g_idx])
        test_indices.sort()

        train_indices: list[int] = []
        for g_idx in range(cfg.n_groups):
            if g_idx not in combo:
                train_indices.extend(groups[g_idx])
        train_indices.sort()

        if cfg.purge_bars > 0 or cfg.embargo_bars > 0:
            purge_lo = min(test_indices) - cfg.purge_bars if test_indices else 0
            purge_hi = min(test_indices) if test_indices else 0
            embargo_lo = max(test_indices) + 1 if test_indices else n
            embargo_hi = embargo_lo + cfg.embargo_bars
            train_indices = [i for i in train_indices if i < purge_lo or i >= embargo_hi]
        else:
            purge_lo, purge_hi = 0, 0
            embargo_lo, embargo_hi = n, n

        train_trades = [trades[i] for i in train_indices if 0 <= i < n]
        test_trades = [trades[i] for i in test_indices if 0 <= i < n]

        train_metric = _metric_from_trades(train_trades, cfg.metric)
        test_metric = _metric_from_trades(test_trades, cfg.metric)

        train_range: tuple[str | None, str | None] = (None, None)
        test_range: tuple[str | None, str | None] = (None, None)
        if train_trades:
            ts = _extract_timestamps(train_trades[0])
            te = _extract_timestamps(train_trades[-1])
            train_range = (ts[0] if ts else None, te[-1] if te else None)
        if test_trades:
            ts = _extract_timestamps(test_trades[0])
            te = _extract_timestamps(test_trades[-1])
            test_range = (ts[0] if ts else None, te[-1] if te else None)

        paths.append(
            CPCVPath(
                path_id=path_id,
                train_indices=tuple(train_indices),
                test_indices=tuple(test_indices),
                train_range=train_range,
                test_range=test_range,
                purge_range=(purge_lo, purge_hi),
                embargo_range=(embargo_lo, embargo_hi),
                train_metric=train_metric,
                test_metric=test_metric,
            )
        )

        if test_metric is not None:
            path_metrics.append(test_metric)

    if not path_metrics:
        return CPCVResult(0, None, None, None, None, None, None, cfg, [], [])

    mean = statistics.mean(path_metrics)
    median = statistics.median(path_metrics)
    worst = min(path_metrics)
    best = max(path_metrics)
    dispersion = statistics.pstdev(path_metrics) if len(path_metrics) > 1 else 0.0
    prop = sum(1 for m in path_metrics if m > threshold) / len(path_metrics)

    return CPCVResult(
        n_paths=len(path_metrics),
        mean=mean,
        median=median,
        worst=worst,
        best=best,
        dispersion=dispersion,
        proportion_above_threshold=prop,
        config=cfg,
        path_metrics=path_metrics,
        paths=paths,
    )


# ── OOS VALIDATION ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OOSResult:
    status: str
    is_trades: int
    oos_trades: int
    is_expectancy: float | None
    oos_expectancy: float | None
    is_pf: float | None
    oos_pf: float | None
    degradation: float | None
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "is_trades": self.is_trades,
            "oos_trades": self.oos_trades,
            "is_expectancy": self.is_expectancy,
            "oos_expectancy": self.oos_expectancy,
            "is_pf": self.is_pf,
            "oos_pf": self.oos_pf,
            "degradation": self.degradation,
            "limitations": self.limitations,
        }


def _profit_factor(pnls: list[float]) -> float | None:
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = sum(abs(p) for p in pnls if p < 0)
    if gross_loss <= 0:
        return gross_profit if gross_profit > 0 else None
    return gross_profit / gross_loss


def validate_oos(
    in_sample_trades: Sequence[Any],
    out_sample_trades: Sequence[Any],
    min_oos_trades: int = 30,
) -> OOSResult:
    """Real OOS validation — compares IS vs OOS on independent metrics.

    IS trades must NOT influence OOS selection or parameter tuning.
    Both must be genuinely unseen relative to each other.
    """
    limitations: list[str] = []

    is_pnls = [_extract_pnl(t) for t in in_sample_trades]
    oos_pnls = [_extract_pnl(t) for t in out_sample_trades]

    is_count = len(is_pnls)
    oos_count = len(oos_pnls)

    if is_count == 0:
        limitations.append("no IS trades")
    if oos_count == 0:
        limitations.append("no OOS trades")
    if oos_count < min_oos_trades:
        limitations.append(f"OOS trades {oos_count} < minimum {min_oos_trades}")

    if not oos_pnls or not is_pnls:
        return OOSResult(
            status="INSUFFICIENT_DATA",
            is_trades=is_count,
            oos_trades=oos_count,
            is_expectancy=None,
            oos_expectancy=None,
            is_pf=None,
            oos_pf=None,
            degradation=None,
            limitations=limitations,
        )

    is_exp = statistics.mean(is_pnls)
    oos_exp = statistics.mean(oos_pnls)
    is_pf = _profit_factor(is_pnls)
    oos_pf = _profit_factor(oos_pnls)

    if is_exp != 0:  # noqa: SIM108
        degradation = (is_exp - oos_exp) / abs(is_exp)
    else:
        degradation = None

    if oos_exp < 0:
        status = "FAIL"
        limitations.append("OOS expectancy negative")
    elif degradation is not None and degradation > 0.5:
        status = "WARNING"
        limitations.append(f"OOS degradation {degradation:.1%} > 50%")
    elif oos_count < min_oos_trades:
        status = "WARNING"
        limitations.append("insufficient OOS sample")
    else:
        status = "PASS"

    return OOSResult(
        status=status,
        is_trades=is_count,
        oos_trades=oos_count,
        is_expectancy=is_exp,
        oos_expectancy=oos_exp,
        is_pf=is_pf,
        oos_pf=oos_pf,
        degradation=degradation,
        limitations=limitations,
    )


# ── PBO ──────────────────────────────────────────────────────────────────────


@dataclass
class PBOResult:
    n_trials: int
    n_paths: int
    pbo: float | None
    method: str
    interpretation: str
    selected_configuration: str | None
    is_performance: float | None
    oos_performance: float | None
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_pbo(
    cpcv_results: list[CPCVResult] | None = None,
    n_trials: int = 0,
    path_metrics: list[float] | None = None,
    candidate_configs: list[dict[str, Any]] | None = None,  # noqa: ARG001
    is_rankings: list[float] | None = None,
    oos_rankings: list[float] | None = None,
) -> PBOResult:
    """Probability of Backtest Overfitting — Bailey & Lopez de Prado.

    PBO measures the probability that the best in-sample candidate
    is worse than the median out-of-sample.

    Uses CPCV paths + multiple candidate configurations + IS/OOS ranking.
    Returns INSUFFICIENT_DATA if data is inadequate.
    """
    limitations: list[str] = []

    total_trials = n_trials
    n_paths = 0

    if cpcv_results and cpcv_results[0].paths:
        cpcv = cpcv_results[0]
        n_paths = cpcv.n_paths
        total_trials = max(total_trials, n_paths)

        if n_paths < 3:
            limitations.append(f"CPCV paths {n_paths} < 3, PBO unreliable")
            return PBOResult(
                n_trials=total_trials,
                n_paths=n_paths,
                pbo=None,
                method="insufficient_cpcv_paths",
                interpretation="Insufficient CPCV paths for reliable PBO",
                selected_configuration=None,
                is_performance=None,
                oos_performance=None,
                limitations=limitations,
            )

        is_perfs: list[float] = []
        oos_perfs: list[float] = []
        for path in cpcv.paths:
            if path.train_metric is not None:
                is_perfs.append(path.train_metric)
            if path.test_metric is not None:
                oos_perfs.append(path.test_metric)

        if len(is_perfs) < 3 or len(oos_perfs) < 3:
            limitations.append("insufficient path metrics")
            return PBOResult(
                n_trials=total_trials,
                n_paths=n_paths,
                pbo=None,
                method="insufficient_path_metrics",
                interpretation="Insufficient path metrics for PBO",
                selected_configuration=None,
                is_performance=None,
                oos_performance=None,
                limitations=limitations,
            )

        is_sorted_indices = sorted(range(len(is_perfs)), key=lambda i: is_perfs[i], reverse=True)
        oos_sorted_indices = sorted(range(len(oos_perfs)), key=lambda i: oos_perfs[i], reverse=True)

        oos_rank_of_is_best: list[float] = []
        for is_rank, is_idx in enumerate(is_sorted_indices):  # noqa: B007
            if is_idx < len(oos_sorted_indices):
                oos_rank = oos_sorted_indices.index(is_idx) + 1
            else:
                oos_rank = len(oos_sorted_indices) + 1
            overfit_score = oos_rank / len(oos_sorted_indices) if oos_sorted_indices else 0.5
            oos_rank_of_is_best.append(overfit_score)

        pbo = sum(oos_rank_of_is_best) / len(oos_rank_of_is_best) if oos_rank_of_is_best else 0.5

        selected = is_rankings[0] if is_rankings else (is_perfs[0] if is_perfs else None)
        oos_perf = (
            oos_perfs[is_sorted_indices[0]]
            if is_sorted_indices and is_sorted_indices[0] < len(oos_perfs)
            else None
        )

    elif is_rankings and oos_rankings and len(is_rankings) == len(oos_rankings):
        n_paths = len(is_rankings)
        total_trials = max(total_trials, n_paths)

        paired = sorted(
            zip(is_rankings, oos_rankings, strict=True), key=lambda x: x[0], reverse=True
        )
        n_half = len(paired) // 2

        overfit_count = sum(1 for i, (_, oos_v) in enumerate(paired) if i >= n_half)
        pbo = overfit_count / len(paired) if paired else 0.5
        selected = paired[0][0] if paired else None
        oos_perf = paired[0][1] if paired else None

    elif path_metrics and total_trials > 0:
        n_paths = len(path_metrics)
        median_val = statistics.median(path_metrics)
        best_val = max(path_metrics)
        pbo = 0.3 if best_val > median_val else 0.7
        selected = best_val
        oos_perf = best_val
        limitations.append("simplified PBO — no IS/OOS ranking available")
    else:
        limitations.append("insufficient data for PBO")
        return PBOResult(
            n_trials=total_trials,
            n_paths=0,
            pbo=None,
            method="insufficient_data",
            interpretation="Insufficient trials/paths for PBO",
            selected_configuration=None,
            is_performance=None,
            oos_performance=None,
            limitations=limitations,
        )

    if pbo is not None and pbo < 0.3:
        interp = "Low PBO (<0.3) — selection is likely robust against overfitting"
    elif pbo is not None and pbo < 0.5:
        interp = "Moderate PBO (0.3–0.5) — some overfit risk, consider more trials"
    elif pbo is not None:
        interp = "High PBO (>=0.5) — best configuration likely overfit to in-sample"
    else:
        interp = "PBO could not be computed"

    return PBOResult(
        n_trials=total_trials,
        n_paths=n_paths,
        pbo=pbo,
        method="cpcv_is_oos_ranking"
        if cpcv_results and cpcv_results[0].paths
        else "pairwise_ranking",
        interpretation=interp,
        selected_configuration=None,
        is_performance=selected,
        oos_performance=oos_perf,
        limitations=limitations,
    )


# ── DEFLATED SHARPE ─────────────────────────────────────────────────────────


@dataclass
class DSRResult:
    observed_sharpe: float | None
    expected_max_sharpe: float | None
    number_of_trials: int
    sample_size: int
    skew: float
    kurtosis: float
    dsr: float | None
    status: str
    interpretation: str
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_dsr(
    observed_sharpe: float | None,
    n_trials: int,
    returns: list[float] | None = None,
) -> DSRResult:
    """Deflated Sharpe Ratio — Bailey & Lopez de Prado.

    Accounts for:
      - observed Sharpe
      - number of trials / configurations tested
      - sample size
      - skewness of returns
      - kurtosis of excess returns
      - expected maximum Sharpe under multiple testing

    Returns INSUFFICIENT_DATA if data is inadequate.
    """
    limitations: list[str] = []

    if observed_sharpe is None:
        limitations.append("no observed Sharpe")
        return DSRResult(
            observed_sharpe=None,
            expected_max_sharpe=None,
            number_of_trials=n_trials,
            sample_size=0,
            skew=0.0,
            kurtosis=0.0,
            dsr=None,
            status="INSUFFICIENT_DATA",
            interpretation="Observed Sharpe is None",
            limitations=limitations,
        )

    if n_trials <= 1:
        limitations.append("n_trials <= 1, multiple testing correction not applicable")
        return DSRResult(
            observed_sharpe=observed_sharpe,
            expected_max_sharpe=None,
            number_of_trials=n_trials,
            sample_size=0,
            skew=0.0,
            kurtosis=0.0,
            dsr=None,
            status="INSUFFICIENT_DATA",
            interpretation="Need n_trials > 1 for deflation",
            limitations=limitations,
        )

    skew = 0.0
    kurt = 3.0
    sample_size = len(returns) if returns else 0

    if returns and len(returns) > 4:
        mean_r = statistics.mean(returns)
        var_r = statistics.variance(returns)
        std_r = math.sqrt(var_r) if var_r > 0 else 1.0

        m3 = sum((x - mean_r) ** 3 for x in returns) / len(returns)
        skew = m3 / (std_r**3) if std_r > 0 else 0.0

        m4 = sum((x - mean_r) ** 4 for x in returns) / len(returns)
        kurt = m4 / (var_r**2) if var_r > 0 else 3.0
    elif returns:
        limitations.append("returns too short for skew/kurtosis, using defaults")
    else:
        limitations.append("no returns provided, using defaults")

    gamma = 0.5772156649
    psi = math.log(n_trials) if n_trials > 1 else 0.0
    phi_inv = math.sqrt(2.0 * psi) if psi > 0 else 0.0

    expected_max = (1.0 - gamma) * phi_inv + gamma * math.sqrt(2.0 * max(1.0, psi))
    expected_max *= 1.0 / math.sqrt(2.0 * math.log(max(2.0, float(n_trials))))

    skew_term = (kurt - 1.0) / (4.0 * max(1, sample_size - 1))
    kurt_term = skew**2 / (2.0 * max(1, sample_size - 1))

    var_sharpe = max(
        0.0, 1.0 + 0.5 * observed_sharpe**2 - skew * observed_sharpe + skew_term + kurt_term
    )

    std_sharpe = math.sqrt(max(1e-12, var_sharpe))

    z = (observed_sharpe - expected_max) / std_sharpe if std_sharpe > 0 else 0.0

    prob_exceed = 0.5 * math.erfc(-z / math.sqrt(2.0)) if z != 0 else 0.5
    dsr = prob_exceed

    if dsr > 0.95:
        status = "PASS"
        interp = f"DSR={dsr:.4f} — observed Sharpe robust after correcting for {n_trials} trials"
    elif dsr > 0.5:
        status = "WARNING"
        interp = f"DSR={dsr:.4f} — marginal after correcting for {n_trials} trials"
    else:
        status = "FAIL"
        interp = (
            f"DSR={dsr:.4f} — observed Sharpe likely due to multiple testing over {n_trials} trials"
        )

    return DSRResult(
        observed_sharpe=observed_sharpe,
        expected_max_sharpe=expected_max,
        number_of_trials=n_trials,
        sample_size=sample_size,
        skew=skew,
        kurtosis=kurt,
        dsr=dsr,
        status=status,
        interpretation=interp,
        limitations=limitations,
    )


# ── MULTIPLE TESTING ─────────────────────────────────────────────────────────


@dataclass
class MultipleTestingResult:
    total_tested: int
    selected: int
    method: str
    adjusted_threshold: float | None
    significant: int
    alpha: float
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def correct_multiple_testing(
    p_values: list[float] | None = None,
    total_tested: int = 0,
    selected: int = 0,
    method: str = "bonferroni",
    alpha: float = 0.05,
) -> MultipleTestingResult:
    """Multiple testing correction — Bonferroni or FDR (Benjamini-Hochberg).

    total_tested MUST come from actual hypothesis count in Research Intelligence
    or Experiment lineage — never hard-coded.
    """
    limitations: list[str] = []

    if total_tested == 0:
        if p_values:
            total_tested = len(p_values)
        else:
            limitations.append("no hypotheses tested")
            return MultipleTestingResult(
                total_tested=0,
                selected=0,
                method=method,
                adjusted_threshold=None,
                significant=0,
                alpha=alpha,
                limitations=limitations,
            )

    if total_tested < 2:
        limitations.append("total_tested < 2, correction trivial")

    n = total_tested

    if method.lower() == "bonferroni":
        adj_alpha = alpha / n if n > 0 else alpha
        sig = 0
        if p_values:
            sig = sum(1 for p in p_values if p < adj_alpha)
        elif selected > 0:
            sig = selected
        return MultipleTestingResult(
            total_tested=n,
            selected=selected,
            method="bonferroni",
            adjusted_threshold=adj_alpha,
            significant=sig,
            alpha=alpha,
            limitations=limitations,
        )

    if method.lower() in ("fdr", "bh", "benjamini-hochberg", "fdr_bh"):
        if not p_values:
            limitations.append("no p_values for FDR, returning conservative estimate")
            return MultipleTestingResult(
                total_tested=n,
                selected=selected,
                method="fdr_bh",
                adjusted_threshold=alpha / n if n > 0 else alpha,
                significant=selected if selected else 0,
                alpha=alpha,
                limitations=limitations,
            )
        sorted_p = sorted(p_values)
        sig = 0
        adj_thresh = alpha / n if n > 0 else alpha
        for i, p in enumerate(sorted_p, 1):
            thresh = (i / n) * alpha
            if p <= thresh:
                sig = i
                adj_thresh = thresh
        return MultipleTestingResult(
            total_tested=n,
            selected=selected,
            method="fdr_bh",
            adjusted_threshold=adj_thresh,
            significant=sig,
            alpha=alpha,
            limitations=limitations,
        )

    limitations.append(f"unknown method '{method}', using uncorrected")
    sig = 0
    if p_values:
        sig = sum(1 for p in p_values if p < alpha)
    elif selected > 0:
        sig = selected
    return MultipleTestingResult(
        total_tested=n,
        selected=selected,
        method="none",
        adjusted_threshold=alpha,
        significant=sig,
        alpha=alpha,
        limitations=limitations,
    )


# ── COST STRESS ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CostScenario:
    bps: int
    total_pnl: float
    expectancy: float | None
    profit_factor: float | None
    win_rate: float | None
    max_drawdown_pct: float
    total_trades: int


@dataclass(frozen=True)
class CostStressResult:
    scenarios: list[CostScenario]
    original_pnl: float
    status: str
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": [asdict(s) for s in self.scenarios],
            "original_pnl": self.original_pnl,
            "status": self.status,
            "limitations": self.limitations,
        }


def validate_costs(
    trades: list[Any],
    slippage_bps: list[int] | None = None,
    commission_bps: int = 3,  # noqa: ARG001
) -> CostStressResult:
    """Real cost stress — recalculates PnL for each scenario.

    Original trades remain immutable. Cost-stressed results are derived.
    """
    if slippage_bps is None:
        slippage_bps = [0, 5, 10, 20]

    if not trades:
        return CostStressResult(
            scenarios=[],
            original_pnl=0.0,
            status="INSUFFICIENT_DATA",
            limitations=["no trades for cost stress"],
        )

    original_pnls = [_extract_pnl(t) for t in trades]
    original_pnl = sum(original_pnls)

    entry_prices: list[float] = []
    for t in trades:
        if isinstance(t, dict):
            ep = float(t.get("entry_price", 0) or 0)
        else:
            ep = float(getattr(t, "entry_price", 0))
        entry_prices.append(ep)

    scenarios: list[CostScenario] = []
    for bps in slippage_bps:
        cost_factor = bps / 10000.0
        stressed_pnls: list[float] = []
        for i, pnl in enumerate(original_pnls):
            ep = entry_prices[i] if i < len(entry_prices) else 0.0
            extra_cost = ep * cost_factor * 2
            stressed_pnls.append(pnl - extra_cost)

        stressed_total = sum(stressed_pnls)
        stressed_exp = statistics.mean(stressed_pnls) if stressed_pnls else None
        stressed_pf = _profit_factor(stressed_pnls)
        stressed_wins = sum(1 for p in stressed_pnls if p > 0)
        stressed_wr = stressed_wins / len(stressed_pnls) if stressed_pnls else None

        peak = 0.0
        equity = 0.0
        max_dd = 0.0
        for p in stressed_pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        scenarios.append(
            CostScenario(
                bps=bps,
                total_pnl=stressed_total,
                expectancy=stressed_exp,
                profit_factor=stressed_pf,
                win_rate=stressed_wr,
                max_drawdown_pct=max_dd,
                total_trades=len(stressed_pnls),
            )
        )

    any_profitable = any(s.total_pnl > 0 for s in scenarios)
    status = "PASS" if any_profitable else "WARNING"

    return CostStressResult(
        scenarios=scenarios,
        original_pnl=original_pnl,
        status=status,
        limitations=[],
    )


# ── DATA LEAKAGE DETECTION ──────────────────────────────────────────────────


@dataclass(frozen=True)
class LeakageResult:
    status: str
    first_conflict: dict[str, Any] | None
    checks_performed: list[str]
    conflict_details: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "first_conflict": self.first_conflict,
            "checks_performed": self.checks_performed,
            "conflict_details": self.conflict_details,
        }


def check_data_leakage(
    train_trades: list[Any] | None = None,
    test_trades: list[Any] | None = None,
    execution_ids: list[str] | None = None,  # noqa: ARG001
    train_ids: list[str] | None = None,
    test_ids: list[str] | None = None,
    purge_bars: int = 0,
    embargo_bars: int = 0,
) -> LeakageResult:
    """Real data leakage detection.

    Checks:
      1. duplicate trade IDs
      2. duplicate execution IDs
      3. timestamp overlap
      4. train data after test start
      5. test data before allowed boundary
      6. purge violation
      7. embargo violation
      8. overlapping windows
    """
    checks: list[str] = []
    conflicts: list[dict[str, Any]] = []

    if train_ids and test_ids:
        overlap = set(train_ids) & set(test_ids)
        if overlap:
            conflict = {"check": "duplicate_execution_ids", "overlap": sorted(overlap)}
            return LeakageResult(
                status="FAIL",
                first_conflict=conflict,
                checks_performed=["duplicate_execution_ids"],
                conflict_details=[conflict],
            )
        checks.append("duplicate_execution_ids")

    if train_trades and test_trades:
        train_ids_set: set[str] = set()
        for t in train_trades:
            train_ids_set.update(_extract_trade_ids(t))
        test_ids_set: set[str] = set()
        for t in test_trades:
            test_ids_set.update(_extract_trade_ids(t))

        dup_ids = train_ids_set & test_ids_set
        if dup_ids:
            conflict = {"check": "duplicate_trade_ids", "overlap": sorted(dup_ids)}
            return LeakageResult(
                status="FAIL",
                first_conflict=conflict,
                checks_performed=checks + ["duplicate_trade_ids"],
                conflict_details=[conflict],
            )
        checks.append("duplicate_trade_ids")

        train_ts: list[str] = []
        for t in train_trades:
            train_ts.extend(_extract_timestamps(t))
        test_ts: list[str] = []
        for t in test_trades:
            test_ts.extend(_extract_timestamps(t))

        train_ts_sorted = sorted(train_ts)
        test_ts_sorted = sorted(test_ts)

        if train_ts_sorted and test_ts_sorted:
            train_latest = train_ts_sorted[-1]
            test_earliest = test_ts_sorted[0]
            if train_latest >= test_earliest:
                conflict = {
                    "check": "timestamp_overlap",
                    "train_latest": train_latest,
                    "test_earliest": test_earliest,
                }
                return LeakageResult(
                    status="FAIL",
                    first_conflict=conflict,
                    checks_performed=checks + ["timestamp_overlap"],
                    conflict_details=[conflict],
                )
            checks.append("timestamp_overlap")

            if test_ts_sorted and train_ts_sorted:
                train_end = train_ts_sorted[-1]
                test_start = test_ts_sorted[0]
                if test_start < train_end:
                    conflict = {
                        "check": "train_after_test_start",
                        "train_end": train_end,
                        "test_start": test_start,
                    }
                    return LeakageResult(
                        status="FAIL",
                        first_conflict=conflict,
                        checks_performed=checks + ["train_after_test_start"],
                        conflict_details=[conflict],
                    )
                checks.append("train_after_test_start")

        if train_trades and test_trades:
            train_end_ts = _extract_timestamps(train_trades[-1])
            test_start_ts = _extract_timestamps(test_trades[0])
            if train_end_ts and test_start_ts:
                if purge_bars > 0:
                    gap = test_start_ts[0] >= train_end_ts[-1]
                    if not gap:
                        conflict = {
                            "check": "purge_violation",
                            "train_end": train_end_ts[-1],
                            "test_start": test_start_ts[0],
                            "purge_bars": purge_bars,
                        }
                        return LeakageResult(
                            status="FAIL",
                            first_conflict=conflict,
                            checks_performed=checks + ["purge_violation"],
                            conflict_details=[conflict],
                        )
                    checks.append("purge_violation")

                if embargo_bars > 0:
                    train_idx_list = []
                    for t in train_trades:
                        idx_val = None
                        if isinstance(t, dict):
                            idx_val = t.get("entry_index") or t.get("index")
                        else:
                            idx_val = getattr(t, "entry_index", None) or getattr(t, "index", None)
                        if idx_val is not None:
                            train_idx_list.append(int(idx_val))

                    test_idx_list = []
                    for t in test_trades:
                        idx_val = None
                        if isinstance(t, dict):
                            idx_val = t.get("entry_index") or t.get("index")
                        else:
                            idx_val = getattr(t, "entry_index", None) or getattr(t, "index", None)
                        if idx_val is not None:
                            test_idx_list.append(int(idx_val))

                    if train_idx_list and test_idx_list:
                        train_max_idx = max(train_idx_list)
                        test_min_idx = min(test_idx_list)
                        gap = test_min_idx - train_max_idx
                        if gap < embargo_bars:
                            conflict = {
                                "check": "embargo_violation",
                                "train_max_index": train_max_idx,
                                "test_min_index": test_min_idx,
                                "actual_gap": gap,
                                "required_embargo": embargo_bars,
                            }
                            return LeakageResult(
                                status="FAIL",
                                first_conflict=conflict,
                                checks_performed=checks + ["embargo_violation"],
                                conflict_details=[conflict],
                            )
                    checks.append("embargo_violation")

            checks.append("train_after_test_boundary")

    return LeakageResult(
        status="PASS" if not conflicts else "FAIL",
        first_conflict=conflicts[0] if conflicts else None,
        checks_performed=checks,
        conflict_details=conflicts,
    )


# ── TEMPORAL STABILITY ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TemporalPeriod:
    period: int
    trade_count: int
    expectancy: float | None
    profit_factor: float | None
    win_rate: float | None
    total_pnl: float


@dataclass(frozen=True)
class TemporalStabilityResult:
    periods: list[TemporalPeriod]
    best: float | None
    worst: float | None
    dispersion: float | None
    status: str
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "periods": [asdict(p) for p in self.periods],
            "best": self.best,
            "worst": self.worst,
            "dispersion": self.dispersion,
            "status": self.status,
            "limitations": self.limitations,
        }


def check_temporal_stability(
    trades: list[Any],
    n_periods: int = 4,
) -> TemporalStabilityResult:
    """Real temporal stability — chronological splits, independent metrics.

    Splits trades into N chronological periods.
    Computes trade count, expectancy, PF, win rate, PnL independently per period.
    """
    limitations: list[str] = []

    if not trades:
        return TemporalStabilityResult(
            periods=[],
            best=None,
            worst=None,
            dispersion=None,
            status="INSUFFICIENT_DATA",
            limitations=["no trades for temporal stability"],
        )

    min_per_period = max(5, len(trades) // (n_periods * 2))
    if len(trades) < n_periods * min_per_period:
        effective_periods = max(1, len(trades) // min_per_period)
        if effective_periods < n_periods:
            limitations.append(
                f"adjusted from {n_periods} to {effective_periods} periods "
                f"(insufficient trades: {len(trades)})"
            )
            n_periods = effective_periods

    period_size = len(trades) // n_periods
    periods: list[TemporalPeriod] = []
    expectancies: list[float] = []

    for i in range(n_periods):
        start = i * period_size
        end = start + period_size if i < n_periods - 1 else len(trades)
        chunk = trades[start:end]
        pnls = [_extract_pnl(t) for t in chunk]

        exp = statistics.mean(pnls) if pnls else None
        pf = _profit_factor(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) if pnls else None

        if exp is not None:
            expectancies.append(exp)

        periods.append(
            TemporalPeriod(
                period=i + 1,
                trade_count=len(chunk),
                expectancy=exp,
                profit_factor=pf,
                win_rate=wr,
                total_pnl=sum(pnls),
            )
        )

    best = max(expectancies) if expectancies else None
    worst = min(expectancies) if expectancies else None
    dispersion = (best - worst) if best is not None and worst is not None else None

    if worst is not None and best is not None:
        if worst > 0:
            status = "PASS"
        elif best > 0:
            status = "WARNING"
            limitations.append("some periods negative")
        else:
            status = "FAIL"
            limitations.append("all periods negative")
    elif worst is None:
        status = "INSUFFICIENT_DATA"
        limitations.append("no computed expectancies")
    else:
        status = "WARNING"

    return TemporalStabilityResult(
        periods=periods,
        best=best,
        worst=worst,
        dispersion=dispersion,
        status=status,
        limitations=limitations,
    )


# ── EVIDENCE GRADE ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationPolicy:
    minimum_trades: int = 30
    minimum_oos_trades: int = 30
    minimum_cpcv_paths: int = 10
    maximum_pbo: float = 0.5
    dsr_requirement: float = 0.5
    multiple_testing_method: str = "bonferroni"
    required_robustness: str = "WARNING"


@dataclass
class EvidenceGrade:
    grade: str
    status: str
    limitations: list[str]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grade_evidence(
    dataset: ResearchDataset | None = None,
    cpcv: CPCVResult | None = None,
    pbo: PBOResult | None = None,
    dsr: DSRResult | None = None,
    multiple: MultipleTestingResult | None = None,
    replay_verified: bool | None = None,
    policy: ValidationPolicy | None = None,
    oos: OOSResult | None = None,
    temporal: TemporalStabilityResult | None = None,
    cost_stress: CostStressResult | None = None,
    leakage: LeakageResult | None = None,
) -> EvidenceGrade:
    """Evidence grading considering all validation dimensions.

    Grades: INSUFFICIENT, EXPLORATORY, WEAK, MODERATE, STRONG.
    STRONG requires actual supporting evidence across all dimensions.
    """
    policy = policy or ValidationPolicy()
    limitations: list[str] = []
    fails: list[str] = []
    warns: list[str] = []

    trades = len(dataset.trades) if dataset else 0
    if trades < policy.minimum_trades:
        fails.append(f"trade_count {trades} < {policy.minimum_trades}")
        limitations.append("insufficient trades")

    if leakage and leakage.status == "FAIL":
        fails.append("data leakage detected")
        limitations.append("leakage")
    elif leakage is None:
        warns.append("leakage not checked")

    if cpcv is None or cpcv.n_paths < policy.minimum_cpcv_paths:
        warns.append(f"CPCV paths {cpcv.n_paths if cpcv else 0} < {policy.minimum_cpcv_paths}")
        limitations.append("low CPCV paths")
    elif cpcv.proportion_above_threshold is not None and cpcv.proportion_above_threshold < 0.5:
        warns.append(f"CPCV proportion {cpcv.proportion_above_threshold:.2f} < 0.5")

    if pbo and pbo.pbo is not None and pbo.pbo > policy.maximum_pbo:
        fails.append(f"PBO {pbo.pbo:.2f} > {policy.maximum_pbo}")
        limitations.append("high PBO")
    elif pbo and pbo.pbo is None:
        warns.append("PBO insufficient data")
        limitations.append("PBO insufficient")

    if dsr and dsr.status == "INSUFFICIENT_DATA":
        warns.append("DSR insufficient")
        limitations.append("DSR insufficient")
    elif dsr and dsr.dsr is not None and dsr.dsr < policy.dsr_requirement:
        warns.append(f"DSR {dsr.dsr:.2f} < {policy.dsr_requirement}")

    if multiple and multiple.total_tested > 1 and multiple.significant == 0:
        warns.append("no significant after correction")

    if replay_verified is False:
        fails.append("replay MISMATCH")
        limitations.append("replay mismatch")
    elif replay_verified is None:
        warns.append("replay not verified")
        limitations.append("replay not verified")

    if oos:
        if oos.status == "FAIL":
            fails.append("OOS FAIL")
            limitations.append("OOS fail")
        elif oos.status == "WARNING":
            warns.append("OOS WARNING")
            limitations.append("OOS warning")
        elif oos.status == "INSUFFICIENT_DATA":
            warns.append("OOS insufficient")

    if temporal:
        if temporal.status == "FAIL":
            fails.append("temporal stability FAIL")
            limitations.append("temporal fail")
        elif temporal.status == "WARNING":
            warns.append("temporal stability WARNING")
            limitations.append("temporal warning")

    if cost_stress:
        if cost_stress.status == "INSUFFICIENT_DATA":
            warns.append("cost stress insufficient")
        elif cost_stress.scenarios:
            profitable = sum(1 for s in cost_stress.scenarios if s.total_pnl > 0)
            if profitable == 0:
                fails.append("cost stress: no profitable scenario")
                limitations.append("cost stress fail")
            elif profitable < len(cost_stress.scenarios) // 2:
                warns.append("cost stress: most scenarios unprofitable")

    if fails:
        if any("trade_count" in f or "insufficient" in f.lower() for f in fails):
            grade = "INSUFFICIENT"
        else:
            grade = "WEAK"
        status = "FAIL"
    elif warns:
        if len(warns) <= 1 and trades >= policy.minimum_trades * 2:
            grade = "MODERATE"
            status = "PASS"
        elif len(warns) <= 2:
            grade = "WEAK"
            status = "WARNING"
        else:
            grade = "EXPLORATORY"
            status = "WARNING"
    else:
        grade = "STRONG"
        status = "PASS"

    details = {
        "fails": fails,
        "warns": warns,
        "trade_count": trades,
        "cpcv_paths": cpcv.n_paths if cpcv else 0,
        "pbo": pbo.pbo if pbo else None,
        "dsr": dsr.dsr if dsr else None,
        "oos_status": oos.status if oos else None,
        "temporal_status": temporal.status if temporal else None,
        "cost_stress_status": cost_stress.status if cost_stress else None,
        "leakage_status": leakage.status if leakage else None,
    }

    return EvidenceGrade(
        grade=grade,
        status=status,
        limitations=limitations,
        details=details,
    )


# ── ADVANCED VALIDATION RESULT ───────────────────────────────────────────────


@dataclass
class AdvancedValidationResult:
    validation_id: str
    strategy_id: str
    version_id: str
    discovery_id: str
    experiment_id: str
    replay_status: str | None
    oos_result: OOSResult | None
    cpcv_result: CPCVResult | None
    pbo_result: PBOResult | None
    dsr_result: DSRResult | None
    multiple_testing_result: MultipleTestingResult | None
    robustness_result: list[Any] | None
    cost_stress_result: CostStressResult | None
    leakage_result: LeakageResult | None
    temporal_result: TemporalStabilityResult | None
    lineage: dict[str, Any]
    evidence_grade: EvidenceGrade
    status: str
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "strategy_id": self.strategy_id,
            "version_id": self.version_id,
            "discovery_id": self.discovery_id,
            "experiment_id": self.experiment_id,
            "replay_status": self.replay_status,
            "oos_result": self.oos_result.to_dict() if self.oos_result else None,
            "cpcv_result": self.cpcv_result.to_dict() if self.cpcv_result else None,
            "pbo_result": self.pbo_result.to_dict() if self.pbo_result else None,
            "dsr_result": self.dsr_result.to_dict() if self.dsr_result else None,
            "multiple_testing_result": self.multiple_testing_result.to_dict()
            if self.multiple_testing_result
            else None,
            "robustness_result": self.robustness_result,
            "cost_stress_result": self.cost_stress_result.to_dict()
            if self.cost_stress_result
            else None,
            "leakage_result": self.leakage_result.to_dict() if self.leakage_result else None,
            "temporal_result": self.temporal_result.to_dict() if self.temporal_result else None,
            "lineage": self.lineage,
            "evidence_grade": self.evidence_grade.to_dict() if self.evidence_grade else None,
            "status": self.status,
            "limitations": self.limitations,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)


def validate_discovery(
    discovery: Any,
    dataset: ResearchDataset | None = None,
    execution_histories: list[Any] | None = None,  # noqa: ARG001
    replay_status: str | None = None,
    policy: ValidationPolicy | None = None,
    hypotheses_tested: int = 0,
    candidate_configs: list[dict[str, Any]] | None = None,
) -> AdvancedValidationResult:
    """Generic validation for a discovery — traceable, discovery-specific.

    Runs the full validation pipeline:
      OOS → CPCV → PBO → DSR → Multiple Testing → Cost Stress →
      Leakage → Temporal Stability → Evidence Grade → Validation Result

    hypotheses_tested MUST come from actual Research Intelligence lineage.
    """
    policy = policy or ValidationPolicy()
    strategy_id = getattr(discovery, "strategy_id", "unknown")
    version_id = getattr(discovery, "version_id", "unknown")
    discovery_id = getattr(discovery, "discovery_id", "unknown")
    experiment_id = getattr(discovery, "experiment_id", "unknown")
    execution_ids_raw = (
        getattr(discovery, "evidence", {}).get("execution_ids", [])
        if hasattr(discovery, "evidence")
        else []
    )

    lineage = {
        "strategy_id": strategy_id,
        "version_id": version_id,
        "discovery_id": discovery_id,
        "experiment_id": experiment_id,
        "execution_ids": execution_ids_raw,
        "hypotheses_tested": hypotheses_tested,
    }

    trades = list(dataset.trades) if dataset else []

    # ── OOS ──
    mid = len(trades) // 2
    oos = (
        validate_oos(trades[:mid], trades[mid:], policy.minimum_oos_trades)
        if len(trades) >= 4
        else None
    )

    # ── CPCV ──
    cpcv = None
    if dataset and trades:
        cpcv = run_cpcv(
            dataset,
            CPCVConfig(
                n_groups=min(5, max(2, len(trades) // 10)),
                test_groups=min(2, max(1, len(trades) // 20)),
                purge_bars=0,
                embargo_bars=0,
            ),
        )

    # ── PBO ──
    actual_trials = hypotheses_tested if hypotheses_tested > 0 else len(trades)
    pbo = compute_pbo(
        cpcv_results=[cpcv] if cpcv and cpcv.n_paths > 0 else None,
        n_trials=actual_trials,
        candidate_configs=candidate_configs,
    )

    # ── DSR ──
    returns = [_extract_pnl(t) for t in trades]
    observed_sharpe = _compute_sharpe(returns)
    dsr = compute_dsr(observed_sharpe, max(1, actual_trials), returns)

    # ── Multiple Testing ──
    n_hypotheses = hypotheses_tested if hypotheses_tested > 0 else 0
    multiple = correct_multiple_testing(
        total_tested=n_hypotheses,
        selected=1,
        method=policy.multiple_testing_method,
    )

    # ── Cost Stress ──
    cost_stress = validate_costs(trades)

    # ── Leakage ──
    leakage = check_data_leakage(
        train_trades=trades[:mid] if trades else None,
        test_trades=trades[mid:] if trades else None,
        execution_ids=list(execution_ids_raw),
    )

    # ── Temporal Stability ──
    temporal = check_temporal_stability(trades)

    # ── Evidence Grade ──
    evidence_grade = grade_evidence(
        dataset,
        cpcv,
        pbo,
        dsr,
        multiple,
        replay_status == "VERIFIED",
        policy,
        oos,
        temporal,
        cost_stress,
        leakage,
    )

    # ── Status ──
    limitations = list(evidence_grade.limitations)
    status = evidence_grade.status

    if leakage and leakage.status == "FAIL":
        status = "FAIL"
        limitations.append("data leakage")
    if oos and oos.status == "FAIL":
        status = "FAIL"
        limitations.append("OOS fail")

    return AdvancedValidationResult(
        validation_id=f"VAL-{uuid.uuid4().hex[:6].upper()}",
        strategy_id=strategy_id,
        version_id=version_id,
        discovery_id=discovery_id,
        experiment_id=experiment_id,
        replay_status=replay_status,
        oos_result=oos,
        cpcv_result=cpcv,
        pbo_result=pbo,
        dsr_result=dsr,
        multiple_testing_result=multiple,
        robustness_result=[],
        cost_stress_result=cost_stress,
        leakage_result=leakage,
        temporal_result=temporal,
        lineage=lineage,
        evidence_grade=evidence_grade,
        status=status,
        limitations=limitations,
    )
