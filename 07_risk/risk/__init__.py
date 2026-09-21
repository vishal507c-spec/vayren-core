"""Risk domain — Python retains the native bridges only.

Engine, session/clock rules, kill switch and models are Rust-owned
(``rust/vayren-core`` ``risk_engine`` + ``kill_switch``); their Python
twins were removed.
"""
