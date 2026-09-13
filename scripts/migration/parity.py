"""Differential / parity engine: Python reference vs Rust candidate.

For every migration unit where practical, both implementations run against
the same inputs and every observable difference is reported. Missing
comparisons are INCONCLUSIVE — never silently PASS. Floats compare with
explicit tolerances, never exact equality.
"""

from __future__ import annotations

import ctypes
import math
from array import array
from collections import Counter
from dataclasses import dataclass, field

from .config import ABS_TOLERANCE, REL_TOLERANCE
from .golden import (
    aggregate_cases,
    drawdown_cases,
    equity_cases,
    mode_cases,
    order_lifecycle_cases,
    sharpe_cases,
)
from .redact import redact_text

# ── Frozen Python oracles (verification-only copies of the pre-migration
# math; never imported by production code, never authoritative). ───────────

_ORDERACLE: dict[int, frozenset[int]] = {
    0: frozenset({1, 6, 12}),
    1: frozenset({2, 6, 11, 12}),
    2: frozenset({3, 6, 11, 12}),
    3: frozenset({4, 5, 6, 7, 9, 11, 12}),
    4: frozenset({4, 5, 7, 9, 11, 12}),
    5: frozenset(),
    6: frozenset(),
    7: frozenset({8, 5, 12}),
    8: frozenset(),
    9: frozenset({10, 5, 12}),
    10: frozenset({4, 5, 6, 7, 9, 11, 12}),
    11: frozenset(),
    12: frozenset(),
}

_TERMINAL_ORACLE = frozenset({5, 6, 8, 11})


def _oracle_drawdown(equities: list[float]) -> tuple[float, float]:
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


def _oracle_equity(initial: float, pnls: list[float]) -> tuple[list[float], list[float]]:
    equity = initial
    peak = initial
    equities: list[float] = []
    drawdowns: list[float] = []
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        drawdowns.append((peak - equity) / peak * 100.0 if peak else 0.0)
        equities.append(equity)
    return equities, drawdowns


def _oracle_sharpe(pnls: list[float], bars: list[float], initial: float) -> float | None:
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


def _oracle_mode(values: list[int]) -> int | None:
    if not values:
        return None
    counts: Counter[int] = Counter()
    for value in values:
        counts[value] += 1
    return counts.most_common(1)[0][0]


_DAY_SECONDS = 86400


def _oracle_bucket(day: int, sec: int, tf: int, session: int) -> tuple[int, int]:
    if tf < _DAY_SECONDS:
        index = (sec - session) // tf if sec >= session else sec // tf
        return day, index
    if tf == _DAY_SECONDS:
        return day, 0
    weekday = (day - 1) % 7
    return day - weekday, 0


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(ABS_TOLERANCE, REL_TOLERANCE * max(1.0, abs(a), abs(b)))


# ── Native bridge access (lazy, fail-soft to INCONCLUSIVE). ────────────────


def _load_native() -> tuple[object | None, str]:
    try:
        import sys

        sys.path.insert(
            0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent / "01_core")
        )
        from core.native.loader import load_vayren_core

        return load_vayren_core(), ""
    except Exception as exc:  # noqa: BLE001 - any load failure is INCONCLUSIVE
        return None, f"native library unavailable: {exc}"


def _f64(values: list[float]) -> tuple[ctypes.Array, array]:
    packed = array("d", values)
    return (ctypes.c_double * len(packed)).from_buffer(packed), packed


def _i64(values: list[int]) -> tuple[ctypes.Array, array]:
    packed = array("q", values)
    return (ctypes.c_int64 * len(packed)).from_buffer(packed), packed


@dataclass
class ParityOutcome:
    unit_id: str
    verdict: str
    cases: int = 0
    passed: int = 0
    failed: int = 0
    inconclusive: int = 0
    mismatches: list[str] = field(default_factory=list)
    detail: str = ""


def _native_drawdown(lib: object, equities: list[float]) -> tuple[float, float]:
    fn = lib.vy_max_drawdown  # type: ignore[attr-defined]
    if not equities:
        return 0.0, 0.0
    view, _keep = _f64(list(equities))
    out_pct, out_abs = ctypes.c_double(), ctypes.c_double()
    fn(view, len(equities), ctypes.byref(out_pct), ctypes.byref(out_abs))
    return float(out_pct.value), float(out_abs.value)


def _native_equity(lib: object, initial: float, pnls: list[float]) -> list[tuple[float, float]]:
    import struct

    fn = lib.vy_equity_curve  # type: ignore[attr-defined]
    if not pnls:
        return []
    view, _keep = _f64(list(pnls))
    raw = (ctypes.c_double * (2 * len(pnls)))()
    wrote = int(fn(float(initial), view, len(pnls), raw))
    assert wrote == len(pnls)
    return list(struct.iter_unpack("dd", bytes(raw)))


def _native_sharpe(
    lib: object, pnls: list[float], bars: list[float], initial: float
) -> float | None:
    fn = lib.vy_sharpe  # type: ignore[attr-defined]
    if len(pnls) < 2:
        return None
    pnl_view, _k1 = _f64(list(pnls))
    bars_view, _k2 = _f64(list(bars))
    out = ctypes.c_double()
    defined = int(fn(pnl_view, bars_view, len(pnls), float(initial), ctypes.byref(out)))
    if not defined or math.isnan(out.value):
        return None
    return float(out.value)


def _native_transition(lib: object, frm: int, to: int) -> int:
    return int(lib.vy_order_transition_allowed(frm, to))  # type: ignore[attr-defined]


def _native_mode(lib: object, values: list[int]) -> int | None:
    fn = lib.vy_mode  # type: ignore[attr-defined]
    if not values:
        return None
    view, _keep = _i64(list(values))
    out = ctypes.c_int64()
    defined = int(fn(view, len(values), ctypes.byref(out)))
    return int(out.value) if defined else None


def _native_aggregate(
    lib: object,
    days: list[int],
    secs: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    tf: int,
    session: int,
) -> list[tuple]:
    import struct

    fn = lib.vy_aggregate  # type: ignore[attr-defined]

    class _Bucket(ctypes.Structure):
        _fields_ = [
            ("day", ctypes.c_int32),
            ("index", ctypes.c_int32),
            ("open", ctypes.c_double),
            ("high", ctypes.c_double),
            ("low", ctypes.c_double),
            ("close", ctypes.c_double),
            ("volume", ctypes.c_double),
        ]

    def view_i(v: list[int]) -> tuple[ctypes.Array, array]:
        packed = array("i", v)
        return (ctypes.c_int32 * len(packed)).from_buffer(packed), packed

    n = len(days)
    if n == 0:
        return []
    d_v, _k1 = view_i(days)
    s_v, _k2 = view_i(secs)
    o_v, _k3 = _f64(opens)
    h_v, _k4 = _f64(highs)
    l_v, _k5 = _f64(lows)
    c_v, _k6 = _f64(closes)
    v_v, _k7 = _f64(volumes)
    out = (_Bucket * n)()
    count = int(fn(d_v, s_v, o_v, h_v, l_v, c_v, v_v, n, int(tf), int(session), out, n))
    size = struct.calcsize("ii5d")
    assert ctypes.sizeof(_Bucket) == size
    return list(struct.iter_unpack("ii5d", bytes(out)[: count * size]))


def _synthetic_bars(rows: int, step: int, seed: int = 7) -> tuple:
    import random
    from datetime import datetime, timedelta

    rng = random.Random(seed + rows + step)
    start = datetime(2026, 1, 5, 9, 15)
    days, secs, opens, highs, lows, closes, volumes = [], [], [], [], [], [], []
    price = 100.0
    for i in range(rows):
        moment = start + timedelta(seconds=i * step)
        base = moment.date().toordinal()
        sec = moment.hour * 3600 + moment.minute * 60 + moment.second
        drift = (i % 7) - 3.0
        days.append(base)
        secs.append(sec)
        opens.append(price)
        highs.append(price + 1.0)
        lows.append(price - 1.0)
        closes.append(price + drift * 0.1)
        volumes.append(float(1000 + i))
        price += drift * 0.1
        _ = rng.random()
    return days, secs, opens, highs, lows, closes, volumes


def run_parity(unit_id: str) -> ParityOutcome:
    if unit_id == "risk.engine.evaluate":
        # Agent-migrated kernel: oracle-vs-engine differential (no cdylib gate).
        try:
            from .agent.kernels import run_risk_parity
        except ImportError as exc:
            return ParityOutcome(unit_id, "INCONCLUSIVE", detail=f"agent kernels missing: {exc}")
        outcome = run_risk_parity()
        return ParityOutcome(
            unit_id,
            outcome.verdict,
            outcome.cases,
            outcome.passed,
            outcome.failed,
            0,
            [redact_text(m) for m in outcome.mismatches[:20]],
            outcome.detail,
        )
    lib, problem = _load_native()
    if lib is None:
        return ParityOutcome(unit_id, "INCONCLUSIVE", detail=problem)
    try:
        if unit_id == "execution.order_lifecycle":
            return _parity_order_lifecycle(lib)
        if unit_id == "backtest.metrics.drawdown":
            return _parity_drawdown(lib)
        if unit_id == "backtest.metrics.equity_curve":
            return _parity_equity(lib)
        if unit_id == "backtest.metrics.sharpe":
            return _parity_sharpe(lib)
        if unit_id == "market.timeframe.mode":
            return _parity_mode(lib)
        if unit_id == "market.timeframe.aggregate":
            return _parity_aggregate(lib)
        return ParityOutcome(unit_id, "INCONCLUSIVE", detail="no parity harness for unit")
    except Exception as exc:  # noqa: BLE001 - harness errors are INCONCLUSIVE
        return ParityOutcome(unit_id, "INCONCLUSIVE", detail=f"harness error: {exc}")


def _parity_order_lifecycle(lib: object) -> ParityOutcome:
    golden = order_lifecycle_cases()
    passed = failed = 0
    mismatches: list[str] = []
    for case in golden.cases:
        frm, to = int(case["from"]), int(case["to"])
        expected = to in _ORDERACLE.get(frm, frozenset())
        actual = bool(_native_transition(lib, frm, to))
        if expected == actual:
            passed += 1
        else:
            failed += 1
            mismatches.append(f"transition {frm}->{to}: python={expected} rust={actual}")
    # Full 13x13 table sweep beyond golden cases.
    for frm in range(13):
        for to in range(13):
            expected = to in _ORDERACLE.get(frm, frozenset())
            actual = bool(_native_transition(lib, frm, to))
            if expected != actual:
                failed += 1
                mismatches.append(f"table {frm}->{to}: python={expected} rust={actual}")
            else:
                passed += 1
    verdict = "PASS" if not failed and passed else ("FAIL" if failed else "INCONCLUSIVE")
    return ParityOutcome(
        "execution.order_lifecycle",
        verdict,
        passed + failed,
        passed,
        failed,
        0,
        [redact_text(m) for m in mismatches[:20]],
    )


def _parity_drawdown(lib: object) -> ParityOutcome:
    golden = drawdown_cases()
    passed = failed = 0
    mismatches: list[str] = []
    for case in golden.cases:
        equities = [float(v) for v in case["equities"]]
        want = _oracle_drawdown(equities)
        got = _native_drawdown(lib, equities)
        if _close(got[0], want[0]) and _close(got[1], want[1]):
            passed += 1
        else:
            failed += 1
            mismatches.append(f"drawdown n={len(equities)}: python={want} rust={got}")
    verdict = "PASS" if not failed and passed else ("FAIL" if failed else "INCONCLUSIVE")
    return ParityOutcome(
        "backtest.metrics.drawdown",
        verdict,
        passed + failed,
        passed,
        failed,
        0,
        [redact_text(m) for m in mismatches[:20]],
    )


def _parity_equity(lib: object) -> ParityOutcome:
    golden = equity_cases()
    passed = failed = 0
    mismatches: list[str] = []
    for case in golden.cases:
        initial = float(case["initial"])
        pnls = [float(v) for v in case["pnls"]]
        want_eq, want_dd = _oracle_equity(initial, pnls)
        got = _native_equity(lib, initial, pnls)
        ok = len(got) == len(want_eq) and all(
            _close(g[0], w) and _close(g[1], d)
            for g, w, d in zip(got, want_eq, want_dd, strict=True)
        )
        if ok:
            passed += 1
        else:
            failed += 1
            mismatches.append(
                f"equity initial={initial} n={len(pnls)}: lengths {len(got)}/{len(want_eq)}"
            )
    verdict = "PASS" if not failed and passed else ("FAIL" if failed else "INCONCLUSIVE")
    return ParityOutcome(
        "backtest.metrics.equity_curve",
        verdict,
        passed + failed,
        passed,
        failed,
        0,
        [redact_text(m) for m in mismatches[:20]],
    )


def _parity_sharpe(lib: object) -> ParityOutcome:
    golden = sharpe_cases()
    passed = failed = 0
    mismatches: list[str] = []
    for case in golden.cases:
        pnls = [float(v) for v in case["pnls"]]
        bars = [float(v) for v in case["bars"]]
        want = _oracle_sharpe(pnls, bars, float(case["initial"]))
        got = _native_sharpe(lib, pnls, bars, float(case["initial"]))
        ok = got is None if want is None else got is not None and _close(got, want)
        if ok:
            passed += 1
        else:
            failed += 1
            mismatches.append(f"sharpe n={len(pnls)}: python={want} rust={got}")
    verdict = "PASS" if not failed and passed else ("FAIL" if failed else "INCONCLUSIVE")
    return ParityOutcome(
        "backtest.metrics.sharpe",
        verdict,
        passed + failed,
        passed,
        failed,
        0,
        [redact_text(m) for m in mismatches[:20]],
    )


def _parity_mode(lib: object) -> ParityOutcome:
    golden = mode_cases()
    passed = failed = 0
    mismatches: list[str] = []
    for case in golden.cases:
        values = [int(v) for v in case["values"]]
        if _native_mode(lib, values) == _oracle_mode(values):
            passed += 1
        else:
            failed += 1
            mismatches.append(f"mode n={len(values)}: python={_oracle_mode(values)}")
    verdict = "PASS" if not failed and passed else ("FAIL" if failed else "INCONCLUSIVE")
    return ParityOutcome(
        "market.timeframe.mode",
        verdict,
        passed + failed,
        passed,
        failed,
        0,
        [redact_text(m) for m in mismatches[:20]],
    )


def _parity_aggregate(lib: object) -> ParityOutcome:
    golden = aggregate_cases()
    passed = failed = 0
    mismatches: list[str] = []
    for case in golden.cases:
        rows = int(case["rows"])
        if rows == 0:
            if _native_aggregate(lib, [], [], [], [], [], [], [], 900, 33300) == []:
                passed += 1
            else:
                failed += 1
            continue
        days, secs, opens, highs, lows, closes, volumes = _synthetic_bars(rows, int(case["step"]))
        tf = int(case["tf"])
        session = int(case["session"])
        want: dict[tuple[int, int], list[float]] = {}
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
        if ok:
            passed += 1
        else:
            failed += 1
            mismatches.append(f"aggregate rows={rows} tf={tf}: buckets {len(got)}/{len(want)}")
    verdict = "PASS" if not failed and passed else ("FAIL" if failed else "INCONCLUSIVE")
    return ParityOutcome(
        "market.timeframe.aggregate",
        verdict,
        passed + failed,
        passed,
        failed,
        0,
        [redact_text(m) for m in mismatches[:20]],
    )


def run_all_parity() -> list[ParityOutcome]:
    return [
        run_parity(uid)
        for uid in (
            "execution.order_lifecycle",
            "backtest.metrics.drawdown",
            "backtest.metrics.equity_curve",
            "backtest.metrics.sharpe",
            "market.timeframe.mode",
            "market.timeframe.aggregate",
            "risk.engine.evaluate",
        )
    ]


__all__ = ["ParityOutcome", "run_parity", "run_all_parity"]
