"""Backtest platform — Python retains the native bridges only.

Runner, engine, validation, models and events are Rust-owned
(``rust/vayren-core`` ``backtest`` + ``backtest_engine`` + ``metrics``);
their Python twins were removed.
"""
