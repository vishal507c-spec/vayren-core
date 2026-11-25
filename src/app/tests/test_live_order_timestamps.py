"""LIVE order timestamps: real history only, IST display, no invented values."""

from __future__ import annotations

from app.services.live_trading_service import LiveTradingService


def test_genuine_history_becomes_ist_date_and_time() -> None:
    facts = LiveTradingService._order_facts(
        {
            "session_date": "",
            "history": [
                ["CREATED", "2026-11-20T04:18:01+00:00"],
                ["SUBMITTED", "2026-11-20T04:18:02+00:00"],
                ["FILLED", "2026-11-20T04:18:05+00:00"],
            ],
        }
    )
    assert facts["session_date"] == "2026-11-20"
    assert facts["time"] == "09:48:01"
    assert facts["updated_at"] == "09:48:05"
    assert facts["timeline"] == [
        {"state": "CREATED", "time": "09:48:01"},
        {"state": "SUBMITTED", "time": "09:48:02"},
        {"state": "FILLED", "time": "09:48:05"},
    ]


def test_legacy_epoch_is_not_reported_as_a_real_time() -> None:
    facts = LiveTradingService._order_facts(
        {
            "session_date": "2026-11-18",
            "history": [["CREATED", "1970-01-01T00:00:00+00:00"]],
        }
    )
    assert facts["session_date"] == "2026-11-18"
    assert facts["time"] == ""
    assert facts["updated_at"] == ""
    assert facts["timeline"] == [{"state": "CREATED", "time": "Not available"}]


def test_missing_history_has_no_facts() -> None:
    facts = LiveTradingService._order_facts({"session_date": "", "history": []})
    assert facts == {
        "session_date": "",
        "time": "",
        "updated_at": "",
        "timeline": [],
    }


def test_partial_history_never_guesses_missing_times() -> None:
    facts = LiveTradingService._order_facts(
        {
            "session_date": "",
            "history": [
                ["CREATED", "1970-01-01T00:00:00+00:00"],
                ["SUBMITTED", "2026-11-20T04:18:03+00:00"],
                ["UNKNOWN", "not-a-timestamp"],
            ],
        }
    )
    assert facts["session_date"] == "2026-11-20"
    assert facts["time"] == "09:48:03"
    assert facts["timeline"] == [
        {"state": "CREATED", "time": "Not available"},
        {"state": "SUBMITTED", "time": "09:48:03"},
        {"state": "UNKNOWN", "time": "Not available"},
    ]
