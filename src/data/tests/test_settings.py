"""Settings — path resolution, interval constants."""

from pathlib import Path

from data.settings import INTERVAL_LABEL, NSE_HOLIDAYS, DownloadSettings


def test_data_dir_is_resolved_to_path(tmp_path: Path) -> None:
    settings = DownloadSettings(data_dir=str(tmp_path))
    assert settings.data_dir == tmp_path
    assert settings.symbols_csv == tmp_path / "symbols.csv"
    assert settings.token_file == tmp_path / "token.json"
    assert settings.logs_dir == tmp_path / "logs"
    assert settings.lock_file == tmp_path / "hist_engine.lock"


def test_explicit_overrides_win(tmp_path: Path) -> None:
    settings = DownloadSettings(
        data_dir=tmp_path,
        symbols_csv="C:/custom/symbols.csv",
        token_file="C:/custom/token.json",
        logs_dir="C:/custom/logs",
    )
    assert settings.symbols_csv == Path("C:/custom/symbols.csv")
    assert settings.token_file == Path("C:/custom/token.json")
    assert settings.logs_dir == Path("C:/custom/logs")


def test_defaults_preserve_original_engine_values() -> None:
    settings = DownloadSettings(data_dir="x")
    assert settings.chunk_days == 200
    assert settings.max_history_years == 10
    assert settings.max_retries == 5
    assert settings.max_consecutive_429 == 3
    assert settings.market_open_h == 9 and settings.market_open_m == 15
    assert settings.market_close_h == 12 and settings.market_close_m == 40
    assert settings.tail_lag_tolerance_days == 5
    assert settings.lock_stale_seconds == 120
    assert settings.max_passes == 5
    assert settings.default_interval == "15m"
    assert settings.provider == "zerodha"
    assert settings.exchange == "NSE"


def test_interval_maps_are_canonical() -> None:
    assert INTERVAL_LABEL["15m"] == "15m"
    assert set(INTERVAL_LABEL) == {"1m", "5m", "15m", "30m", "1h"}


def test_holidays_include_2024_2025_2026() -> None:
    assert "2024-01-26" in NSE_HOLIDAYS
    assert "2025-08-15" in NSE_HOLIDAYS
    assert "2026-01-26" in NSE_HOLIDAYS
