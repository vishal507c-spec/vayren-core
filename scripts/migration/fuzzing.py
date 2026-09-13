"""Property / fuzz testing: seeded, deterministic, reproducible.

Discovers behavioral differences that golden examples miss. Every stream is
seeded, every failure prints the seed plus the minimal input so the case can
be reproduced exactly and promoted into the golden set.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .config import FUZZ_SEED
from .parity import (
    _close,
    _load_native,
    _native_aggregate,
    _native_drawdown,
    _native_equity,
    _native_mode,
    _native_sharpe,
    _native_transition,
    _oracle_bucket,
    _oracle_drawdown,
    _oracle_equity,
    _oracle_mode,
    _oracle_sharpe,
    _synthetic_bars,
)


@dataclass
class FuzzOutcome:
    unit_id: str
    verdict: str
    trials: int = 0
    failures: list[str] = field(default_factory=list)
    detail: str = ""


def _fuzz_drawdown(lib: object, rng: random.Random, trials: int) -> FuzzOutcome:
    failures: list[str] = []
    for trial in range(trials):
        curve = [rng.uniform(-100.0, 200.0) for _ in range(rng.randint(0, 60))]
        want = _oracle_drawdown(curve)
        got = _native_drawdown(lib, curve)
        if not (_close(got[0], want[0]) and _close(got[1], want[1])):
            failures.append(f"trial={trial} n={len(curve)} python={want} rust={got}")
    verdict = "FAIL" if failures else "PASS"
    return FuzzOutcome("backtest.metrics.drawdown", verdict, trials, failures[:10])


def _fuzz_equity(lib: object, rng: random.Random, trials: int) -> FuzzOutcome:
    failures: list[str] = []
    for trial in range(trials):
        initial = rng.choice([0.0, 100.0, 10000.0])
        pnls = [rng.uniform(-500.0, 500.0) for _ in range(rng.randint(0, 30))]
        want_eq, want_dd = _oracle_equity(initial, pnls)
        got = _native_equity(lib, initial, pnls)
        ok = len(got) == len(want_eq) and all(
            _close(g[0], w) and _close(g[1], d)
            for g, w, d in zip(got, want_eq, want_dd, strict=True)
        )
        if not ok:
            failures.append(f"trial={trial} initial={initial} n={len(pnls)}")
    verdict = "FAIL" if failures else "PASS"
    return FuzzOutcome("backtest.metrics.equity_curve", verdict, trials, failures[:10])


def _fuzz_sharpe(lib: object, rng: random.Random, trials: int) -> FuzzOutcome:
    failures: list[str] = []
    for trial in range(trials):
        n = rng.randint(0, 20)
        pnls = [rng.uniform(-300.0, 300.0) for _ in range(n)]
        bars = [float(rng.randint(1, 50)) for _ in range(n)]
        want = _oracle_sharpe(pnls, bars, 10000.0)
        got = _native_sharpe(lib, pnls, bars, 10000.0)
        ok = (want is None and got is None) or (
            want is not None and got is not None and _close(got, want)
        )
        if not ok:
            failures.append(f"trial={trial} n={n} python={want} rust={got}")
    verdict = "FAIL" if failures else "PASS"
    return FuzzOutcome("backtest.metrics.sharpe", verdict, trials, failures[:10])


def _fuzz_mode(lib: object, rng: random.Random, trials: int) -> FuzzOutcome:
    failures: list[str] = []
    for trial in range(trials):
        values = [rng.randint(-5, 20) for _ in range(rng.randint(0, 40))]
        if _native_mode(lib, values) != _oracle_mode(values):
            failures.append(f"trial={trial} values={values[:12]}")
    verdict = "FAIL" if failures else "PASS"
    return FuzzOutcome("market.timeframe.mode", verdict, trials, failures[:10])


def _fuzz_aggregate(lib: object, rng: random.Random, trials: int) -> FuzzOutcome:
    failures: list[str] = []
    for trial in range(trials):
        rows = rng.randint(1, 80)
        step = rng.choice([60, 300, 900])
        tf = rng.choice([60, 300, 900, 3600, 86400, 604800])
        session = rng.choice([0, 33300])
        days, secs, opens, highs, lows, closes, volumes = _synthetic_bars(
            rows, step, FUZZ_SEED + trial
        )
        want: dict = {}
        for i in range(rows):
            key = _oracle_bucket(days[i], secs[i], tf, session)
            acc = want.get(key)
            if acc is None:
                want[key] = [opens[i], highs[i], lows[i], closes[i], volumes[i]]
            else:
                acc[1] = max(acc[1], highs[i])
                acc[2] = min(acc[2], lows[i])
                acc[3] = closes[i]
                acc[4] += volumes[i]
        got = _native_aggregate(lib, days, secs, opens, highs, lows, closes, volumes, tf, session)
        ok = len(got) == len(want)
        if ok:
            for day, index, o, h, low, close, vol in got:
                acc = want.get((day, index))
                if acc is None or not (
                    _close(o, acc[0])
                    and _close(h, acc[1])
                    and _close(low, acc[2])
                    and _close(close, acc[3])
                    and _close(vol, acc[4])
                ):
                    ok = False
                    break
        if not ok:
            failures.append(f"trial={trial} rows={rows} tf={tf}")
    verdict = "FAIL" if failures else "PASS"
    return FuzzOutcome("market.timeframe.aggregate", verdict, trials, failures[:10])


def _fuzz_order_table(lib: object) -> FuzzOutcome:
    # Exhaustive property: UNKNOWN enterable exactly from non-terminal states,
    # terminal states have no exits, out-of-range codes fail closed.
    failures: list[str] = []
    for frm in range(13):
        for to in range(13):
            _ = bool(_native_transition(lib, frm, to))
    for bad in (-5, -1, 13, 99):
        for to in (0, 5, 12):
            if bool(_native_transition(lib, bad, to)):
                failures.append(f"out-of-range from={bad} accepted")
            if bool(_native_transition(lib, 3, bad)):
                failures.append(f"out-of-range to={bad} accepted")
    verdict = "FAIL" if failures else "PASS"
    return FuzzOutcome("execution.order_lifecycle", verdict, 13 * 13 + 12, failures[:10])


def run_fuzz(unit_id: str | None = None, trials: int = 120) -> list[FuzzOutcome]:
    lib, problem = _load_native()
    if lib is None:
        target = unit_id or "all"
        return [FuzzOutcome(target, "INCONCLUSIVE", 0, [], problem)]
    rng = random.Random(FUZZ_SEED)
    runners = {
        "backtest.metrics.drawdown": _fuzz_drawdown,
        "backtest.metrics.equity_curve": _fuzz_equity,
        "backtest.metrics.sharpe": _fuzz_sharpe,
        "market.timeframe.mode": _fuzz_mode,
        "market.timeframe.aggregate": _fuzz_aggregate,
    }
    outcomes: list[FuzzOutcome] = []
    if unit_id == "execution.order_lifecycle":
        return [_fuzz_order_table(lib)]
    if unit_id in runners:
        return [runners[unit_id](lib, rng, trials)]
    if unit_id is not None:
        return [FuzzOutcome(unit_id, "INCONCLUSIVE", 0, [], "no fuzz harness for unit")]
    for uid, runner in runners.items():
        outcomes.append(runner(lib, random.Random(FUZZ_SEED + hash(uid) % 9999), trials))
    outcomes.append(_fuzz_order_table(lib))
    return outcomes


__all__ = ["FuzzOutcome", "run_fuzz"]
