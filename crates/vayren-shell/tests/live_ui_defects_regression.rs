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
    assert!(
        src.contains("root.footer.clock"),
        "Footer must bind live clock"
    );
    assert!(
        src.contains("root.footer.snapshot-time"),
        "Footer must bind server snapshot time"
    );
}
#[test]
fn test_broker_label_consistent_between_top_card_and_inspector() {
    let src = include_str!("../ui/live.slint");

    // Both Top Card (wide & narrow) and Inspector must use NOT CONFIGURED fallback when broker-label is empty
    assert!(
        src.contains("value-text: root.bar.broker-label != \"\" ? root.bar.broker-label : \"NOT CONFIGURED\";"),
        "TopCard must use NOT CONFIGURED fallback for empty broker-label"
    );
    assert!(
        src.contains("text: root.bar.broker-label != \"\" ? root.bar.broker-label : \"NOT CONFIGURED\";"),
        "Inspector must use NOT CONFIGURED fallback for empty broker-label (consistent with TopCard)"
    );

    // Neither Top Card nor Inspector may use FYERS as a fallback
    assert!(
        !src.contains("root.bar.broker-label != \"\" ? root.bar.broker-label : \"FYERS\""),
        "LiveScreen must NEVER use FYERS as fallback for empty broker-label"
    );
    assert!(
        !src.contains("root.footer.broker != \"\" ? root.footer.broker : \"FYERS\""),
        "Footer must NEVER use FYERS as fallback"
    );
}

#[test]
fn test_broker_status_consistent_between_top_card_and_inspector() {
    let src = include_str!("../ui/live.slint");

    // Both Top Card and Inspector must use DISCONNECTED fallback when conn-label is empty
    assert!(
        src.contains("sub-text: root.bar.conn-label != \"\" ? root.bar.conn-label : \"DISCONNECTED\";"),
        "TopCard must use DISCONNECTED fallback for empty conn-label"
    );
    assert!(
        src.contains("text: root.bar.conn-label != \"\" ? root.bar.conn-label : \"DISCONNECTED\";"),
        "Inspector must use DISCONNECTED fallback for empty conn-label"
    );
}


// ── 12 Live UI Audit Defects Regression Tests ──

#[test]
fn test_defect_1_watchlist_header_and_data_row_alignment() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("Text { width: 14px; text: \"\"; color: VayrenPalette.muted; font-size: 10px; font-weight: 700; }"),
        "Watchlist header column 1 must be exactly 14px wide"
    );
    assert!(
        src.contains("Rectangle { width: 14px; Rectangle { width: 10px; height: 10px; border-radius: 5px; background: LiveTone.ink(row.status-tone); } }"),
        "Watchlist row column 1 must be 14px wide to match header column 1"
    );
}

#[test]
fn test_defect_2_reconciliation_footer_no_fake_fallbacks() {
    let src = include_str!("../ui/live.slint");
    assert!(
        !src.contains("BROKER SYNCED"),
        "Footer must never use fake BROKER SYNCED fallback"
    );
    assert!(
        !src.contains("NSE STREAM"),
        "Footer must never use fake NSE STREAM fallback"
    );
    assert!(
        !src.contains("30m CANDLES"),
        "Footer must never use fake 30m CANDLES fallback"
    );
    assert!(
        src.contains("Text { text: \"RECON: \" + (root.footer.reconciliation != \"\" ? root.footer.reconciliation : \"NOT CONFIGURED\");"),
        "Footer reconciliation must display NOT CONFIGURED when empty"
    );
    assert!(
        src.contains("Text { text: \"WS: \" + (root.footer.websocket != \"\" ? root.footer.websocket : \"DISCONNECTED\");"),
        "Footer WS must display DISCONNECTED when empty"
    );
    assert!(
        src.contains("Text { text: \"DATA: \" + (root.footer.market-data != \"\" ? root.footer.market-data : \"NO DATA\");"),
        "Footer DATA must display NO DATA when empty"
    );
}

#[test]
fn test_defect_3_broker_capital_status_color() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("text: root.bar.capital-value != \"\" ? root.bar.capital-value : \"Unavailable\";"),
        "Inspector must render 'Unavailable' when broker capital value is missing"
    );
    assert!(
        src.contains("color: root.bar.capital-tone == 1 ? VayrenPalette.live-teal : (root.bar.capital-tone == 3 ? VayrenPalette.neg : (root.bar.capital-tone == 2 ? VayrenPalette.amber : VayrenPalette.muted));"),
        "Inspector capital must map tone dynamically to semantic palette colors"
    );
}

#[test]
fn test_defect_4_watchlist_search_filter_empty_state() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("title: \"No symbols match\";"),
        "Watchlist must render 'No symbols match' empty state when filtering produces 0 results"
    );
    assert!(
        src.contains("detail: \"No symbols match your current search and filter criteria.\";"),
        "Watchlist empty state must provide helpful detail"
    );
    assert!(
        src.contains("if root.watchlist.length == 0 && root.filter-chip-selected == 0 && root.filter-text == \"\": VerticalLayout {"),
        "Strategy universe editor must only display when no filter/search is active"
    );
}

#[test]
fn test_defect_5_broker_configuration_action_wired() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("label: \"CONFIGURE BROKER\";"),
        "Inspector must have a visible CONFIGURE BROKER button"
    );
    assert!(
        src.contains("clicked => { root.configure-broker(); }"),
        "Action must be wired to root.configure-broker()"
    );
}

#[test]
fn test_defect_6_live_confirm_modal_broker_name_graceful() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("text: root.broker-name != \"\" ? (root.broker-name + (root.broker-conn != \"\" ? (\" (\" + root.broker-conn + \")\") : \"\")) : \"Broker not configured\";"),
        "LiveConfirmModal must display 'Broker not configured' without empty parentheses when broker is empty"
    );
    assert!(
        !src.contains("Text { text: root.broker-name + \" (\" + root.broker-conn + \")\";"),
        "LiveConfirmModal must not format blind parentheses"
    );
}

#[test]
fn test_defect_7_inspector_reopen_rail_padding_clearance() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("padding-right: root.inspector-docked ? root.inspector-w + 10px : (!root.show-inspector ? 34px : 10px);"),
        "Workspace must provide 34px padding-right when inspector is closed to prevent reopen rail overlapping scrollbars"
    );
}

#[test]
fn test_defect_8_strategy_combobox_selection() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("color: root.strategy-selected > 0 ? VayrenPalette.live-teal : VayrenPalette.live-ink-dim;"),
        "Strategy label color must only highlight when strategy-selected > 0"
    );
    assert!(
        src.contains("property <string> selected-strategy: root.strategy-selected > 0 && root.strategy-selected < root.strategy-names.length ? root.strategy-names[root.strategy-selected] : \"Select Strategy\";"),
        "selected-strategy must evaluate to 'Select Strategy' when index is 0 or unselected"
    );
}

#[test]
fn test_defect_9_top_status_card_truncation_fixes() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("font-size: root.icon-char == \"EC2\" ? 8px : 9px;"),
        "EC2 badge icon must use 8px font-size to prevent clipping inside circle"
    );
    assert!(
        src.contains("sub-text: (root.gateway.rtt != \"\" ? (\"RTT: \" + root.gateway.rtt) : \"RTT: N/A\")"),
        "EC2 status card sub-text must prioritize RTT before HB"
    );
    assert!(
        src.contains("root.status-text != \"\" && root.status-text != \"CONNECTED\" ? (root.status-text + \" · \") : \"\""),
        "Broker capital card must display non-connected status upfront"
    );
}

#[test]
fn test_defect_10_daily_pnl_panel_vertical_stretch() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("vertical-stretch: 1;"),
        "Tab 4 (Daily P&L) must have vertical-stretch: 1"
    );
}

#[test]
fn test_defect_11_live_events_filters_scrollview() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("horizontal-scrollbar-policy: as-needed;"),
        "Event category chips must be wrapped in ScrollView with as-needed horizontal scrollbar"
    );
}

#[test]
fn test_defect_12_stale_arm_halt_wording_eliminated() {
    let src = include_str!("../ui/live.slint");
    assert!(
        !src.contains("START/STOP/HALT"),
        "UI must not contain stale START/STOP/HALT mentions"
    );
    let remote_live_src = include_str!("../src/remote_live.rs");
    assert!(
        !remote_live_src.contains("START/STOP/HALT are disabled on this link"),
        "Remote live gateway must not contain stale START/STOP/HALT notice"
    );
}
