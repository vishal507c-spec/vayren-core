"""Shadow, fuzz and database migration: real execution with evidence."""

from __future__ import annotations

import sqlite3

from scripts.migration import fuzzing, shadow
from scripts.migration.db_migrate import discover_schema, plan_forward


def test_shadow_never_silent_pass() -> None:
    outcome = shadow.run_shadow("backtest.metrics.drawdown", count=10)
    assert outcome.verdict in ("PASS", "FAIL", "INCONCLUSIVE")
    if outcome.verdict == "INCONCLUSIVE":
        assert outcome.detail
    else:
        assert outcome.comparisons == 10
        assert outcome.verdict == "PASS", outcome.mismatches


def test_shadow_unknown_unit_inconclusive() -> None:
    outcome = shadow.run_shadow("no.such.unit", count=5)
    assert outcome.verdict == "INCONCLUSIVE"


def test_fuzz_is_deterministic_and_honest() -> None:
    first = fuzzing.run_fuzz("market.timeframe.mode", trials=30)
    second = fuzzing.run_fuzz("market.timeframe.mode", trials=30)
    assert [(o.unit_id, o.verdict) for o in first] == [(o.unit_id, o.verdict) for o in second]
    assert first[0].verdict in ("PASS", "FAIL", "INCONCLUSIVE")


def test_db_migration_detects_compatible_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "TEST.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL, high REAL,"
        " low REAL, close REAL, volume INTEGER)"
    )
    conn.execute("CREATE TABLE non_trading(candle_time TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE history_boundaries(symbol TEXT PRIMARY KEY, earliest TEXT, latest TEXT)"
    )
    conn.commit()
    conn.close()
    report = discover_schema(db)
    assert report.compatible
    assert plan_forward(report)["forward_steps"] == []


def test_db_migration_plans_drift_with_rollback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "DRIFT.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL)")
    conn.commit()
    conn.close()
    report = discover_schema(db)
    assert not report.compatible
    plan = plan_forward(report)
    assert plan["forward_steps"]
    assert plan["rollback"]
    assert plan["validation"]
