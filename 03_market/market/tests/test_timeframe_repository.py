"""Timeframe detection and aggregation tests via CandleRepository/SymbolRepository."""

from datetime import date, timedelta
from pathlib import Path

from market.database.ohlcv import OhlcvCandleDatabase
from market.database.sqlite import SqliteCandleDatabase
from market.repository.candle_repository import CandleRepository
from market.repository.symbol_repository import SymbolRepository
from market.tests.conftest import seed_ohlcv_intraday_database


def _repository(path: Path) -> tuple[OhlcvCandleDatabase, CandleRepository]:
    database = OhlcvCandleDatabase(path)
    database.connect()
    return database, CandleRepository(database)


def test_detects_base_duration(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        assert repository.detect_bar_duration("TATASTEEL") == 900
        assert repository.detect_timeframes("TATASTEEL") == (
            "15m",
            "30m",
            "45m",
            "1h",
            "2h",
            "4h",
            "1D",
            "1W",
        )
    finally:
        database.close()


def test_detects_daily_base_from_sample_schema(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        repository = CandleRepository(database)
        assert repository.detect_bar_duration("SPY") == 86400
        assert repository.detect_timeframes("SPY") == ("1D", "1W")
    finally:
        database.close()


def test_get_candles_timeframe_30m(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        bars = repository.get_candles_timeframe("TATASTEEL", "30m", 5000)
    finally:
        database.close()
    assert len(bars) == 26
    first = bars[0]
    assert first.timestamp == "2026-01-01 09:15:00"
    assert first.open == 100.0
    assert first.high == 103.0
    assert first.low == 99.0
    assert first.close == 102.5
    assert first.volume == 2001
    assert first.bar_size == "30m"


def test_get_candles_timeframe_1d(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        bars = repository.get_candles_timeframe("TATASTEEL", "1D", 5000)
    finally:
        database.close()
    assert len(bars) == 2
    day1, day2 = bars
    assert day1.timestamp == "2026-01-01 00:00:00"
    assert day1.open == 100.0
    assert day1.high == 149.0
    assert day1.low == 99.0
    assert day1.close == 148.5
    assert day1.volume == 25300
    assert day1.bar_size == "1D"
    assert day2.open == 200.0
    assert day2.close == 248.5


def test_get_candles_timeframe_1w(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        bars = repository.get_candles_timeframe("TATASTEEL", "1W", 5000)
    finally:
        database.close()
    assert len(bars) == 1
    monday = date(2026, 1, 1) - timedelta(days=date(2026, 1, 1).weekday())
    assert bars[0].timestamp == f"{monday} 00:00:00"
    assert bars[0].open == 100.0
    assert bars[0].close == 248.5
    assert bars[0].volume == 53100


def test_get_candles_timeframe_respects_limit(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        bars = repository.get_candles_timeframe("TATASTEEL", "30m", 3)
    finally:
        database.close()
    assert [bar.timestamp for bar in bars] == [
        "2026-01-02 14:15:00",
        "2026-01-02 14:45:00",
        "2026-01-02 15:15:00",
    ]
    assert [bar.volume for bar in bars] == [2241, 2245, 1124]


def test_get_candles_timeframe_at_base_is_plain_fetch(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        plain = repository.get_candles("TATASTEEL", 10)
        same = repository.get_candles_timeframe("TATASTEEL", "15m", 10)
    finally:
        database.close()
    assert len(same) == 10
    assert [bar.timestamp for bar in same] == [bar.timestamp for bar in plain]


def test_get_candles_timeframe_unknown_label_falls_back(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        bars = repository.get_candles_timeframe("TATASTEEL", "garbage", 7)
    finally:
        database.close()
    assert len(bars) == 7


def test_aggregated_timestamps_ascending(ohlcv_intraday_path: Path) -> None:
    database, repository = _repository(ohlcv_intraday_path)
    try:
        bars = repository.get_candles_timeframe("TATASTEEL", "1h", 5000)
    finally:
        database.close()
    assert len(bars) == 14
    timestamps = [bar.timestamp for bar in bars]
    assert timestamps == sorted(timestamps)
    assert timestamps[0] == "2026-01-01 09:15:00"
    assert timestamps[-1] == "2026-01-02 15:15:00"


def test_symbol_repository_detect_and_load(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    assert repository.detect_timeframes("AMBUJACEM") == ("1D", "1W")
    bars = repository.get_candles_timeframe("AMBUJACEM", "1D", 5)
    assert len(bars) == 5
    assert [bar.timestamp for bar in bars] == [
        "2026-01-08 09:15:00",
        "2026-01-09 09:15:00",
        "2026-01-10 09:15:00",
        "2026-01-11 09:15:00",
        "2026-01-12 09:15:00",
    ]


def test_symbol_repository_missing_file_raises(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    try:
        repository.detect_timeframes("NOPE")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


def test_aggregation_on_real_file(tmp_path: Path) -> None:
    path = seed_ohlcv_intraday_database(tmp_path / "REALSTOCK.db", days=3, bars_per_day=10)
    database, repository = _repository(path)
    try:
        bars = repository.get_candles_timeframe("REALSTOCK", "30m", 5000)
    finally:
        database.close()
    assert len(bars) == 15
    assert bars[0].timestamp == "2026-01-01 09:15:00"
