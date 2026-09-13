"""Redaction: secrets never reach manifests, fixtures, logs or telemetry."""

from __future__ import annotations

from scripts.migration import redact


def test_assignment_values_redacted() -> None:
    text = redact.redact_text("connect(api_key=ABC123, interval='15m')")
    assert "ABC123" not in text
    assert "interval" in text


def test_mapping_keys_redacted() -> None:
    cleaned = redact.redact_mapping({"access_token": "xyz", "symbol": "RELIANCE"})
    assert cleaned["access_token"] == "***REDACTED***"
    assert cleaned["symbol"] == "RELIANCE"


def test_detector_names_offending_pattern() -> None:
    assert redact.contains_secret_literal("password=hunter2") == "password"
    assert redact.contains_secret_literal("plain market data") is None
