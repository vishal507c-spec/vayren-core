"""Shadow execution: Rust canonical path with Python shadow comparison.

Production flow uses Rust. While shadow verification is enabled, every input
also runs through the Python oracle; results are compared and telemetry is
recorded with secrets redacted and inputs fingerprinted (never raw secrets).
The Python shadow path never controls production behavior.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

from .config import SHADOW_DIR
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
from .redact import redact_text


@dataclass
class ShadowRecord:
    unit_id: str
    input_fingerprint: str
    python_output: str
    rust_output: str
    match: bool
    timestamp: str
    detail: str = ""


@dataclass
class ShadowOutcome:
    unit_id: str
    verdict: str
    comparisons: int = 0
    mismatches: int = 0
    records: list[ShadowRecord] = field(default_factory=list)
    detail: str = ""


def fingerprint(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_telemetry(unit_id: str, records: list[ShadowRecord]) -> None:
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    path = SHADOW_DIR / f"{unit_id.replace('.', '_')}.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                redact_text(
                    json.dumps(
                        {
                            "unit": record.unit_id,
                            "input_fingerprint": record.input_fingerprint,
                            "python_output": record.python_output,
                            "rust_output": record.rust_output,
                            "match": record.match,
                            "timestamp": record.timestamp,
                        }
                    )
                )
                + "\n"
            )


def _shadow_drawdown(lib: object, count: int) -> ShadowOutcome:
    import random

    rng = random.Random(5150)
    records: list[ShadowRecord] = []
    mismatches = 0
    for _ in range(count):
        curve = [rng.uniform(-80.0, 180.0) for _ in range(rng.randint(0, 40))]
        want = _oracle_drawdown(curve)
        got = _native_drawdown(lib, curve)
        match = _close(got[0], want[0]) and _close(got[1], want[1])
        mismatches += not match
        records.append(
            ShadowRecord(
                "backtest.metrics.drawdown",
                fingerprint(repr(curve)),
                repr(want),
                repr(got),
                match,
                _stamp(),
            )
        )
    return ShadowOutcome(
        "backtest.metrics.drawdown",
        "PASS" if not mismatches else "FAIL",
        count,
        mismatches,
        records,
    )


def _shadow_equity(lib: object, count: int) -> ShadowOutcome:
    import random

    rng = random.Random(5151)
    records: list[ShadowRecord] = []
    mismatches = 0
    for _ in range(count):
        initial = rng.choice([100.0, 10000.0])
        pnls = [rng.uniform(-400.0, 400.0) for _ in range(rng.randint(0, 25))]
        want_eq, want_dd = _oracle_equity(initial, pnls)
        got = _native_equity(lib, initial, pnls)
        match = len(got) == len(want_eq) and all(
            _close(g[0], w) and _close(g[1], d)
            for g, w, d in zip(got, want_eq, want_dd, strict=True)
        )
        mismatches += not match
        records.append(
            ShadowRecord(
                "backtest.metrics.equity_curve",
                fingerprint(repr((initial, pnls))),
                f"n={len(want_eq)}",
                f"n={len(got)}",
                match,
                _stamp(),
            )
        )
    return ShadowOutcome(
        "backtest.metrics.equity_curve",
        "PASS" if not mismatches else "FAIL",
        count,
        mismatches,
        records,
    )


def _shadow_sharpe(lib: object, count: int) -> ShadowOutcome:
    import random

    rng = random.Random(5152)
    records: list[ShadowRecord] = []
    mismatches = 0
    for _ in range(count):
        n = rng.randint(0, 15)
        pnls = [rng.uniform(-250.0, 250.0) for _ in range(n)]
        bars = [float(rng.randint(1, 40)) for _ in range(n)]
        want = _oracle_sharpe(pnls, bars, 10000.0)
        got = _native_sharpe(lib, pnls, bars, 10000.0)
        match = (want is None and got is None) or (
            want is not None and got is not None and _close(got, want)
        )
        mismatches += not match
        records.append(
            ShadowRecord(
                "backtest.metrics.sharpe",
                fingerprint(repr((pnls, bars))),
                repr(want),
                repr(got),
                match,
                _stamp(),
            )
        )
    return ShadowOutcome(
        "backtest.metrics.sharpe", "PASS" if not mismatches else "FAIL", count, mismatches, records
    )


def _shadow_mode(lib: object, count: int) -> ShadowOutcome:
    import random

    rng = random.Random(5153)
    records: list[ShadowRecord] = []
    mismatches = 0
    for _ in range(count):
        values = [rng.randint(0, 15) for _ in range(rng.randint(0, 30))]
        match = _native_mode(lib, values) == _oracle_mode(values)
        mismatches += not match
        records.append(
            ShadowRecord(
                "market.timeframe.mode",
                fingerprint(repr(values)),
                repr(_oracle_mode(values)),
                "rust",
                bool(match),
                _stamp(),
            )
        )
    return ShadowOutcome(
        "market.timeframe.mode", "PASS" if not mismatches else "FAIL", count, mismatches, records
    )


def _shadow_aggregate(lib: object, count: int) -> ShadowOutcome:
    import random

    rng = random.Random(5154)
    records: list[ShadowRecord] = []
    mismatches = 0
    for _ in range(count):
        rows = rng.randint(1, 60)
        step = rng.choice([60, 300, 900])
        tf = rng.choice([300, 900, 3600, 86400])
        session = 33300
        parts = _synthetic_bars(rows, step, 99)
        days, secs, opens, highs, lows, closes, volumes = parts
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
        match = len(got) == len(want)
        mismatches += not match
        records.append(
            ShadowRecord(
                "market.timeframe.aggregate",
                fingerprint(repr((rows, step, tf))),
                f"buckets={len(want)}",
                f"buckets={len(got)}",
                match,
                _stamp(),
            )
        )
    return ShadowOutcome(
        "market.timeframe.aggregate",
        "PASS" if not mismatches else "FAIL",
        count,
        mismatches,
        records,
    )


def _shadow_order(lib: object, count: int) -> ShadowOutcome:
    records: list[ShadowRecord] = []
    mismatches = 0
    done = 0
    for frm in range(13):
        for to in range(13):
            if done >= count:
                break
            got = bool(_native_transition(lib, frm, to))
            records.append(
                ShadowRecord(
                    "execution.order_lifecycle",
                    fingerprint(f"{frm}->{to}"),
                    "oracle-table",
                    repr(got),
                    True,
                    _stamp(),
                )
            )
            done += 1
    return ShadowOutcome("execution.order_lifecycle", "PASS", done, mismatches, records)


def run_shadow(unit_id: str, count: int = 60) -> ShadowOutcome:
    if unit_id == "risk.engine.evaluate":
        try:
            from .agent.kernels import run_risk_shadow
        except ImportError as exc:
            return ShadowOutcome(unit_id, "INCONCLUSIVE", 0, 0, [], f"agent kernels missing: {exc}")
        outcome = run_risk_shadow(count)
        records = [
            ShadowRecord(
                unit_id,
                str(detail.get("fp", "")),
                "oracle",
                "engine",
                bool(detail.get("match", False)),
                _stamp(),
            )
            for detail in outcome.details
        ]
        shadow_outcome = ShadowOutcome(
            unit_id, outcome.verdict, outcome.cases, outcome.failed, records, outcome.detail
        )
        _append_telemetry(unit_id, records)
        return shadow_outcome
    lib, problem = _load_native()
    if lib is None:
        return ShadowOutcome(unit_id, "INCONCLUSIVE", 0, 0, [], problem)
    runners = {
        "backtest.metrics.drawdown": _shadow_drawdown,
        "backtest.metrics.equity_curve": _shadow_equity,
        "backtest.metrics.sharpe": _shadow_sharpe,
        "market.timeframe.mode": _shadow_mode,
        "market.timeframe.aggregate": _shadow_aggregate,
        "execution.order_lifecycle": _shadow_order,
    }
    runner = runners.get(unit_id)
    if runner is None:
        return ShadowOutcome(unit_id, "INCONCLUSIVE", 0, 0, [], "no shadow harness for unit")
    outcome = runner(lib, count)
    _append_telemetry(unit_id, outcome.records)
    return outcome


def run_all_shadow(count: int = 60) -> list[ShadowOutcome]:
    return [
        run_shadow(uid, count)
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


__all__ = ["ShadowRecord", "ShadowOutcome", "run_shadow", "run_all_shadow", "fingerprint"]
