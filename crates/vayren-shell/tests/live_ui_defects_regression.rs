//! Focused regression tests verifying the 8 LIVE UI surgical fixes:
//! 1. Banner mode enum value (`root.bar.mode == 1`)
//! 2. Blotter tables wrapped in ScrollView so rows never clip offscreen
//! 3. Orders table header-to-row column alignment (STATUS 70px + DETAIL)
//! 4. Selected stock data & pipeline rendered in right-side Inspector
//! 5. Compact responsive labels & icon clear button for blotter tabs
//! 6. Live confirmation disabled when venue blockers exist
//! 7. Trailing dash guard on live banner message
//! 8. Blotter tab comment indexing and footer status strip

#[test]
fn test_live_confirmation_banner_checks_mode_1() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("root.bar.mode == 1"),
        "LIVE confirmation banner must check mode 1 (ExecMode::Live)"
    );
    assert!(
        !src.contains("root.bar.mode == 2"),
        "Legacy mode 2 check must be completely eliminated"
    );
}

#[test]
fn test_live_banner_guards_against_dangling_dash() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("root.start-reason != \"\" ? (root.start-verdict + \" — \" + root.start-reason) : root.start-verdict"),
        "Live banner must guard against dangling dash when start-reason is empty"
    );
}

#[test]
fn test_confirmation_modal_requires_zero_venue_blockers() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains(
            "can-confirm: root.setup.needs-live-confirm && root.venue-blockers.length == 0;"
        ),
        "LiveConfirmModal can-confirm must require zero venue blockers"
    );
    assert!(
        src.contains("label: root.can-confirm ? \"CONFIRM & START LIVE\" : \"BLOCKED BY VENUE\";"),
        "Confirmation button must indicate blocked state when venue gates are not ready"
    );
}

#[test]
fn test_blotters_have_scrollviews_for_all_four_tabs() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("if root.positions.length > 0: ScrollView {"),
        "Positions tab must wrap rows in ScrollView"
    );
    assert!(
        src.contains("if root.orders.length > 0: ScrollView {"),
        "Orders tab must wrap rows in ScrollView"
    );
    assert!(
        src.contains("if root.fills.length > 0: ScrollView {"),
        "Fills tab must wrap rows in ScrollView"
    );
    assert!(
        src.contains("if root.events.length > 0: ScrollView {"),
        "Live events tab must wrap rows in ScrollView"
    );
}

#[test]
fn test_orders_table_columns_aligned() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("Text { width: 70px; text: \"STATUS\"; color: VayrenPalette.muted; font-size: 10px; font-weight: 700; }"),
        "Orders header STATUS column must match row width (70px)"
    );
    assert!(
        src.contains("Text { horizontal-stretch: 1; text: \"DETAIL\"; color: VayrenPalette.muted; font-size: 10px; font-weight: 700; }"),
        "Orders header DETAIL column must stretch to match row reason"
    );
}

#[test]
fn test_inspector_renders_selected_stock_data_and_pipeline() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("SELECTED INSTRUMENT"),
        "Inspector must contain SELECTED INSTRUMENT card"
    );
    assert!(
        src.contains("root.selected-stock.symbol"),
        "Inspector must bind selected-stock symbol"
    );
    assert!(
        src.contains("root.selected-stock.pipeline"),
        "Inspector must render selected-stock execution pipeline"
    );
}

#[test]
fn test_blotters_support_compact_mode() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("root.blotters-compact ? (\"Pos (\" + root.positions.length + \")\")"),
        "Positions tab header must support compact label"
    );
    assert!(
        src.contains("root.blotters-compact ? \"✕\" : \"Clear\""),
        "Clear button must support compact icon mode"
    );
}

#[test]
fn test_footer_status_strip_rendered() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("FOOTER STATUS STRIP"),
        "LiveScreen must contain the footer status strip"
    );
    assert!(
        src.contains("root.footer.broker"),
        "Footer must bind broker status"
    );
    assert!(
        src.contains("root.footer.reconciliation"),
        "Footer must bind reconciliation status"
    );
}
