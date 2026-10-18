//! Regression test: Live Events Time column formatting + clipping.

#[test]
fn live_events_time_column_uses_formatted_time_and_is_not_clipped() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("Text { width: 72px; text: ev.time;"),
        "Live Events Time column must render the formatted time (ev.time) with enough width"
    );
    // Tooltip retains the full original timestamp.
    assert!(
        src.contains("Tooltip { text: @markdown(\"\\{ev.timestamp}\"); }"),
        "Live Events Time column must expose the full timestamp via tooltip"
    );
}

#[test]
fn live_event_timestamp_formatting_regression() {
    assert_eq!(
        vayren_domain::live::format_event_time("2026-10-18T03:57:15Z"),
        "09:27:15"
    );
    assert_eq!(
        vayren_domain::live::format_event_time("13:00:02"),
        "13:00:02"
    );
}
