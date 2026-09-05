"""Execution history persistence — TradeRecord serialization roundtrip.

`ExecutionHistory.to_dict/from_dict` calls `TradeRecord.to_dict/from_dict`;
these tests pin the save → load path for histories with real trades.
"""

from pathlib import Path

from backtest.execution import (
    ExecutionEvent,
    ExecutionHistory,
    create_snapshot,
    load_history,
    save_history,
)
from backtest.models.config import BacktestConfig
from backtest.models.trade import TradeRecord


def _trade(r_multiple: float | None = 2.5) -> TradeRecord:
    return TradeRecord(
        symbol="T",
        side="LONG",
        entry_index=3,
        exit_index=8,
        entry_time="2020-01-06 09:15:00",
        exit_time="2020-01-09 09:15:00",
        entry_price=100.0,
        exit_price=106.0,
        quantity=10.0,
        pnl=58.2,
        pnl_pct=5.82,
        commission=1.8,
        bars_held=5,
        exit_reason="SIGNAL",
        r_multiple=r_multiple,
    )


def _history() -> ExecutionHistory:
    snap = create_snapshot(
        strategy_id="s",
        version_id="v1",
        source_hash="abc",
        ir=None,
        parameters={"p": 1.0},
        config=BacktestConfig(
            symbol="T", timeframe="1D", start_date="2020-01-01", end_date="2020-12-31"
        ),
    )
    event = ExecutionEvent(
        execution_id=snap.execution_id,
        sequence=0,
        event_type="BarProcessed",
        timestamp="2020-01-06 09:15:00",
        data={"index": 3, "close": 100.0},
    )
    return ExecutionHistory(snapshot=snap, events=[event], signals=[], trades=(_trade(),))


def test_trade_record_dict_roundtrip() -> None:
    assert TradeRecord.from_dict(_trade().to_dict()) == _trade()
    assert TradeRecord.from_dict(_trade(None).to_dict()) == _trade(None)


def test_history_json_roundtrip() -> None:
    history = _history()
    restored = ExecutionHistory.from_json(history.to_json())
    assert restored.snapshot == history.snapshot
    assert restored.trades == history.trades
    assert [e.to_dict() for e in restored.events] == [e.to_dict() for e in history.events]


def test_save_and_load_history(tmp_path: Path) -> None:
    history = _history()
    path = save_history(history, tmp_path)
    assert path.is_file()
    loaded = load_history(history.snapshot.execution_id, tmp_path)
    assert loaded is not None
    assert loaded.snapshot == history.snapshot
    assert loaded.trades == history.trades
    # saving the identical history again is a dedup no-op on the same file
    assert save_history(history, tmp_path) == path


def test_empty_trades_history_roundtrip(tmp_path: Path) -> None:
    history = _history()
    empty = ExecutionHistory(
        snapshot=history.snapshot, events=list(history.events), signals=[], trades=()
    )
    loaded = ExecutionHistory.from_json(empty.to_json())
    assert loaded.trades == ()
    assert load_history("missing-id", tmp_path) is None
