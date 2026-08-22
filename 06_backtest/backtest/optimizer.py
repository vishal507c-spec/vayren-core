"""Optimizer — grid search with profitability-first ranking + validation."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import StrategyParameters

from backtest.models.config import BacktestConfig
from backtest.models.result import StrategyResult
from backtest.runner import BacktestRunner


@dataclass(frozen=True)
class ParamRange:
    key: str
    min: float
    max: float
    step: float

    def values(self) -> tuple[float, ...]:
        vals: list[float] = []
        v = self.min
        # avoid FP drift
        while v <= self.max + 1e-9:
            vals.append(round(v, 6))
            v = round(v + self.step, 6)
        return tuple(vals)


@dataclass(frozen=True)
class Candidate:
    rank: int
    params: dict[str, float]
    trades: int
    profit_factor: float | None
    expectancy: float | None
    win_rate: float | None
    max_drawdown: float
    result: StrategyResult | None = None


@dataclass(frozen=True)
class OptimizationResult:
    candidates: tuple[Candidate, ...]
    total_tested: int
    best: Candidate | None
    train_result: StrategyResult | None = None


@dataclass(frozen=True)
class ValidationResult:
    train: StrategyResult
    test: StrategyResult
    is_overfit: bool


def _score(candidate: Candidate) -> tuple[float, float, float]:
    """Profitability-first score: expectancy, PF, -drawdown. Higher is better."""
    exp = candidate.expectancy if candidate.expectancy is not None else -1e9
    pf = candidate.profit_factor if candidate.profit_factor is not None else 0
    # penalize low trade count implicitly via min filter; among remaining, slight trade bonus
    trade_bonus = min(1.0, candidate.trades / 200.0) * 0.05
    return (exp + trade_bonus, pf, -candidate.max_drawdown)


def optimize(
    runner: BacktestRunner,
    base_config: BacktestConfig,
    base_definition: StrategyDefinition,
    ranges: tuple[ParamRange, ...],
    min_trades: int = 100,
    max_candidates: int = 50,
) -> OptimizationResult:
    """Grid search over ranges, profitability-first ranking."""
    if not ranges:
        return OptimizationResult(candidates=(), total_tested=0, best=None)
    value_lists = [r.values() for r in ranges]
    keys = [r.key for r in ranges]
    total = 1
    for lst in value_lists:
        total *= len(lst)
    # safety cap: limit to 5000 combos
    if total > 5000:
        total = 5000
    candidates: list[Candidate] = []
    tested = 0
    for combo in product(*value_lists):
        if tested >= 5000:
            break
        tested += 1
        param_dict = dict(zip(keys, combo, strict=True))
        # merge with base params that are not in ranges
        merged = dict(base_definition.params)
        merged.update(param_dict)
        try:
            new_def = base_definition.with_params(StrategyParameters(merged))
            # temporary registry clone for this candidate
            from strategy.registry import StrategyRegistry

            tmp_reg = StrategyRegistry()
            tmp_reg.register_kind(
                base_definition.kind,
                runner._registry.factory(base_definition.kind),
                runner._registry.param_specs(base_definition.kind),
            )  # type: ignore[attr-defined]
            tmp_reg.register_definition(new_def)
            tmp_runner = BacktestRunner(runner._repository, tmp_reg)  # type: ignore[attr-defined]
            # need to bypass enabled check — new_def enabled True
            res = tmp_runner.run(base_config, (new_def.id,))
        except Exception:
            continue
        if not res.results:
            continue
        sr = res.results[0]
        if sr.metrics.total_trades < min_trades:
            continue
        c = Candidate(
            rank=0,
            params=param_dict,
            trades=sr.metrics.total_trades,
            profit_factor=sr.metrics.profit_factor,
            expectancy=sr.metrics.expectancy,
            win_rate=sr.metrics.win_rate,
            max_drawdown=sr.metrics.max_drawdown_pct,
            result=sr,
        )
        candidates.append(c)
    # rank
    candidates.sort(key=_score, reverse=True)
    ranked: list[Candidate] = []
    for i, c in enumerate(candidates[:max_candidates]):
        ranked.append(
            Candidate(
                rank=i + 1,
                params=c.params,
                trades=c.trades,
                profit_factor=c.profit_factor,
                expectancy=c.expectancy,
                win_rate=c.win_rate,
                max_drawdown=c.max_drawdown,
                result=c.result,
            )
        )
    best = ranked[0] if ranked else None
    train = best.result if best else None
    return OptimizationResult(
        candidates=tuple(ranked), total_tested=tested, best=best, train_result=train
    )


def validate(
    runner: BacktestRunner,
    base_definition: StrategyDefinition,
    candidate_params: dict[str, float],
    train_config: BacktestConfig,
    test_config: BacktestConfig,
) -> ValidationResult | None:
    """Run candidate on train and test separately, detect overfit."""
    merged = dict(base_definition.params)
    merged.update(candidate_params)
    from strategy.models.parameters import StrategyParameters
    from strategy.registry import StrategyRegistry

    new_def = base_definition.with_params(StrategyParameters(merged))
    tmp_reg = StrategyRegistry()
    tmp_reg.register_kind(
        base_definition.kind,
        runner._registry.factory(base_definition.kind),
        runner._registry.param_specs(base_definition.kind),
    )  # type: ignore[attr-defined]
    tmp_reg.register_definition(new_def)
    tmp_runner = BacktestRunner(runner._repository, tmp_reg)  # type: ignore[attr-defined]
    train_res = tmp_runner.run(train_config, (new_def.id,))
    test_res = tmp_runner.run(test_config, (new_def.id,))
    if not train_res.results or not test_res.results:
        return None
    train = train_res.results[0]
    test = test_res.results[0]
    # overfit if test PF < 1.0 or test expectancy negative while train positive
    is_overfit = False
    if train.metrics.profit_factor and train.metrics.profit_factor > 1.3:
        if test.metrics.profit_factor is None or test.metrics.profit_factor < 1.0:
            is_overfit = True
    if (
        train.metrics.expectancy
        and train.metrics.expectancy > 0
        and test.metrics.expectancy is not None
        and test.metrics.expectancy < 0
    ):
        is_overfit = True
    return ValidationResult(train=train, test=test, is_overfit=is_overfit)
