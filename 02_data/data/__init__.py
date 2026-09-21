"""data — historical data ingest domain.

Flow mechanics are Rust-owned (``rust/vayren-core`` ``download`` +
``download_engine``). Python retains the provider SDK boundary
(``data.provider``), download settings, API pacing and the native bridge.
"""
