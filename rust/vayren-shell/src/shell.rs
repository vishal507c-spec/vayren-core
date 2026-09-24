//! Shell state + projection logic — pure, headless-testable native UI state
//! (AI_ENTRY.md §1: Rust owns UI state/interaction logic; Slint only
//! renders bound properties).
//!
//! The shell owns the active screen centrally (single state source, mission
//! §14). Workspace content projection binds the [`BrokerPanel`] view-model
//! to Slint properties: every displayed value comes from backend facts,
//! never from UI inference.

use crate::broker_connection::{BrokerWorkspace, ConnectionState};
use crate::lab::{self, LabMode, LabState, LibFilter, RunState};
use crate::live::{self, ExecMode, Gate, GateStatus, LiveState, SymbolPick};
use crate::market;
use crate::market_download as mdownload;
use crate::portfolio::{self, PortfolioState};
use crate::research_state::{self, ResearchState};
use crate::view_model::{BrokerPanel, CapabilityStatus, Environment};
use crate::viewport::ChartViewportZoom;
use crate::{
    AppWindow, BrokerCheckRow, BrokerRowView, CapabilityRowView, CredentialFieldView, DlCalDay,
    DlCredField, DlPlan, DlStatus, DlStock, LabBoardCell, LabDetailMetric, LabHeader, LabKpi,
    LabLibraryRow, LabMatrixRow, LabParam, LabPoint, LabPreset, LabRankRow, LabTradeRow, LiveBar,
    LiveCandle, LiveEventRow, LiveFill, LiveGate, LiveKv, LiveMarket, LiveOrder, LivePosition,
    LiveSetup, LiveStat, LiveSymbolRow, MarketCandle, MarketIndicator, MarketMarker, MarketPlotSeg,
    MarketPopupRow, MarketSettingsRow, MarketStatusRow, MarketTick, MarketTimeframe,
    MarketTradeContext, MarketWatchRow, PortfolioAlloc, PortfolioFill, PortfolioGate, PortfolioKpi,
    PortfolioOrder, PortfolioPosition, PortfolioRisk, ProgressStepView, ResearchCompareRow,
    ResearchConfigGroup, ResearchEvidenceDim, ResearchEvidenceWhy, ResearchExperimentRow,
    ResearchField, ResearchKv, ResearchKvGroup, ResearchMetric, ResearchRobustRow,
    ResearchSignalRow, ResearchStrategyRow, ResearchTradeRow, ShellScreen,
};
use slint::ComponentHandle;
#[cfg(test)]
use slint::Model;
use std::cell::RefCell;
use std::rc::Rc;

/// Title text for a navigation target (status bar + pending card).
pub fn screen_title(screen: ShellScreen) -> &'static str {
    match screen {
        ShellScreen::Chart => "CHART",
        ShellScreen::Lab => "STRATEGY LAB",
        ShellScreen::Research => "RESEARCH",
        ShellScreen::Portfolio => "PORTFOLIO",
        ShellScreen::Live => "LIVE",
        ShellScreen::Broker => "BROKER",
        ShellScreen::Data => "DATA",
    }
}

/// Native surfaces migrated so far: broker panel, Strategy Lab, Portfolio and
/// Research (each further workspace migrates in its own slice).
pub fn screen_pending(screen: ShellScreen) -> bool {
    !matches!(
        screen,
        ShellScreen::Broker
            | ShellScreen::Lab
            | ShellScreen::Portfolio
            | ShellScreen::Research
            | ShellScreen::Live
            | ShellScreen::Chart
    )
}

/// Map a `--screen <name>` CLI value to its screen. Unknown names yield
/// `None` so the caller falls back to the default screen (never guess).
pub fn screen_from_name(name: &str) -> Option<ShellScreen> {
    match name {
        "chart" => Some(ShellScreen::Chart),
        "lab" => Some(ShellScreen::Lab),
        "research" => Some(ShellScreen::Research),
        "portfolio" => Some(ShellScreen::Portfolio),
        "live" => Some(ShellScreen::Live),
        "broker" => Some(ShellScreen::Broker),
        "data" => Some(ShellScreen::Data),
        _ => None,
    }
}

/// Initial screen from CLI args (`--screen <name>` or `--screen=<name>`).
/// Defaults to Lab; unknown values fall back to Lab (never guess, never
/// invent a screen). Pure and headless-testable; `main` passes real argv.
pub fn initial_screen(argv: &[String]) -> ShellScreen {
    let mut index = 0;
    while index < argv.len() {
        let arg = argv[index].as_str();
        let name = if let Some(value) = arg.strip_prefix("--screen=") {
            Some(value)
        } else if arg == "--screen" && index + 1 < argv.len() {
            index += 1;
            Some(argv[index].as_str())
        } else {
            None
        };
        if let Some(screen) = name.and_then(screen_from_name) {
            return screen;
        }
        index += 1;
    }
    ShellScreen::Lab
}

/// Environment badge kind: 0 = paper, 1 = sandbox, 2 = live.
pub fn env_kind(environment: Environment) -> i32 {
    match environment {
        Environment::Paper => 0,
        Environment::Sandbox => 1,
        Environment::Live => 2,
    }
}

/// Capability status kind: 0 = supported, 1 = not supported, 2 = not configured.
fn status_kind(status: CapabilityStatus) -> i32 {
    match status {
        CapabilityStatus::Supported => 0,
        CapabilityStatus::NotSupported => 1,
        CapabilityStatus::NotConfigured => 2,
    }
}

/// Project the backend view-model into the Slint capability-row model.
pub fn capability_rows(panel: &BrokerPanel) -> Vec<CapabilityRowView> {
    panel
        .capabilities
        .iter()
        .map(|row| CapabilityRowView {
            id: row.id.clone().into(),
            label: row.status.label().into(),
            kind: status_kind(row.status),
        })
        .collect()
}

/// Bind the broker panel view-model to the UI properties, including the
/// parity card projection (Rust `project_card`, refresh idle).
pub fn apply(ui: &AppWindow, panel: &BrokerPanel) {
    ui.set_broker_name(panel.display_name.clone().into());
    ui.set_broker_id(panel.broker_id.clone().into());
    ui.set_env_kind(env_kind(panel.environment));
    ui.set_env_label(panel.environment.label().into());
    ui.set_health_label(panel.health.label().into());
    ui.set_health_connected(panel.connected());
    ui.set_live_ready(panel.live_ready());
    ui.set_blockers(
        std::rc::Rc::new(slint::VecModel::from(
            panel
                .blockers
                .iter()
                .map(|b| b.clone().into())
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let card = panel.project_card(false);
    ui.set_status_glyph(card.status_glyph.into());
    ui.set_status_label(card.status_label.into());
    ui.set_status_tone(card.status_tone);
    ui.set_status_explanation(card.status_explanation.into());
    ui.set_reason_line(card.reason_line.into());
    ui.set_account_id(card.account_id.into());
    ui.set_conn_label(card.conn_label.into());
    ui.set_conn_tone(card.conn_tone);
    ui.set_last_sync(card.last_sync.into());
    ui.set_health_summary(card.health_summary.into());
    ui.set_health_summary_tone(card.health_summary_tone);
    ui.set_check_rows(
        std::rc::Rc::new(slint::VecModel::from(
            card.check_rows
                .into_iter()
                .map(|c| BrokerCheckRow {
                    key: c.key.into(),
                    label: c.label.into(),
                    value: c.value.into(),
                    tone: c.tone,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_funds_line(card.funds_line.into());
    ui.set_funds_sub(card.funds_sub.into());
    ui.set_positions_line(card.positions_line.into());
    ui.set_positions_sub(card.positions_sub.into());
    ui.set_orders_line(card.orders_line.into());
    ui.set_orders_sub(card.orders_sub.into());
    ui.set_credentials_line(card.credentials_line.into());
    ui.set_callback_display(card.callback_display.into());
    ui.set_refresh_visible(card.refresh_visible);
    ui.set_refresh_label(card.refresh_label.into());
    ui.set_refresh_enabled(card.refresh_enabled);
    ui.set_refresh_primary(card.refresh_primary);
    ui.set_login_label(card.login_label.into());
    ui.set_login_enabled(card.login_enabled);
    ui.set_login_primary(card.login_primary);
    ui.set_configure_label(card.configure_label.into());
    ui.set_disconnect_visible(card.disconnect_visible);
    ui.set_disconnect_enabled(card.disconnect_enabled);
    ui.set_remove_visible(card.remove_visible);
}

/// Bind the broker CONNECTION workspace view-model to the Slint screen.
/// Every displayed value comes from backend facts + the venue's existing
/// credential schema — the screen never infers sessions or secrets.
pub fn apply_connection(ui: &AppWindow, workspace: &BrokerWorkspace) {
    ui.set_conn_brokers(
        Rc::new(slint::VecModel::from(
            workspace
                .brokers
                .iter()
                .map(|b| {
                    let is_sel = b.selected || b.id == workspace.selected_id;
                    let (status_label, status_tone, is_conn) = if is_sel {
                        match workspace.state {
                            ConnectionState::Connected => ("Connected", 1, true),
                            ConnectionState::Authenticating
                            | ConnectionState::GettingToken
                            | ConnectionState::Verifying => ("Authenticating", 2, false),
                            ConnectionState::Failed => ("Connection Failed", 3, false),
                            ConnectionState::NotConfigured | ConnectionState::Ready => {
                                ("Not Connected", 3, false)
                            }
                        }
                    } else {
                        (b.status_label(), b.status_tone(), b.connected)
                    };
                    BrokerRowView {
                        id: b.id.clone().into(),
                        display_name: b.display_name.clone().into(),
                        mark: b.mark().into(),
                        venue_subtitle: b.venue_subtitle.clone().into(),
                        status_label: status_label.into(),
                        status_tone,
                        selected: is_sel,
                        connected: is_conn,
                    }
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_conn_display_name(workspace.display_name.clone().into());
    ui.set_conn_broker_id(workspace.selected_id.clone().into());
    ui.set_conn_mark(
        workspace
            .display_name
            .chars()
            .next()
            .map(|c| c.to_uppercase().to_string())
            .unwrap_or_default()
            .into(),
    );
    ui.set_conn_venue_subtitle(workspace.venue_subtitle.clone().into());
    ui.set_conn_env_label(workspace.env_label.clone().into());
    ui.set_conn_description(
        crate::broker_connection::description_line(&workspace.display_name).into(),
    );
    let (pill_label, pill_tone) = workspace.pill();
    ui.set_conn_pill_label(pill_label.into());
    ui.set_conn_pill_tone(pill_tone);
    ui.set_conn_status_message(workspace.status_message().into());
    ui.set_conn_fields(
        Rc::new(slint::VecModel::from(
            workspace
                .fields
                .iter()
                .map(|f| CredentialFieldView {
                    key: f.key.clone().into(),
                    label: f.label.clone().into(),
                    placeholder: f.placeholder.clone().into(),
                    secret: f.secret,
                    required: f.required,
                    saved: f.saved,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_conn_progress(
        Rc::new(slint::VecModel::from(
            workspace
                .progress()
                .into_iter()
                .map(|s| ProgressStepView {
                    index: s.index,
                    title: s.title.into(),
                    subtitle: s.subtitle.into(),
                    state: s.state,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_conn_cta_label(workspace.cta().into());
    ui.set_conn_cta_arrow(!matches!(
        workspace.state,
        ConnectionState::Authenticating
            | ConnectionState::GettingToken
            | ConnectionState::Verifying
            | ConnectionState::Failed
    ));
    ui.set_conn_cta_enabled(workspace.can_connect);
    ui.set_conn_form_enabled(workspace.form_enabled());
    ui.set_conn_is_connected(workspace.state == ConnectionState::Connected);
    ui.set_conn_is_failed(workspace.state == ConnectionState::Failed);
    ui.set_conn_error_message(if workspace.state == ConnectionState::Failed {
        workspace.status_message().into()
    } else {
        "".into()
    });
    ui.set_conn_disconnect_visible(workspace.can_disconnect);
    ui.set_conn_disconnect_enabled(workspace.can_disconnect);
    ui.set_conn_help_caption(if workspace.display_name.trim().is_empty() {
        "View setup guide.".into()
    } else {
        format!("View setup guide for {}.", workspace.display_name.trim()).into()
    });
    // Pre-fill saved-credential sentinel so the user sees which fields already
    // have stored values and does not need to re-enter them on reconnect.
    // The sentinel is a non-empty placeholder that the connect handler
    // recognises as "unchanged — use vault value".
    const SAVED_SENTINEL: &str = "\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}";
    let setters: [fn(&AppWindow, slint::SharedString); 6] = [
        AppWindow::set_broker_field_v0,
        AppWindow::set_broker_field_v1,
        AppWindow::set_broker_field_v2,
        AppWindow::set_broker_field_v3,
        AppWindow::set_broker_field_v4,
        AppWindow::set_broker_field_v5,
    ];
    for (i, f) in workspace.fields.iter().enumerate() {
        if let Some(setter) = setters.get(i) {
            if f.saved {
                setter(ui, SAVED_SENTINEL.into());
            }
        }
    }
}

/// Testing-backend init shared with headless perf/UI tests (test-only).
#[cfg(test)]
pub(crate) fn init_test_backend() {
    static ONCE: std::sync::Once = std::sync::Once::new();
    ONCE.call_once(i_slint_backend_testing::init_no_event_loop);
}

fn market_model<T: Clone + 'static>(rows: Vec<T>) -> slint::ModelRc<T> {
    Rc::new(slint::VecModel::from(rows)).into()
}

/// Project the native Market state onto the standalone chart screen.
///
/// Mirrors the embed-view binding one-to-one (same projection, different
/// bind target): every rendered value comes from `MarketState`.
pub fn apply_market(ui: &AppWindow, state: &market::MarketState) {
    let view = market::project(state);
    ui.set_market_panel_visible(view.panel_visible);
    ui.set_market_symbol_title(view.symbol_title.into());
    ui.set_market_timeframe_label(view.timeframe_label.into());
    ui.set_market_exchange_label(view.exchange_label.into());
    ui.set_market_status_message(view.status_message.into());
    ui.set_market_has_data(view.has_data);
    ui.set_market_watch_rows(market_model(
        view.watch_rows
            .into_iter()
            .map(|w| MarketWatchRow {
                symbol: w.symbol.into(),
                price: w.price.into(),
                change: w.change.into(),
                tone: w.tone.cell(),
                selected: w.selected,
            })
            .collect(),
    ));
    ui.set_market_watchlists(market_model(
        view.watchlists
            .into_iter()
            .map(slint::SharedString::from)
            .collect::<Vec<_>>(),
    ));
    ui.set_market_watchlist_name(view.watchlist_name.into());
    ui.set_market_filter(view.filter.into());
    ui.set_market_sort_ascending(view.sort_ascending);
    let tf = |rows: Vec<market::TimeframeRow>| {
        market_model(
            rows.into_iter()
                .map(|t| MarketTimeframe {
                    label: t.label.into(),
                    selected: t.selected,
                })
                .collect(),
        )
    };
    ui.set_market_timeframes_visible(tf(view.timeframes_visible));
    ui.set_market_timeframes_overflow(tf(view.timeframes_overflow));
    ui.set_market_plot_slots(view.plot_slots);
    ui.set_market_candles(candles_model(view.candles));
    ui.set_market_price_ticks(ticks_model(view.price_ticks));
    ui.set_market_time_ticks(ticks_model(view.time_ticks));
    ui.set_market_plot_segments(segments_model(view.plot_segments));
    ui.set_market_markers(markers_model(view.markers));
    apply_flags_props(
        ui,
        view.scale_mode,
        view.scale_label.as_str(),
        view.grid_visible,
        view.cross_visible,
    );
    apply_hover_props(
        ui,
        &market::HoverView {
            hover_x: view.hover_x,
            hover_y: view.hover_y,
            hover_price: view.hover_price,
            hover_time: view.hover_time,
            hover_volume: view.hover_volume,
            hover_bull: view.hover_bull,
            has_hover: view.has_hover,
            header_ohlc: view.header_ohlc,
        },
    );
    ui.set_market_indicator_rows(market_model(
        view.indicator_rows
            .into_iter()
            .map(|i| MarketIndicator {
                name: i.name.into(),
                visible: i.visible,
            })
            .collect(),
    ));
    ui.set_market_popup_open(view.popup_open);
    ui.set_market_popup_query(view.popup_query.into());
    ui.set_market_popup_category(view.popup_category.into());
    ui.set_market_popup_rows(market_model(
        view.popup_rows
            .into_iter()
            .map(|r| MarketPopupRow {
                kind: r.kind.into(),
                label: r.label.into(),
                name: r.name.into(),
                category: r.category.into(),
            })
            .collect(),
    ));
    ui.set_market_settings_open(view.settings_open);
    ui.set_market_settings_name(view.settings_name.into());
    ui.set_market_settings_rows(market_model(
        view.settings_rows
            .into_iter()
            .map(|r| MarketSettingsRow {
                key: r.key.into(),
                label: r.label.into(),
                value: r.value as f32,
                min: r.min as f32,
                max: r.max as f32,
                step: r.step as f32,
                decimals: r.decimals,
            })
            .collect(),
    ));
    ui.set_market_active_label(view.active_label.into());
    ui.set_market_trade_context(MarketTradeContext {
        visible: view.trade_context.visible,
        trade: view.trade_context.trade.into(),
        symbol_side: view.trade_context.symbol_side.into(),
        time: view.trade_context.time.into(),
        pnl: view.trade_context.pnl.into(),
        r: view.trade_context.r.into(),
    });
    ui.set_market_status_open(view.market_status_open);
    let status_rows = |rows: Vec<(String, String, bool)>| -> slint::ModelRc<MarketStatusRow> {
        market_model(
            rows.into_iter()
                .map(|(key, value, muted)| MarketStatusRow {
                    key: key.into(),
                    value: value.into(),
                    muted,
                })
                .collect(),
        )
    };
    ui.set_market_status_regime(status_rows(view.market_status_regime));
    ui.set_market_status_data(status_rows(view.market_status_data));
    let dl = mdownload::project_download(&state.download);
    ui.set_market_dl_open(dl.open);
    ui.set_market_dl_busy(dl.busy);
    ui.set_market_dl_interval_index(dl.interval_index as i32);
    ui.set_market_dl_interval_label(dl.interval_label.clone().into());
    ui.set_market_dl_interval_items(market_model(
        dl.interval_items
            .iter()
            .map(|s| slint::SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_market_dl_selected_text(dl.selected_text.clone().into());
    ui.set_market_dl_progress_text(dl.progress_text.clone().into());
    ui.set_market_dl_stock_rows(market_model(
        dl.stock_rows
            .iter()
            .map(|s| DlStock {
                symbol: s.symbol.clone().into(),
                selected: s.selected,
            })
            .collect(),
    ));
    ui.set_market_dl_chips(market_model(
        dl.chips
            .iter()
            .map(|s| slint::SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_market_dl_chips_note(dl.chips_note.clone().into());
    ui.set_market_dl_filter(dl.filter.clone().into());
    ui.set_market_dl_from_display(dl.from_display.clone().into());
    ui.set_market_dl_to_display(dl.to_display.clone().into());
    ui.set_market_dl_plan(DlPlan {
        stocks: dl.plan.stocks.clone().into(),
        interval: dl.plan.interval.clone().into(),
        range: dl.plan.range.clone().into(),
        days: dl.plan.days.clone().into(),
        rows: dl.plan.rows.clone().into(),
        error: dl.plan.error.clone().into(),
    });
    let dl_status = &dl.status;
    ui.set_market_dl_status(DlStatus {
        mode: dl_status.mode.clone().into(),
        status: dl_status.status.clone().into(),
        symbol: dl_status.symbol.clone().into(),
        interval: dl_status.interval.clone().into(),
        range: dl_status.range.clone().into(),
        chunk: dl_status.chunk.clone().into(),
        rows: dl_status.rows.clone().into(),
        coverage: dl_status.coverage.clone().into(),
        progress_pct: dl_status.progress_pct,
        progress_note: dl_status.progress_note.clone().into(),
        perf_rows: dl_status.perf_rows.clone().into(),
        perf_elapsed: dl_status.perf_elapsed.clone().into(),
        perf_eta: dl_status.perf_eta.clone().into(),
        perf_size: dl_status.perf_size.clone().into(),
        complete_status: dl_status.complete_status.clone().into(),
        complete_rows: dl_status.complete_rows.clone().into(),
        complete_coverage: dl_status.complete_coverage.clone().into(),
        complete_duration: dl_status.complete_duration.clone().into(),
        complete_size: dl_status.complete_size.clone().into(),
        error_line: dl_status.error_line.clone().into(),
        error_detail: dl_status.error_detail.clone().into(),
        error_details_shown: dl_status.error_details_shown,
        cov_symbol: dl_status.cov_symbol.clone().into(),
        cov_interval: dl_status.cov_interval.clone().into(),
        cov_range: dl_status.cov_range.clone().into(),
        cov_coverage: dl_status.cov_coverage.clone().into(),
        cov_rows: dl_status.cov_rows.clone().into(),
        provider_name: dl_status.provider_name.clone().into(),
        provider_status: dl_status.provider_status.clone().into(),
        provider_label: dl_status.provider_label.clone().into(),
        provider_detail: dl_status.provider_detail.clone().into(),
        configure_visible: dl_status.configure_visible,
        advanced_visible: dl_status.advanced_visible,
        advanced_open: dl_status.advanced_open,
        env_visible: dl_status.env_visible,
        broker_caps: dl_status.broker_caps.clone().into(),
        broker_error: dl_status.broker_error.clone().into(),
    });
    ui.set_market_dl_brokers(market_model(
        dl.brokers
            .iter()
            .map(|s| slint::SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_market_dl_broker_index(dl.broker_index as i32);
    ui.set_market_dl_log(market_model(
        dl.log
            .iter()
            .map(|s| slint::SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_market_dl_log_expanded(dl.log_expanded);
    ui.set_market_dl_cal_open(dl.cal_open);
    ui.set_market_dl_cal_title(dl.cal_title.clone().into());
    ui.set_market_dl_cal_days(market_model(
        dl.cal_days
            .iter()
            .map(|c| DlCalDay {
                day: c.day,
                label: c.label.clone().into(),
                x: c.x,
                y: c.y,
            })
            .collect(),
    ));
    ui.set_market_dl_weekdays(market_model(
        dl.weekday_labels
            .iter()
            .map(|s| slint::SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_market_dl_cred_open(dl.cred_open);
    ui.set_market_dl_cred_fields(market_model(
        dl.cred_fields
            .iter()
            .map(|f| DlCredField {
                key: f.key.clone().into(),
                label: f.label.clone().into(),
                secret: f.secret,
                value: f.value.clone().into(),
                revealed: f.revealed,
            })
            .collect(),
    ));
    ui.set_market_dl_cred_has_stored(dl.cred_has_stored);
    ui.set_market_dl_cred_status(dl.cred_status.clone().into());
    ui.set_market_dl_confirm_clear(dl.confirm_clear);
}

/// Wire the Market chart interactions: Slint reports, Rust mutates centrally,
/// the screen re-projects. Symbol/timeframe refetch through `fetch` (bridge
/// snapshot, same bars the startup path loads). Backend-owned wires stay
/// queued on the state (bounded at 64, replayed by a future engine bridge) —
/// the legacy host drain is gone, nothing is dropped silently.
/// Dirty-flag apply tiers (Phase 22): each interaction refreshes ONLY the
/// Slint properties it can change. Full `apply_market` stays for data /
/// selection / list changes; viewport ops rebuild geometry lists only;
/// hover moves touch 8 scalar props and zero models.
fn candles_model(rows: Vec<market::CandlePoint>) -> slint::ModelRc<MarketCandle> {
    market_model(
        rows.into_iter()
            .map(|c| MarketCandle {
                x: c.x,
                o: c.open,
                h: c.high,
                l: c.low,
                c: c.close,
                vol: c.volume,
                top: c.top,
                body: c.body,
                tone: c.tone.cell(),
            })
            .collect(),
    )
}

fn ticks_model(rows: Vec<market::AxisTick>) -> slint::ModelRc<MarketTick> {
    market_model(
        rows.into_iter()
            .map(|t| MarketTick {
                pos: t.pos,
                label: t.label.into(),
            })
            .collect(),
    )
}

fn segments_model(rows: Vec<market::PlotSegment>) -> slint::ModelRc<MarketPlotSeg> {
    market_model(
        rows.into_iter()
            .map(|s| MarketPlotSeg {
                x1: s.x1,
                y1: s.y1,
                x2: s.x2,
                y2: s.y2,
                color: s.color,
                wide: s.wide,
            })
            .collect(),
    )
}

fn markers_model(rows: Vec<market::MarkerGlyph>) -> slint::ModelRc<MarketMarker> {
    market_model(
        rows.into_iter()
            .map(|m| MarketMarker {
                x: m.x,
                y: m.y,
                kind: m.kind,
                color: m.color,
                label: m.label.into(),
                pill: m.pill,
                pill_solid: m.pill_solid,
                tip: m.tip,
                pill_w: m.pill_w,
            })
            .collect(),
    )
}

fn apply_hover_props(ui: &AppWindow, hover: &market::HoverView) {
    ui.set_market_hover_x(hover.hover_x);
    ui.set_market_hover_y(hover.hover_y);
    ui.set_market_hover_price(hover.hover_price.clone().into());
    ui.set_market_hover_time(hover.hover_time.clone().into());
    ui.set_market_hover_volume(hover.hover_volume.clone().into());
    ui.set_market_hover_bull(hover.hover_bull);
    ui.set_market_has_hover(hover.has_hover);
    ui.set_market_header_ohlc(hover.header_ohlc.clone().into());
}

/// CROSSHAIR_DIRTY only: pointer moves never rebuild geometry or lists.
pub fn apply_market_hover(ui: &AppWindow, state: &market::MarketState) {
    apply_hover_props(ui, &market::project_hover(state));
}

/// VIEWPORT_DIRTY (+ crosshair): pan/zoom/scale ops rebuild the visible
/// geometry lists only — watchlist, timeframes, indicators, popups, the
/// download console and status panels are untouched.
pub fn apply_market_viewport(ui: &AppWindow, state: &market::MarketState) {
    let view = market::project(state);
    ui.set_market_plot_slots(view.plot_slots);
    ui.set_market_candles(candles_model(view.candles));
    ui.set_market_price_ticks(ticks_model(view.price_ticks));
    ui.set_market_time_ticks(ticks_model(view.time_ticks));
    ui.set_market_plot_segments(segments_model(view.plot_segments));
    ui.set_market_markers(markers_model(view.markers));
    ui.set_market_status_message(view.status_message.into());
    ui.set_market_has_data(view.has_data);
    apply_flags_props(
        ui,
        view.scale_mode,
        view.scale_label.as_str(),
        view.grid_visible,
        view.cross_visible,
    );
    apply_hover_props(ui, &market::project_hover(state));
}

fn apply_flags_props(ui: &AppWindow, scale_mode: i32, scale_label: &str, grid: bool, cross: bool) {
    ui.set_market_scale_mode(scale_mode);
    ui.set_market_scale_label(scale_label.into());
    ui.set_market_grid_visible(grid);
    ui.set_market_cross_visible(cross);
}

pub fn wire_market(
    ui: &AppWindow,
    state: Rc<RefCell<market::MarketState>>,
    fetch: Rc<dyn Fn(Option<String>, Option<String>)>,
) {
    fn refresh(ui: &AppWindow, state: &Rc<RefCell<market::MarketState>>) {
        apply_market(ui, &state.borrow());
    }
    /// Crosshair-only refresh: 8 scalar props, zero model rebuilds.
    fn refresh_hover(ui: &AppWindow, state: &Rc<RefCell<market::MarketState>>) {
        apply_market_hover(ui, &state.borrow());
    }
    /// Viewport refresh: visible geometry lists only, no list panels.
    fn refresh_viewport(ui: &AppWindow, state: &Rc<RefCell<market::MarketState>>) {
        apply_market_viewport(ui, &state.borrow());
    }
    macro_rules! act {
        ($cb:ident, $wire:expr, $action:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$cb(move || {
                let Some(ui) = weak.upgrade() else { return };
                strong.borrow_mut().interact($wire, $action);
                refresh(&ui, &strong);
            });
        }};
    }
    macro_rules! act_str {
        ($cb:ident, $make:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$cb(move |text: slint::SharedString| {
                let Some(ui) = weak.upgrade() else { return };
                let (wire, action) = $make(text.to_string());
                strong.borrow_mut().interact(&wire, action);
                refresh(&ui, &strong);
            });
        }};
    }
    macro_rules! act_bool {
        ($cb:ident, $make:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$cb(move |open: bool| {
                let Some(ui) = weak.upgrade() else { return };
                let (wire, action) = $make(open);
                strong.borrow_mut().interact(&wire, action);
                refresh(&ui, &strong);
            });
        }};
    }
    // Viewport-tier variants: geometry lists only, list panels untouched.
    macro_rules! act_vp {
        ($cb:ident, $wire:expr, $action:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$cb(move || {
                let Some(ui) = weak.upgrade() else { return };
                strong.borrow_mut().interact($wire, $action);
                refresh_viewport(&ui, &strong);
            });
        }};
    }
    macro_rules! act_vp2 {
        ($cb:ident, $make:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$cb(move |a: f32, b: f32| {
                let Some(ui) = weak.upgrade() else { return };
                let (wire, action) = $make(a, b);
                let (prev_first, prev_count, prev_manual) = {
                    let st = strong.borrow();
                    (st.first, st.count, st.price_manual)
                };
                if strong.borrow_mut().interact(&wire, action) {
                    let st = strong.borrow();
                    if st.first != prev_first
                        || st.count != prev_count
                        || st.price_manual != prev_manual
                    {
                        refresh_viewport(&ui, &strong);
                    }
                }
            });
        }};
    }
    act!(
        on_market_panel_toggle,
        "panel",
        market::MarketAction::PanelToggle
    );
    act!(on_market_sort_asc, "sort", market::MarketAction::SortAsc);
    act!(on_market_sort_desc, "sort", market::MarketAction::SortDesc);
    act!(
        on_market_add_watchlist,
        "watchlist:add",
        market::MarketAction::AddWatchlist
    );
    act!(
        on_market_remove_watchlist,
        "watchlist:remove",
        market::MarketAction::RemoveWatchlist
    );
    act_vp!(
        on_market_reset_view,
        "reset",
        market::MarketAction::ResetView
    );
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_hover_left(move || {
            let Some(ui) = weak.upgrade() else { return };
            strong
                .borrow_mut()
                .interact("", market::MarketAction::HoverLeft);
            refresh_hover(&ui, &strong);
        });
    }
    act_vp!(on_market_drag_end, "", market::MarketAction::DragEnd);
    act_vp!(
        on_market_price_drag_end,
        "",
        market::MarketAction::PriceDragEnd
    );
    act_vp!(on_market_price_reset, "", market::MarketAction::PriceReset);
    // Chart-settings picks from the INDICATORS popup CHART section (stable
    // row ids; labels are display-only and never matched). Geometry-affecting
    // picks refresh the viewport tier; pure visibility flags need no rebuild.
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_chart_opt_picked(move |name: slint::SharedString| {
            let Some(ui) = weak.upgrade() else { return };
            let name = name.to_string();
            let action = match name.as_str() {
                "scale" => market::MarketAction::CycleScaleMode,
                "grid" => market::MarketAction::ToggleGrid,
                "cross" => market::MarketAction::ToggleCrosshair,
                _ => return,
            };
            strong
                .borrow_mut()
                .interact(&format!("chart-opt:{name}"), action);
            refresh_viewport(&ui, &strong);
        });
    }
    act!(
        on_market_trade_prev,
        "trade:prev",
        market::MarketAction::TradePrev
    );
    act!(
        on_market_trade_next,
        "trade:next",
        market::MarketAction::TradeNext
    );
    act!(
        on_market_trade_open,
        "trade:open",
        market::MarketAction::TradeOpen
    );
    act!(
        on_market_status_toggle,
        "",
        market::MarketAction::ToggleStatus
    );
    act_str!(on_market_filter_changed, |s| (
        String::new(),
        market::MarketAction::SetFilter(s)
    ));
    act_str!(on_market_select_watchlist, |s| (
        format!("watchlist:select:{s}"),
        market::MarketAction::SelectWatchlist(s)
    ));
    act_str!(on_market_add_indicator, |s| (
        format!("indicator:add:{s}"),
        market::MarketAction::AddIndicator(s)
    ));
    act_str!(on_market_toggle_indicator_visible, |s| (
        format!("indicator:vis:{s}"),
        market::MarketAction::ToggleIndicatorVisible(s)
    ));
    act_str!(on_market_remove_indicator, |s| (
        format!("indicator:rm:{s}"),
        market::MarketAction::RemoveIndicator(s)
    ));
    act_str!(on_market_indicator_query, |s| (
        String::new(),
        market::MarketAction::IndicatorQuery(s)
    ));
    act_str!(on_market_indicator_category, |s| (
        String::new(),
        market::MarketAction::IndicatorCategory(s)
    ));
    act_bool!(on_market_indicator_popup, |open| (
        String::new(),
        market::MarketAction::IndicatorPopup(open)
    ));
    {
        // Pointer moves are the hottest path: crosshair scalars only, the
        // 4,800-node candle scene is never rebuilt for a hover.
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_hover_moved(move |x: f32, y: f32| {
            let Some(ui) = weak.upgrade() else { return };
            if strong
                .borrow_mut()
                .interact("", market::MarketAction::HoverMoved(x, y))
            {
                refresh_hover(&ui, &strong);
            }
        });
    }
    act_vp2!(on_market_wheel_zoom, |x, steps| (
        String::new(),
        market::MarketAction::WheelZoom(x, steps)
    ));
    act_vp2!(on_market_drag_start, |x, y| (
        String::new(),
        market::MarketAction::DragStart(x, y)
    ));
    act_vp2!(on_market_drag_move, |x, y| (
        String::new(),
        market::MarketAction::DragMove(x, y)
    ));
    // Symbol/timeframe go through the bridge (real bars), not just the
    // optimistic local apply — the snapshot overwrites consistently.
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        let fetch = fetch.clone();
        ui.on_market_symbol_selected(move |name: slint::SharedString| {
            let Some(ui) = weak.upgrade() else { return };
            let timeframe = strong.borrow().timeframe.clone();
            // Old bars stay visible (real data, previous symbol) while the
            // worker loads; the snapshot swaps in atomically on arrival.
            strong.borrow_mut().interact(
                &format!("symbol:select:{name}"),
                market::MarketAction::SelectSymbol(name.to_string()),
            );
            refresh_viewport(&ui, &strong);
            fetch(Some(name.to_string()), Some(timeframe));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        let fetch = fetch.clone();
        ui.on_market_timeframe_picked(move |timeframe: slint::SharedString| {
            let Some(ui) = weak.upgrade() else { return };
            let symbol = strong.borrow().selected_symbol.clone();
            strong.borrow_mut().interact(
                &format!("timeframe:select:{timeframe}"),
                market::MarketAction::SelectTimeframe(timeframe.to_string()),
            );
            refresh_viewport(&ui, &strong);
            fetch(Some(symbol), Some(timeframe.to_string()));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_wheel_pan(move |frac: f32| {
            let Some(ui) = weak.upgrade() else { return };
            strong
                .borrow_mut()
                .interact("", market::MarketAction::WheelPanX(frac));
            refresh_viewport(&ui, &strong);
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_price_zoom(move |steps: f32, y: f32| {
            let Some(ui) = weak.upgrade() else { return };
            strong
                .borrow_mut()
                .interact("", market::MarketAction::PriceZoom(steps, y));
            refresh_viewport(&ui, &strong);
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_price_drag(move |notches: f32, anchor: f32| {
            let Some(ui) = weak.upgrade() else { return };
            strong
                .borrow_mut()
                .interact("", market::MarketAction::PriceDrag(notches, anchor));
            refresh_viewport(&ui, &strong);
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_settings_popup(move |open: bool, name: slint::SharedString| {
            let Some(ui) = weak.upgrade() else { return };
            strong.borrow_mut().interact(
                "",
                market::MarketAction::SettingsPopup(open, name.to_string()),
            );
            refresh(&ui, &strong);
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_settings_edit(move |key: slint::SharedString, value: f32| {
            let Some(ui) = weak.upgrade() else { return };
            strong.borrow_mut().interact(
                "",
                market::MarketAction::SettingsEdit(key.to_string(), value as f64),
            );
            refresh(&ui, &strong);
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_settings_save(move || {
            let Some(ui) = weak.upgrade() else { return };
            let payload = strong.borrow().settings_payload();
            if let Some((name, json)) = payload {
                strong.borrow_mut().interact(
                    &format!("indicator:params:{name}:{json}"),
                    market::MarketAction::ApplyIndicatorSettings(name, json),
                );
                refresh(&ui, &strong);
            }
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_settings_reset(move || {
            let name = strong.borrow().settings_name.clone();
            if name.is_empty() {
                return;
            }
            let Some(ui) = weak.upgrade() else { return };
            strong.borrow_mut().interact(
                &format!("indicator:clear-params:{name}"),
                market::MarketAction::ResetIndicatorSettings(name),
            );
            refresh(&ui, &strong);
        });
    }
    // Download console: local optimistic apply; backend wires stay queued.
    macro_rules! dl_act {
        ($cb:ident, $action:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$cb(move || {
                let Some(ui) = weak.upgrade() else { return };
                strong.borrow_mut().interact_download($action);
                refresh(&ui, &strong);
            });
        }};
    }
    macro_rules! dl_str {
        ($cb:ident, $make:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$cb(move |v: slint::SharedString| {
                let Some(ui) = weak.upgrade() else { return };
                strong.borrow_mut().interact_download($make(v.to_string()));
                refresh(&ui, &strong);
            });
        }};
    }
    dl_act!(on_market_dl_toggle, mdownload::DownloadAction::Toggle);
    dl_act!(
        on_market_dl_select_all,
        mdownload::DownloadAction::SelectAll
    );
    dl_act!(on_market_dl_clear_all, mdownload::DownloadAction::ClearAll);
    dl_act!(on_market_dl_close_cal, mdownload::DownloadAction::CloseCal);
    dl_act!(
        on_market_dl_cal_prev,
        mdownload::DownloadAction::CalPrevMonth
    );
    dl_act!(
        on_market_dl_cal_next,
        mdownload::DownloadAction::CalNextMonth
    );
    dl_act!(on_market_dl_download, mdownload::DownloadAction::Download);
    dl_act!(
        on_market_dl_coverage,
        mdownload::DownloadAction::CheckCoverage
    );
    dl_act!(on_market_dl_cancel, mdownload::DownloadAction::Cancel);
    dl_act!(on_market_dl_retry, mdownload::DownloadAction::Retry);
    dl_act!(
        on_market_dl_view_coverage,
        mdownload::DownloadAction::ViewCoverage
    );
    dl_act!(
        on_market_dl_error_details,
        mdownload::DownloadAction::ToggleErrorDetails
    );
    dl_act!(
        on_market_dl_advanced,
        mdownload::DownloadAction::ToggleAdvanced
    );
    dl_act!(
        on_market_dl_log_toggle,
        mdownload::DownloadAction::ToggleLog
    );
    dl_act!(on_market_dl_log_clear, mdownload::DownloadAction::ClearLog);
    dl_act!(
        on_market_dl_open_creds,
        mdownload::DownloadAction::OpenCreds
    );
    dl_act!(
        on_market_dl_close_creds,
        mdownload::DownloadAction::CloseCreds
    );
    dl_act!(on_market_dl_cred_test, mdownload::DownloadAction::CredTest);
    dl_act!(on_market_dl_cred_save, mdownload::DownloadAction::CredSave);
    dl_act!(
        on_market_dl_cred_clear,
        mdownload::DownloadAction::CredClear
    );
    dl_str!(on_market_dl_interval, mdownload::DownloadAction::Interval);
    dl_str!(
        on_market_dl_toggle_symbol,
        mdownload::DownloadAction::ToggleSymbol
    );
    dl_str!(
        on_market_dl_filter_changed,
        mdownload::DownloadAction::SetFilter
    );
    dl_str!(on_market_dl_open_cal, mdownload::DownloadAction::OpenCal);
    dl_str!(on_market_dl_quick, mdownload::DownloadAction::QuickRange);
    dl_str!(on_market_dl_broker, mdownload::DownloadAction::Broker);
    dl_str!(
        on_market_dl_cred_reveal,
        mdownload::DownloadAction::CredReveal
    );
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_dl_pick_day(move |d: i32| {
            let Some(ui) = weak.upgrade() else { return };
            strong
                .borrow_mut()
                .interact_download(mdownload::DownloadAction::PickDay(d));
            refresh(&ui, &strong);
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_dl_cred_confirm(move |yes: bool| {
            let Some(ui) = weak.upgrade() else { return };
            strong
                .borrow_mut()
                .interact_download(mdownload::DownloadAction::CredConfirmClear(yes));
            refresh(&ui, &strong);
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_market_dl_cred_field(move |k: slint::SharedString, v: slint::SharedString| {
            let Some(ui) = weak.upgrade() else { return };
            strong
                .borrow_mut()
                .interact_download(mdownload::DownloadAction::CredField(
                    k.to_string(),
                    v.to_string(),
                ));
            refresh(&ui, &strong);
        });
    }
}

/// Representative backend-fed connection workspace (FYERS selected, not
/// connected; Zerodha connected). Field shapes mirror the real venue
/// schemas — placeholders only, never values. Real wiring replaces this
/// with the manager snapshot.
pub fn demo_connection_workspace() -> BrokerWorkspace {
    let value = serde_json::json!({
        "brokers": [
            {"id": "zerodha", "display_name": "Zerodha",
             "venue_subtitle": "Kite Connect", "status": "CONNECTED"},
            {"id": "fyers", "display_name": "Fyers",
             "venue_subtitle": "FYERS API v3", "status": "LOGIN_REQUIRED"},
        ],
        "selected_id": "fyers",
        "display_name": "Fyers",
        "venue_subtitle": "FYERS API v3",
        "environment": "paper",
        "status_raw": "LOGIN_REQUIRED",
        "configured": false,
        "can_login": false,
        "can_disconnect": false,
        "reason": "",
        "credential_fields": [
            {"key": "app_id", "label": "App ID",
             "placeholder": "Enter FYERS App ID",
             "secret": false, "required": true},
            {"key": "secret", "label": "Secret",
             "placeholder": "Enter FYERS Secret ID",
             "secret": true, "required": true},
            {"key": "client_id", "label": "Client ID",
             "placeholder": "Enter your Client ID",
             "secret": false, "required": false},
            {"key": "totp_secret", "label": "TOTP Secret",
             "placeholder": "Enter TOTP Secret",
             "secret": true, "required": false},
            {"key": "pin", "label": "PIN",
             "placeholder": "Enter 4-digit PIN",
             "secret": true, "required": false},
        ],
    });
    let mut workspace = BrokerWorkspace::from_json(&value);
    // Demo starts unconfigured: the CTA invites the one obvious action.
    workspace.configured = false;
    workspace
}

/// Switch the active screen (single shell state source).
pub fn select(ui: &AppWindow, screen: ShellScreen) {
    ui.set_active_screen(screen);
    ui.set_screen_title(screen_title(screen).into());
    ui.set_screen_pending(screen_pending(screen));
}

/// Register the navigation handler (call once per window).
pub fn wire(ui: &AppWindow) {
    let handle = ui.as_weak();
    ui.on_nav_selected(move |screen| {
        if let Some(ui) = handle.upgrade() {
            select(&ui, screen);
        }
    });
}

/// Bind the chart viewport zoom state to the UI properties.
pub fn apply_zoom(ui: &AppWindow, zoom: &ChartViewportZoom) {
    ui.set_chart_zoom_level(zoom.level() as f32);
    ui.set_chart_zoom_label(zoom.label().into());
}

/// Register the Reset Zoom action (call once per window). The user action
/// arrives from Slint; viewport state resets centrally in Rust.
pub fn wire_zoom(ui: &AppWindow, zoom: Rc<RefCell<ChartViewportZoom>>) {
    let handle = ui.as_weak();
    ui.on_reset_zoom(move || {
        zoom.borrow_mut().reset();
        if let Some(ui) = handle.upgrade() {
            apply_zoom(&ui, &zoom.borrow());
        }
    });
}

/// Project the Strategy Lab view-model onto the Slint screen. Every rendered
/// value comes from `LabState` (selection, mode, results) — the screen never
/// infers state, so a selected strategy can never coexist with a
/// "no strategy" workspace.
pub fn apply_lab(ui: &AppWindow, state: &LabState) {
    let view = lab::project(state);
    ui.set_lab(LabHeader {
        has_strategy: view.has_strategy,
        name: view.name.into(),
        description: view.description.into(),
        tags: view.tags.into(),
        version: view.version.into(),
        modified: view.modified.into(),
        last_backtest: view.last_backtest.into(),
        state_label: view.state_label.into(),
        state_tone: view.state_tone,
        mode: view.mode,
        outdated: view.outdated,
        run_enabled: view.run_enabled,
        universe: view.universe.into(),
        timeframe: view.timeframe.into(),
        dates: view.dates.into(),
        capital: view.capital.into(),
        show_results: view.show_results,
        tab: view.tab as i32,
        summary_line: view.summary_line.into(),
        empty_reason: view.empty_reason.into(),
        equity_caption: view.equity_caption.into(),
        drawdown_caption: view.drawdown_caption.into(),
        ranking_count: view.ranking_count.into(),
        run_stop: view.run_stop,
        verdict_label: view.verdict_label.into(),
        verdict_note: view.verdict_note.into(),
        verdict_tone: view.verdict_tone,
        progress_label: view.progress_label.into(),
        run_label: view.run_label.into(),
        code: view.code.into(),
        dirty: view.dirty,
        cfg_universe_csv: view.cfg_universe_csv.into(),
        timeframe_index: view.timeframe_index,
        cfg_dates_start: view.cfg_dates_start.into(),
        cfg_dates_end: view.cfg_dates_end.into(),
        cfg_capital: view.cfg_capital.into(),
        config_error: view.config_error.into(),
        rank_search: view.rank_search.into(),
        rank_desc: view.rank_desc,
        rankby_current: view.rankby_current,
        trade_needle: view.trade_needle.into(),
        trade_symbol: view.trade_symbol.into(),
        trade_filters_active: view.trade_filters_active,
        selected_trade: view.selected_trade,
        compare_side: view.compare_side,
        is_compare: view.is_compare,
        detail_symbol: view
            .detail
            .as_ref()
            .map(|d| d.symbol.clone().into())
            .unwrap_or_default(),
        detail_title: view
            .detail
            .as_ref()
            .map(|d| d.title.clone().into())
            .unwrap_or_default(),
        detail_stats: view
            .detail
            .as_ref()
            .map(|d| d.stats.clone().into())
            .unwrap_or_default(),
        detail_caption: view
            .detail
            .as_ref()
            .map(|d| d.caption.clone().into())
            .unwrap_or_default(),
        cmp_verdict: view.compare.banner_verdict.into(),
        cmp_reason: view.compare.banner_reason.into(),
        cmp_tone: view.compare.banner_tone,
        cmp_trade_summary: view.compare.trade_summary.into(),
        cmp_stale: view.compare.stale_notice.into(),
        cmp_scope: view.compare.board_scope.into(),
        cmp_leader: view.compare.board_leader.into(),
        cmp_has_sides: view.compare.has_sides,
        sym_open: view.sym_open,
        sym_search: view.sym_search.into(),
        sym_button_line: view.sym_button_line.into(),
        sym_count_line: view.sym_count_line.into(),
        cfg_cost: view.cfg_cost.into(),
        cfg_cost_warn: view.cfg_cost_warn,
        run_id: view.run_id.into(),
        run_ts: view.run_ts.into(),
        cfg_hash: view.cfg_hash.into(),
        result_pnl: view.result_pnl.into(),
        result_return: view.result_return.into(),
        result_pnl_tone: view.result_pnl_tone,
        result_exec_line: view.result_exec_line.into(),
        stale_exec_line: view.stale_exec_line.into(),
        stale_cur_line: view.stale_cur_line.into(),
        range_line: view.range_line.into(),
        diag_show: view.diag_show,
        diag_title: view.diag_title.into(),
        diag_detail: view.diag_detail.into(),
        diag_stale: view.diag_stale,
        risk_gate_show: view.risk_gate_show,
        risk_gate_text: view.risk_gate_text.into(),
        lens: view.lens,
        strategy_count_line: view.strategy_count_line.into(),
        editing_hint: view.editing_hint.into(),
        studio_ref_pf: view.studio_ref_pf.into(),
        studio_source: view.studio_source.into(),
        equity_select: view.equity_select,
        dates_human: view.dates_human.into(),
        dates_error: view.dates_error.into(),
        today_days: view.today_days,
        dates_start_days: view.dates_start_days,
        dates_end_days: view.dates_end_days,
    });
    ui.set_lab_date_presets(
        Rc::new(slint::VecModel::from(
            view.date_presets
                .into_iter()
                .map(|p| LabPreset {
                    label: p.label.into(),
                    start_days: p.start_days,
                    end_days: p.end_days,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_library(
        Rc::new(slint::VecModel::from(
            view.library
                .into_iter()
                .map(|r| LabLibraryRow {
                    real_index: r.real_index,
                    name: r.name.into(),
                    description: r.description.into(),
                    state_label: r.state_label.into(),
                    favorite: r.favorite,
                    selected: r.selected,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_kpis(
        Rc::new(slint::VecModel::from(
            view.kpis
                .into_iter()
                .map(|k| LabKpi {
                    label: k.label.into(),
                    value: k.value.into(),
                    tone: k.tone,
                    emphasized: k.emphasized,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_ranking(
        Rc::new(slint::VecModel::from(
            view.ranking
                .into_iter()
                .map(|r| LabRankRow {
                    rank: r.rank.into(),
                    symbol: r.symbol.into(),
                    pnl: r.pnl.into(),
                    ret: r.ret.into(),
                    trades: r.trades.into(),
                    win: r.win.into(),
                    pf: r.pf.into(),
                    dd: r.dd.into(),
                    sharpe: r.sharpe.into(),
                    pnl_tone: r.pnl_tone.cell(),
                    pf_tone: r.pf_tone.cell(),
                    unranked: r.unranked,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_trades(
        Rc::new(slint::VecModel::from(
            view.trades
                .into_iter()
                .map(|t| LabTradeRow {
                    no: t.no.into(),
                    abs_index: t.abs_index,
                    symbol: t.symbol.into(),
                    side: t.side.into(),
                    entry: t.entry.into(),
                    entry_px: t.entry_px.into(),
                    exit: t.exit.into(),
                    exit_px: t.exit_px.into(),
                    pnl: t.pnl.into(),
                    r: t.r.into(),
                    bars: t.bars.into(),
                    reason: t.reason.into(),
                    pnl_tone: t.pnl_tone.cell(),
                    selected: t.selected,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    // view coordinates scaled to the fixed 1000x300 chart box (presentation
    // transform only — the engine data itself is untouched).
    let to_points = |series: &Vec<(f32, f32)>| -> Vec<LabPoint> {
        series
            .iter()
            .map(|(x, y)| LabPoint {
                x: x * 1000.0,
                y: y * 300.0,
            })
            .collect()
    };
    ui.set_lab_equity(Rc::new(slint::VecModel::from(to_points(&view.equity))).into());
    ui.set_lab_drawdown(Rc::new(slint::VecModel::from(to_points(&view.drawdown))).into());
    ui.set_lab_params(
        Rc::new(slint::VecModel::from(
            view.params
                .into_iter()
                .map(|p| LabParam {
                    key: p.key.into(),
                    label: p.label.into(),
                    value: p.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let strings = |items: Vec<String>| {
        Rc::new(slint::VecModel::from(
            items
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
    };
    ui.set_lab_timeframes(strings(view.timeframes).into());
    ui.set_lab_rankby_labels(strings(view.rankby_labels).into());
    ui.set_lab_rank_heads(strings(view.rank_heads).into());
    ui.set_lab_trade_heads(strings(view.trade_heads).into());
    let detail = view.detail.unwrap_or_default();
    ui.set_lab_detail_metrics(
        Rc::new(slint::VecModel::from(
            detail
                .metrics
                .into_iter()
                .map(|m| LabDetailMetric {
                    label: m.label.into(),
                    value: m.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_detail_equity(Rc::new(slint::VecModel::from(to_points(&detail.equity))).into());
    ui.set_lab_board_labels(
        strings(view.compare.board.iter().map(|r| r.label.clone()).collect()).into(),
    );
    ui.set_lab_board_columns(strings(view.compare.board_symbols.clone()).into());
    ui.set_lab_board_trades(strings(view.compare.board_trades.clone()).into());
    ui.set_lab_board_cols(view.compare.board_symbols.len().max(1) as i32);
    ui.set_lab_board_cells(
        Rc::new(slint::VecModel::from(
            view.compare
                .board
                .iter()
                .flat_map(|r| r.cells.iter())
                .map(|c| LabBoardCell {
                    symbol: c.symbol.clone().into(),
                    text: c.text.clone().into(),
                    best: c.best,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_matrix(
        Rc::new(slint::VecModel::from(
            view.compare
                .matrix
                .into_iter()
                .map(|m| LabMatrixRow {
                    key: m.key.into(),
                    buy: m.buy.into(),
                    sell: m.sell.into(),
                    winner: m.winner,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let rank_row = |r: lab::RankRow| LabRankRow {
        rank: r.rank.into(),
        symbol: r.symbol.into(),
        pnl: r.pnl.into(),
        ret: r.ret.into(),
        trades: r.trades.into(),
        win: r.win.into(),
        pf: r.pf.into(),
        dd: r.dd.into(),
        sharpe: r.sharpe.into(),
        pnl_tone: r.pnl_tone.cell(),
        pf_tone: r.pf_tone.cell(),
        unranked: r.unranked,
    };
    ui.set_lab_compare_ranking(
        Rc::new(slint::VecModel::from(
            view.compare
                .ranking
                .into_iter()
                .map(rank_row)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_equity_buy(
        Rc::new(slint::VecModel::from(to_points(&view.compare.equity_buy))).into(),
    );
    ui.set_lab_equity_sell(
        Rc::new(slint::VecModel::from(to_points(&view.compare.equity_sell))).into(),
    );
    ui.set_lab_drawdown_buy(
        Rc::new(slint::VecModel::from(to_points(&view.compare.drawdown_buy))).into(),
    );
    ui.set_lab_drawdown_sell(
        Rc::new(slint::VecModel::from(to_points(
            &view.compare.drawdown_sell,
        )))
        .into(),
    );
    ui.set_lab_sym_visible(
        Rc::new(slint::VecModel::from(
            view.sym_visible
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_sym_visible_on(Rc::new(slint::VecModel::from(view.sym_visible_on)).into());
    ui.set_lab_universe_chips(
        Rc::new(slint::VecModel::from(
            view.universe_chips
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_lab_filter_active(filter_kind(state.filter));
}

fn filter_kind(filter: LibFilter) -> i32 {
    match filter {
        LibFilter::All => 0,
        LibFilter::Favorites => 1,
        LibFilter::Recent => 2,
    }
}

/// Wire Strategy Lab interactions: the Slint surface only reports actions;
/// the Rust view-model mutates centrally and re-projects (single state
/// source). RUN stays inert until the engine bridge is wired.
/// One backtest request gathered from the Lab workspace state (the host
/// forwards it to the Python backend; all values are backend-owned echoes).
#[derive(Debug, Clone, PartialEq)]
pub struct LabRunRequest {
    pub strategy: String,
    pub symbols: Vec<String>,
    pub timeframe: String,
    pub start: String,
    pub end: String,
    pub capital: f64,
    pub mode: String,
}

impl LabRunRequest {
    /// Gather the current workspace request; `None` when no strategy is
    /// selected (RUN stays honestly disabled — never invents a request).
    pub fn gather(state: &LabState) -> Option<Self> {
        let name = state.selected_strategy()?.name.clone();
        let known = |s: &String| state.universe_symbols.iter().any(|u| u == s);
        let mut symbols: Vec<String> = state
            .universe_selected
            .iter()
            .filter(|s| known(s))
            .cloned()
            .collect();
        if symbols.is_empty() {
            symbols = state
                .cfg_universe_csv
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty() && known(&s))
                .collect();
        }
        let timeframe = state
            .timeframes
            .get(state.timeframe_index.max(0) as usize)
            .cloned()
            .unwrap_or_default();
        let capital = parse_capital(&state.cfg_capital).unwrap_or(0.0);
        let mode = match state.mode {
            LabMode::Long => "buy",
            LabMode::Short => "sell",
            LabMode::Compare => "compare",
        }
        .to_string();
        Some(LabRunRequest {
            strategy: name,
            symbols,
            timeframe,
            start: state.cfg_dates_start.clone(),
            end: state.cfg_dates_end.clone(),
            capital,
            mode,
        })
    }
}

/// Parse a capital echo ("₹10,00,000", "1000000", "") into a number.
fn parse_capital(text: &str) -> Option<f64> {
    let cleaned: String = text
        .chars()
        .filter(|c| c.is_ascii_digit() || *c == '.' || *c == '-')
        .collect();
    if cleaned.is_empty() {
        return None;
    }
    cleaned.parse::<f64>().ok()
}

pub fn wire_lab(
    ui: &AppWindow,
    state: Rc<RefCell<LabState>>,
    fetch_workspace: Rc<dyn Fn(String)>,
    fetch_run: Rc<dyn Fn(LabRunRequest)>,
) {
    let bind = |ui: &AppWindow, state: &Rc<RefCell<LabState>>, handler: fn(&mut LabState, i32)| {
        let strong = state.clone();
        let handle = ui.as_weak();
        move |arg: i32| {
            strong.borrow_mut().tap(handler, arg);
            if let Some(ui) = handle.upgrade() {
                apply_lab(&ui, &strong.borrow());
            }
        }
    };
    let bind0 = |ui: &AppWindow, state: &Rc<RefCell<LabState>>, handler: fn(&mut LabState)| {
        let strong = state.clone();
        let handle = ui.as_weak();
        move || {
            handler(&mut strong.borrow_mut());
            if let Some(ui) = handle.upgrade() {
                apply_lab(&ui, &strong.borrow());
            }
        }
    };
    {
        // Library selection refreshes the workspace from the backend (code,
        // params, config echoes); the optimistic local select keeps the UI
        // instant, the snapshot makes it truthful.
        let strong = state.clone();
        let weak = ui.as_weak();
        let fetch = fetch_workspace.clone();
        ui.on_lab_library_picked(move |i: i32| {
            let Some(ui) = weak.upgrade() else { return };
            let name = {
                let mut guard = strong.borrow_mut();
                if !guard.interaction_select(i.max(0) as usize) {
                    return;
                }
                guard
                    .selected_strategy()
                    .map(|s| s.name.clone())
                    .unwrap_or_default()
            };
            if !name.is_empty() {
                fetch(name);
            }
            apply_lab(&ui, &strong.borrow());
        });
    }
    ui.on_lab_filter_picked(bind(ui, &state, |s, i| {
        s.interaction_filter(match i {
            1 => LibFilter::Favorites,
            2 => LibFilter::Recent,
            _ => LibFilter::All,
        });
    }));
    ui.on_lab_mode_picked(bind(ui, &state, |s, i| {
        s.interaction_mode(LabMode::from_kind(i))
    }));
    ui.on_lab_tab_picked(bind(ui, &state, |s, i| {
        s.interaction_tab(i.max(0) as usize)
    }));
    {
        // RUN executes a real backend backtest: optimistic Running state for
        // instant feedback, then the engine snapshot (results or an
        // actionable config_error — never fabricated numbers).
        let strong = state.clone();
        let handle = ui.as_weak();
        let fetch = fetch_run.clone();
        ui.on_lab_run_requested(move || {
            let Some(ui) = handle.upgrade() else { return };
            let request = {
                let mut guard = strong.borrow_mut();
                guard.interaction_run();
                if !guard.start_run() {
                    None
                } else {
                    LabRunRequest::gather(&guard)
                }
            };
            if let Some(request) = request {
                apply_lab(&ui, &strong.borrow());
                fetch(request);
            }
            if let Some(ui) = handle.upgrade() {
                apply_lab(&ui, &strong.borrow());
            }
        });
    }
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_lab_search_changed(move |text| {
        strong.borrow_mut().set_search(text.as_str());
        if let Some(ui) = handle.upgrade() {
            apply_lab(&ui, &strong.borrow());
        }
    });
    ui.on_lab_save_requested(bind0(ui, &state, |s| s.interaction_save()));
    ui.on_lab_compile_requested(bind0(ui, &state, |s| s.interaction_compile()));
    ui.on_lab_new_requested(bind0(ui, &state, |s| s.interaction_simple("new")));
    ui.on_lab_row_menu(bind(ui, &state, |s, i| {
        s.interaction_simple(&format!("menu:{}", i.max(0)));
    }));
    ui.on_lab_trade_picked(bind(ui, &state, |s, i| {
        s.interaction_trade_pick(i);
    }));
    ui.on_lab_cmp_side_picked(bind(ui, &state, |s, i| {
        s.interaction_cmpside(i);
    }));
    ui.on_lab_rank_order_toggled(bind0(ui, &state, |s| s.interaction_ranktoggle()));
    ui.on_lab_sym_open(bind0(ui, &state, |s| s.interaction_symopen()));
    ui.on_lab_sym_close(bind0(ui, &state, |s| s.interaction_symclose()));
    ui.on_lab_sym_clear(bind0(ui, &state, |s| s.interaction_symclear()));
    ui.on_lab_sym_apply(bind0(ui, &state, |s| s.interaction_symapply()));
    ui.on_lab_sym_all_visible(bind0(ui, &state, |s| s.interaction_symall()));
    ui.on_lab_run_buy_requested(bind0(ui, &state, |s| s.interaction_simple("runbuy")));
    ui.on_lab_run_sell_requested(bind0(ui, &state, |s| s.interaction_simple("runsell")));
    ui.on_lab_run_all_requested(bind0(ui, &state, |s| s.interaction_simple("runall")));
    ui.on_lab_export_trades_requested(bind0(ui, &state, |s| s.interaction_simple("exporttrades")));
    ui.on_lab_lens_picked(bind(ui, &state, |s, i| s.interaction_lens(i)));
    ui.on_lab_equity_select_picked(bind(ui, &state, |s, i| s.interaction_equity_view(i)));
    ui.on_lab_inspector_close(bind0(ui, &state, |s| s.interaction_detail_close()));
    ui.on_lab_reset_requested(bind0(ui, &state, |s| s.interaction_reset()));
    ui.on_lab_diag_link_clicked(bind0(ui, &state, |s| s.interaction_lens(1)));
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_lab_rank_picked(move |symbol| {
        {
            let mut guard = strong.borrow_mut();
            guard.interaction_simple(&format!("ranksel:{}", symbol.as_str()));
        }
        if let Some(ui) = handle.upgrade() {
            apply_lab(&ui, &strong.borrow());
        }
    });
    macro_rules! on_lab_text {
        ($on:ident, $act:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$on(move |text| {
                {
                    let mut guard = strong.borrow_mut();
                    $act(&mut guard, text.as_str());
                }
                if let Some(ui) = handle.upgrade() {
                    apply_lab(&ui, &strong.borrow());
                }
            });
        }};
    }
    on_lab_text!(on_lab_code_changed, LabState::interaction_codeedit);
    on_lab_text!(on_lab_sym_search_changed, LabState::interaction_symsearch);
    on_lab_text!(on_lab_timeframe_picked, LabState::interaction_timeframe);
    on_lab_text!(on_lab_capital_committed, LabState::interaction_capital);
    on_lab_text!(on_lab_rank_search_changed, LabState::interaction_ranksearch);
    on_lab_text!(on_lab_rankby_picked, LabState::interaction_rankby);
    on_lab_text!(
        on_lab_trade_filter_changed,
        LabState::interaction_tradefilter
    );
    on_lab_text!(on_lab_cost_committed, LabState::interaction_cost);
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_lab_sym_toggle(move |symbol| {
            {
                let mut guard = strong.borrow_mut();
                guard.interaction_symtoggle(symbol.as_str());
            }
            if let Some(ui) = handle.upgrade() {
                apply_lab(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_lab_param_committed(move |key, text| {
            {
                let mut guard = strong.borrow_mut();
                guard.interaction_param(key.as_str(), text.as_str());
            }
            if let Some(ui) = handle.upgrade() {
                apply_lab(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_lab_dates_committed(move |start, end| {
            {
                let mut guard = strong.borrow_mut();
                guard.interaction_dates(start.as_str(), end.as_str());
            }
            if let Some(ui) = handle.upgrade() {
                apply_lab(&ui, &strong.borrow());
            }
        });
    }
}

trait Tap {
    fn tap(&mut self, handler: fn(&mut LabState, i32), arg: i32);
}
impl Tap for LabState {
    fn tap(&mut self, handler: fn(&mut LabState, i32), arg: i32) {
        handler(self, arg);
    }
}

/// Representative lab state for the standalone shell binary: the real
/// workstation library (OBR + SMA records), no fabricated results — results
/// appear only via `LabState::apply_result` from the engine bridge.
pub fn demo_lab_state() -> LabState {
    LabState {
        strategies: vec![
            lab::LabStrategy {
                name: "OBR".into(),
                description: "Opening Range Breakout".into(),
                tags: vec!["BREAKOUT".into(), "INTRADAY".into()],
                version: "1.0".into(),
                modified: "11 Sep 26".into(),
                favorite: true,
                last_backtest: "—".into(),
            },
            lab::LabStrategy {
                name: "SMA".into(),
                description: "SMA crossover".into(),
                tags: vec!["TREND".into()],
                version: "1.0".into(),
                modified: "—".into(),
                favorite: false,
                last_backtest: "—".into(),
            },
        ],
        selected: Some(0),
        config: lab::LabConfig {
            universe: "RELIANCE".into(),
            timeframe: "15m".into(),
            dates: "02 Jan '26 → 11 Sep '26".into(),
            capital: "₹10,00,000".into(),
        },
        run: RunState::Ready,
        engine_wired: false,
        ..LabState::default()
    }
}

/// VISUAL-CHECK FIXTURE — only reachable via the explicit
/// `--visual-check=<state>` CLI flag for layout inspection; the normal run
/// path never produces results without the engine bridge. Values use the
/// unmistakable `99`-pattern and every chart/risk series is captioned
/// "DEMO", so a fixture screen can never be mistaken for real output.
pub fn visual_fixture(kind: &str) -> LabState {
    let mut state = demo_lab_state();
    match kind {
        "no-strategy" => {
            state.selected = None;
        }
        "ready" => {}
        "running" => {
            state.engine_wired = true;
            state.start_run();
        }
        "complete" | "outdated" | "failed" => {
            let kpi = |label: &str, tone: lab::Tone, emphasized: bool| lab::Kpi {
                label: label.into(),
                value: if label == "NET P&L" {
                    "+₹ 99,99,999".into()
                } else {
                    "99.99".into()
                },
                tone,
                emphasized,
            };
            let results = lab::LabResults {
                kpis: vec![
                    kpi("NET P&L", lab::Tone::Positive, true),
                    kpi("TRADES", lab::Tone::Neutral, false),
                    kpi("WIN RATE", lab::Tone::Neutral, false),
                    kpi("PROFIT FACTOR", lab::Tone::Neutral, false),
                    kpi("EXPECTANCY", lab::Tone::Negative, false),
                    kpi("MAX DRAWDOWN", lab::Tone::Negative, false),
                    kpi("SHARPE", lab::Tone::Neutral, false),
                    kpi("AVG TRADE", lab::Tone::Positive, false),
                ],
                ranking: (1..=12)
                    .map(|i| lab::RankRow {
                        rank: i.to_string(),
                        symbol: format!("DEMO{}", i),
                        pnl: if i % 3 == 0 {
                            "-99,999".into()
                        } else {
                            "+99,999".into()
                        },
                        ret: "+9.99".into(),
                        trades: "99".into(),
                        win: "99.9".into(),
                        pf: "9.99".into(),
                        dd: "-9.99".into(),
                        sharpe: "9.99".into(),
                        pnl_tone: if i % 3 == 0 {
                            lab::Tone::Negative
                        } else {
                            lab::Tone::Positive
                        },
                        pf_tone: lab::Tone::Positive,
                        unranked: false,
                    })
                    .collect(),
                trades: (1..=14)
                    .map(|i| lab::TradeRow {
                        no: i.to_string(),
                        abs_index: (i - 1) as i32,
                        symbol: "DEMO1".into(),
                        side: if i % 2 == 0 {
                            "LONG".into()
                        } else {
                            "SHORT".into()
                        },
                        entry: "2,999.99".into(),
                        entry_px: "2,999.99".into(),
                        exit: "2,999.99".into(),
                        exit_px: "2,999.99".into(),
                        pnl: if i % 4 == 0 {
                            "-99.99".into()
                        } else {
                            "+99.99".into()
                        },
                        r: if i % 4 == 0 {
                            "-0.99".into()
                        } else {
                            "+0.99".into()
                        },
                        bars: "9".into(),
                        reason: "DEMO".into(),
                        pnl_tone: if i % 4 == 0 {
                            lab::Tone::Negative
                        } else {
                            lab::Tone::Positive
                        },
                        selected: false,
                    })
                    .collect(),
                equity: (0..=60)
                    .map(|i| {
                        let t = i as f32 / 60.0;
                        (t, 0.25 + 0.5 * t + 0.08 * (t * 18.0).sin())
                    })
                    .collect(),
                drawdown: (0..=60)
                    .map(|i| {
                        let t = i as f32 / 60.0;
                        (t, 0.15 + 0.12 * (t * 21.0).sin().abs())
                    })
                    .collect(),
                risk_notes: vec![
                    "DEMO · max drawdown -9.99% over the tested window".into(),
                    "DEMO · worst trade -0.99R · expectancy -99.99".into(),
                    "DEMO · concentration 9.9% largest position".into(),
                ],
            };
            state.engine_wired = true;
            state.apply_result(results);
            if kind == "failed" {
                state.run = RunState::Failed;
            }
            if kind == "outdated" {
                state.edit_config(|c| c.timeframe = "5m".into());
            }
        }
        _ => {}
    }
    state
}

/// Project the Portfolio view-model onto the Slint screen. Every rendered
/// value comes from `PortfolioState` (backend snapshot + tab/selection) —
/// the screen never infers numbers, so an unconfigured account can never
/// coexist with populated KPIs or tables.
pub fn apply_portfolio(ui: &AppWindow, state: &PortfolioState) {
    let view = portfolio::project(state);
    ui.set_portfolio_account_title(view.account_title.into());
    ui.set_portfolio_account_tone(view.account_tone.badge());
    ui.set_portfolio_account_info(view.account_info.into());
    ui.set_portfolio_account_detail(view.account_detail.into());
    ui.set_portfolio_updated(state.updated_label.clone().into());
    ui.set_portfolio_kpis_primary(
        Rc::new(slint::VecModel::from(
            view.kpis_primary
                .into_iter()
                .map(|k| PortfolioKpi {
                    label: k.label.into(),
                    value: k.value.into(),
                    tone: k.tone.badge(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_kpis_secondary(
        Rc::new(slint::VecModel::from(
            view.kpis_secondary
                .into_iter()
                .map(|k| PortfolioKpi {
                    label: k.label.into(),
                    value: k.value.into(),
                    tone: k.tone.badge(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_perf_has_data(view.perf_has_data);
    ui.set_portfolio_perf_trades(view.perf_trades.into());
    ui.set_portfolio_perf_wins(view.perf_wins.into());
    ui.set_portfolio_perf_losses(view.perf_losses.into());
    ui.set_portfolio_perf_winrate(view.perf_winrate.into());
    ui.set_portfolio_gates(
        Rc::new(slint::VecModel::from(
            view.gates
                .into_iter()
                .map(|g| PortfolioGate {
                    label: g.label.into(),
                    status: g.status.into(),
                    tone: g.tone.badge(),
                    detail: g.detail.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_positions_count(view.positions_count.into());
    ui.set_portfolio_has_positions(!view.positions.is_empty());
    ui.set_portfolio_positions(
        Rc::new(slint::VecModel::from(
            view.positions
                .into_iter()
                .map(|p| PortfolioPosition {
                    symbol: p.symbol.into(),
                    side: p.side.into(),
                    qty: p.qty.into(),
                    avg: p.avg.into(),
                    ltp: p.ltp.into(),
                    value: p.value.into(),
                    pnl: p.pnl.into(),
                    pnl_tone: p.pnl_tone.badge(),
                    alloc: p.alloc.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_positions_empty_title(view.positions_empty_title.into());
    ui.set_portfolio_positions_empty_detail(view.positions_empty_detail.into());
    ui.set_portfolio_selected_position(state.selected_position.map(|i| i as i32).unwrap_or(-1));
    ui.set_portfolio_allocation(
        Rc::new(slint::VecModel::from(
            view.allocation
                .into_iter()
                .map(|a| PortfolioAlloc {
                    symbol: a.symbol.into(),
                    pct_label: a.pct_label.into(),
                    pct: a.pct,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_alloc_has_data(view.alloc_has_data);
    ui.set_portfolio_detail_symbol(view.detail.symbol.into());
    ui.set_portfolio_detail_side_tone(if view.detail.side.is_empty() { 0 } else { 4 });
    ui.set_portfolio_detail_side(view.detail.side.into());
    ui.set_portfolio_detail_qty(view.detail.qty.into());
    ui.set_portfolio_detail_avg(view.detail.avg.into());
    ui.set_portfolio_detail_value(view.detail.value.into());
    ui.set_portfolio_detail_pnl(view.detail.pnl.into());
    ui.set_portfolio_detail_pnl_tone(view.detail.pnl_tone.badge());
    ui.set_portfolio_detail_alloc(view.detail.alloc.into());
    ui.set_portfolio_orders_tab(view.orders_tab as i32);
    ui.set_portfolio_orders(
        Rc::new(slint::VecModel::from(
            view.orders
                .into_iter()
                .map(|o| PortfolioOrder {
                    id: o.id.into(),
                    time: o.time.into(),
                    symbol: o.symbol.into(),
                    side: o.side.into(),
                    qty: o.qty.into(),
                    status: o.status.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_fills(
        Rc::new(slint::VecModel::from(
            view.fills
                .into_iter()
                .map(|f| PortfolioFill {
                    time: f.time.into(),
                    symbol: f.symbol.into(),
                    side: f.side.into(),
                    qty: f.qty.into(),
                    price: f.price.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_active_has_rows(view.active_has_rows);
    ui.set_portfolio_active_empty_title(view.active_empty_title.into());
    ui.set_portfolio_active_empty_detail(view.active_empty_detail.into());
    ui.set_portfolio_health_label(view.health_label.into());
    ui.set_portfolio_health_tone(view.health_tone.badge());
    ui.set_portfolio_health_note(view.health_note.into());
    ui.set_portfolio_risk_rows(
        Rc::new(slint::VecModel::from(
            view.risk_rows
                .into_iter()
                .map(|r| PortfolioRisk {
                    label: r.label.into(),
                    value: r.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_portfolio_risk_has_data(view.risk_has_data);
    ui.set_portfolio_risk_empty_detail(view.risk_empty_detail.into());
}

/// Wire Portfolio interactions: the Slint surface only reports actions; the
/// Rust view-model mutates centrally and re-projects (single state source).
/// Refresh re-projects the current backend snapshot (the bridge sets the
/// stamp); tab/selection switch instantly without navigation.
pub fn wire_portfolio(ui: &AppWindow, state: Rc<RefCell<PortfolioState>>) {
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_portfolio_refresh_requested(move || {
        if let Some(ui) = handle.upgrade() {
            apply_portfolio(&ui, &strong.borrow());
        }
    });
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_portfolio_orders_tab_picked(move |tab| {
        strong.borrow_mut().set_orders_tab(tab.max(0) as usize);
        if let Some(ui) = handle.upgrade() {
            apply_portfolio(&ui, &strong.borrow());
        }
    });
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_portfolio_position_picked(move |index| {
        let _ = strong.borrow_mut().select_position((index.max(0)) as usize);
        if let Some(ui) = handle.upgrade() {
            apply_portfolio(&ui, &strong.borrow());
        }
    });
}

/// Project the Research view-model onto the Slint screen. Every rendered
/// value comes from `ResearchState` — the screen never infers state, so a
/// stored result can never present as current after its inputs changed.
#[allow(clippy::too_many_lines)]
pub fn apply_research(ui: &AppWindow, state: &ResearchState) {
    let view = research_state::project(state);
    ui.set_research_strategy_name(view.strategy_name.into());
    ui.set_research_strategy_version(view.strategy_version.into());
    ui.set_research_experiment_id(view.experiment_id.into());
    ui.set_research_context_line(view.context_line.into());
    ui.set_research_state_label(view.state_label.into());
    ui.set_research_state_tone(view.state_tone);
    ui.set_research_has_strategy(view.has_strategy);
    ui.set_research_show_results(view.show_results);
    ui.set_research_stale(view.stale);
    ui.set_research_run_enabled(view.run_enabled);
    ui.set_research_run_blocked_reason(view.run_blocked_reason.into());
    ui.set_research_cancel_visible(view.cancel_visible);
    ui.set_research_create_visible(view.create_visible);
    ui.set_research_create_error(view.create_error.into());
    ui.set_research_hypothesis(view.hypothesis.into());
    ui.set_research_question(view.question.into());
    ui.set_research_effect(view.effect.into());
    ui.set_research_signal_filter(view.signal_filter.into());
    ui.set_research_signal_count_line(view.signal_count_line.into());
    ui.set_research_trade_count_line(view.trade_count_line.into());
    ui.set_research_empty_title(view.empty_title.into());
    ui.set_research_empty_detail(view.empty_detail.into());
    ui.set_research_conclusion(view.conclusion.into());
    ui.set_research_validation_status(view.validation_status.into());
    ui.set_research_validation_summary(view.validation_summary.into());
    ui.set_research_fingerprint_line(view.fingerprint_line.into());
    ui.set_research_strategy_count(view.strategy_count.into());
    ui.set_research_experiment_count(view.experiment_count.into());
    ui.set_research_tab(view.tab as i32);
    ui.set_research_inspector_open(view.inspector_open);
    ui.set_research_narrow_view(view.narrow_view as i32);
    ui.set_research_log_expanded(view.log_expanded);
    ui.set_research_log_status(view.log_status.into());
    ui.set_research_strategies(
        Rc::new(slint::VecModel::from(
            view.strategies
                .into_iter()
                .map(|s| ResearchStrategyRow {
                    name: s.name.into(),
                    description: s.description.into(),
                    version: s.version.into(),
                    selected: s.selected,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_experiments(
        Rc::new(slint::VecModel::from(
            view.experiments
                .into_iter()
                .map(|e| ResearchExperimentRow {
                    id: e.id.into(),
                    strategy: e.strategy.into(),
                    status: e.status.into(),
                    status_tone: e.status_tone,
                    selected: e.selected,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_config_groups(
        Rc::new(slint::VecModel::from(
            view.config_groups
                .into_iter()
                .map(|g| ResearchConfigGroup {
                    title: g.title.into(),
                    hint: g.hint.into(),
                    fields: Rc::new(slint::VecModel::from(
                        g.fields
                            .into_iter()
                            .map(|f| ResearchField {
                                key: f.key.into(),
                                label: f.label.into(),
                                value: f.value.into(),
                                kind: f.kind,
                                options: Rc::new(slint::VecModel::from(
                                    f.options.into_iter().map(<_>::into).collect::<Vec<_>>(),
                                ))
                                .into(),
                                selected: f.selected as i32,
                            })
                            .collect::<Vec<_>>(),
                    ))
                    .into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_evidence_verdict(view.evidence_verdict.into());
    ui.set_research_evidence_tone(view.evidence_tone);
    ui.set_research_evidence_grade(view.evidence_grade.into());
    ui.set_research_evidence_why(
        Rc::new(slint::VecModel::from(
            view.evidence_why
                .into_iter()
                .map(|w| ResearchEvidenceWhy {
                    kind: w.kind.into(),
                    text: w.text.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_evidence_dims(
        Rc::new(slint::VecModel::from(
            view.evidence_dims
                .into_iter()
                .map(|d| ResearchEvidenceDim {
                    name: d.name.into(),
                    status: d.status.into(),
                    tone: d.tone,
                    detail: d.detail.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_hypothesis_open(view.hypothesis_open);
    ui.set_research_why_open(view.why_open);
    ui.set_research_technical_open(view.technical_open);
    ui.set_research_show_next_steps(view.show_next_steps);
    let results = state.results.clone().unwrap_or_default();
    ui.set_research_metrics(
        Rc::new(slint::VecModel::from(
            view.metrics
                .into_iter()
                .map(|m| ResearchMetric {
                    label: m.label.into(),
                    value: m.value.into(),
                    tone: m.tone,
                    emphasized: m.emphasized,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let shown_signals: Vec<research_state::SignalRow> = if view.show_results {
        state.filtered_signals()
    } else {
        Vec::new()
    };
    ui.set_research_signals(
        Rc::new(slint::VecModel::from(
            shown_signals
                .into_iter()
                .map(|s| ResearchSignalRow {
                    time: s.time.into(),
                    symbol: s.symbol.into(),
                    tf: s.tf.into(),
                    side: s.side.into(),
                    price: s.price.into(),
                    event: s.event.into(),
                    strategy: s.strategy.into(),
                    exp: s.exp.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_trades(
        Rc::new(slint::VecModel::from(
            results
                .trades
                .into_iter()
                .map(|t| ResearchTradeRow {
                    no: t.no.into(),
                    entry: t.entry.into(),
                    exit: t.exit.into(),
                    side: t.side.into(),
                    qty: t.qty.into(),
                    pnl: t.pnl.into(),
                    reason: t.reason.into(),
                    hold: t.hold.into(),
                    pnl_tone: t.pnl_tone.kind(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_robustness(
        Rc::new(slint::VecModel::from(
            results
                .robustness
                .into_iter()
                .map(|r| ResearchRobustRow {
                    test: r.test.into(),
                    input: r.input.into(),
                    stability: r.stability.into(),
                    stability_tone: r.stability_tone.badge(),
                    evidence: r.evidence.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let kv = |rows: Vec<research_state::KvRow>| {
        Rc::new(slint::VecModel::from(
            rows.into_iter()
                .map(|r| ResearchKv {
                    label: r.label.into(),
                    value: r.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
    };
    let kv_view = |rows: Vec<research_state::KvView>| {
        Rc::new(slint::VecModel::from(
            rows.into_iter()
                .map(|r| ResearchKv {
                    label: r.label.into(),
                    value: r.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
    };
    ui.set_research_validation_rows(kv(results.validation_rows).into());
    ui.set_research_quality_rows(kv(results.quality_rows).into());
    // No executed result yet: inspector still carries the real live-form
    // facts (identity, config, status) — never blank, never invented.
    let inspector_rows = if state.results.is_some() {
        results.inspector_rows
    } else {
        state.default_inspector_rows()
    };
    ui.set_research_inspector_rows(kv(inspector_rows).into());
    ui.set_research_report_sections(kv(results.report_sections).into());
    ui.set_research_compare_rows(
        Rc::new(slint::VecModel::from(
            results
                .compare_rows
                .into_iter()
                .map(|c| ResearchCompareRow {
                    exp: c.exp.into(),
                    strategy: c.strategy.into(),
                    status: c.status.into(),
                    detail: c.detail.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_compare_verdict(results.compare_verdict.into());
    ui.set_research_stats_rows(kv_view(view.stats_rows).into());
    ui.set_research_montecarlo_rows(kv_view(view.montecarlo_rows).into());
    ui.set_research_benchmark_rows(kv_view(view.benchmark_rows).into());
    ui.set_research_inspector_groups(
        Rc::new(slint::VecModel::from(
            view.inspector_groups
                .into_iter()
                .map(|g| ResearchKvGroup {
                    title: g.title.into(),
                    rows: Rc::new(slint::VecModel::from(
                        g.rows
                            .into_iter()
                            .map(|r| ResearchKv {
                                label: r.label.into(),
                                value: r.value.into(),
                            })
                            .collect::<Vec<_>>(),
                    ))
                    .into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_research_log_lines(
        Rc::new(slint::VecModel::from(
            view.log_lines
                .into_iter()
                .map(<_>::into)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
}

/// Re-project after one state mutation (single state source per screen).
fn refresh_research(handle: &slint::Weak<AppWindow>, state: &Rc<RefCell<ResearchState>>) {
    if let Some(ui) = handle.upgrade() {
        apply_research(&ui, &state.borrow());
    }
}

/// Wire Research interactions: the Slint surface only reports actions; the
/// Rust view-model mutates centrally and re-projects.
pub fn wire_research(ui: &AppWindow, state: Rc<RefCell<ResearchState>>) {
    macro_rules! wire_int {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move |arg: i32| {
                $action(&mut strong.borrow_mut(), arg);
                refresh_research(&handle, &strong);
            });
        }};
    }
    macro_rules! wire_text {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move |text: slint::SharedString| {
                $action(&mut strong.borrow_mut(), text.as_str());
                refresh_research(&handle, &strong);
            });
        }};
    }
    macro_rules! wire_unit {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move || {
                $action(&mut strong.borrow_mut());
                refresh_research(&handle, &strong);
            });
        }};
    }
    wire_int!(
        on_research_strategy_picked,
        |s: &mut ResearchState, i: i32| {
            let _ = s.select_strategy(i.max(0) as usize);
        }
    );
    wire_int!(on_research_tab_picked, |s: &mut ResearchState, i: i32| {
        s.set_tab(i.max(0) as usize);
    });
    wire_int!(
        on_research_narrow_view_picked,
        |s: &mut ResearchState, i: i32| {
            s.set_narrow_view(i.max(0) as usize);
        }
    );
    wire_int!(
        on_research_experiment_picked,
        |s: &mut ResearchState, i: i32| {
            let _ = s.pick_experiment(i.max(0) as usize);
        }
    );
    wire_unit!(
        on_research_hypothesis_toggled,
        ResearchState::toggle_hypothesis
    );
    wire_unit!(on_research_why_toggled, ResearchState::toggle_why);
    wire_unit!(
        on_research_technical_toggled,
        ResearchState::toggle_technical
    );
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_research_config_edited(move |key: slint::SharedString, text: slint::SharedString| {
            let _ = strong.borrow_mut().edit_field(key.as_str(), text.as_str());
            refresh_research(&handle, &strong);
        });
    }
    {
        // ComboBox reports the selected value (not the index): resolve it
        // against the canonical option lists (unknown values are no-ops).
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_research_config_option(
            move |key: slint::SharedString, value: slint::SharedString| {
                let mut state = strong.borrow_mut();
                let _ = match key.as_str() {
                    "timeframe" => research_state::TIMEFRAME_OPTIONS
                        .iter()
                        .position(|o| *o == value.as_str())
                        .map_or(false, |i| state.set_timeframe(i)),
                    "side" => research_state::SIDE_OPTIONS
                        .iter()
                        .position(|o| *o == value.as_str())
                        .map_or(false, |i| state.set_side(i)),
                    "direction" => research_state::DIRECTION_OPTIONS
                        .iter()
                        .position(|o| *o == value.as_str())
                        .map_or(false, |i| state.set_direction(i)),
                    _ => false,
                };
                drop(state);
                refresh_research(&handle, &strong);
            },
        );
    }
    wire_text!(on_research_hypothesis_edited, ResearchState::set_hypothesis);
    wire_text!(on_research_question_edited, ResearchState::set_question);
    wire_text!(on_research_effect_edited, ResearchState::set_effect);
    wire_text!(
        on_research_signal_filter_changed,
        ResearchState::set_signal_filter
    );
    wire_unit!(on_research_create_pressed, |s: &mut ResearchState| {
        let _ = s.create();
    });
    wire_unit!(on_research_run_pressed, |s: &mut ResearchState| {
        let _ = s.start_run();
    });
    wire_unit!(on_research_cancel_pressed, |s: &mut ResearchState| {
        let _ = s.cancel_run();
    });
    wire_unit!(
        on_research_inspector_toggled,
        ResearchState::toggle_inspector
    );
}

/// Honest disconnected portfolio state for the standalone shell binary: the
/// shape the bridge feeds before any account is configured — no numbers
/// invented. Real wiring replaces this with the live workspace snapshot.
pub fn demo_portfolio_state() -> PortfolioState {
    PortfolioState {
        snapshot: portfolio::demo_unconfigured_snapshot(),
        ..PortfolioState::default()
    }
}

/// Representative FUNDED portfolio state (paper account, one position, one
/// order + fill) for render-matrix verification — never product data.
pub fn demo_portfolio_configured() -> PortfolioState {
    PortfolioState {
        snapshot: portfolio::demo_configured_snapshot(),
        ..PortfolioState::default()
    }
}

/// Project the LIVE view-model onto the Slint workstation. Every rendered
/// value comes from `LiveState` facts (gates, session, blotters) — the
/// screen never infers connection, readiness or run state.
pub fn apply_live(ui: &AppWindow, state: &LiveState) {
    let view = live::project(state);
    ui.set_live_bar(LiveBar {
        mode: view.bar.mode,
        broker_label: view.bar.broker_label.into(),
        broker_tone: view.bar.broker_tone,
        conn_label: view.bar.conn_label.into(),
        conn_tone: view.bar.conn_tone,
        strategy_label: view.bar.strategy_label.into(),
        strategy_tone: view.bar.strategy_tone,
        risk_label: view.bar.risk_label.into(),
        risk_tone: view.bar.risk_tone,
        recon_label: view.bar.recon_label.into(),
        recon_tone: view.bar.recon_tone,
        exec_label: view.bar.exec_label.into(),
        exec_tone: view.bar.exec_tone,
        halted: view.bar.halted,
        halt_enabled: view.bar.halt_enabled,
        inspector_open: view.bar.inspector_open,
        note: view.bar.note.into(),
        note_tone: view.bar.note_tone,
    });
    ui.set_live_inspector_open(view.bar.inspector_open);
    ui.set_live_setup(LiveSetup {
        quantity: view.setup.quantity.into(),
        session_label: view.setup.session_label.into(),
        session_tone: view.setup.session_tone,
        can_start: view.setup.can_start,
        can_stop: view.setup.can_stop,
        can_arm: view.setup.can_arm,
        needs_live_confirm: view.setup.needs_live_confirm,
        confirmed_live: view.setup.confirmed_live,
        symbol_total: view.setup.symbol_total.into(),
    });
    ui.set_live_strategy_names(
        Rc::new(slint::VecModel::from(
            view.setup
                .strategy_names
                .into_iter()
                .map(<_>::into)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_strategy_selected(view.setup.strategy_selected);
    ui.set_live_timeframe_names(
        Rc::new(slint::VecModel::from(
            view.setup
                .timeframe_names
                .into_iter()
                .map(<_>::into)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_timeframe_selected(view.setup.timeframe_selected);
    ui.set_live_market(LiveMarket {
        has_data: view.market.has_data,
        state_label: view.market.state_label.into(),
        tone: view.market.tone,
        title: view.market.title.into(),
        detail: view.market.detail.into(),
        header: view.market.header.into(),
    });
    ui.set_live_candles(
        Rc::new(slint::VecModel::from(
            view.candles
                .into_iter()
                .map(|c| LiveCandle {
                    x: c.x,
                    open: c.open,
                    high: c.high,
                    low: c.low,
                    close: c.close,
                    up: c.up,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_symbols(
        Rc::new(slint::VecModel::from(
            view.symbols
                .into_iter()
                .map(|s| LiveSymbolRow {
                    real_index: s.real_index,
                    name: s.name.into(),
                    checked: s.checked,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_symbol_filter(view.symbol_filter.into());
    ui.set_live_gates(
        Rc::new(slint::VecModel::from(
            view.gates
                .into_iter()
                .map(|g| LiveGate {
                    name: g.name.into(),
                    status: g.status.into(),
                    tone: g.tone,
                    reason: g.reason.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_arm_note(view.arm_note.into());
    ui.set_live_blockers(
        Rc::new(slint::VecModel::from(
            view.setup
                .blockers
                .into_iter()
                .map(<_>::into)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let kv_model = |rows: Vec<live::KvRow>| {
        Rc::new(slint::VecModel::from(
            rows.into_iter()
                .map(|r| LiveKv {
                    key: r.key.into(),
                    value: r.value.into(),
                    tone: r.tone,
                })
                .collect::<Vec<_>>(),
        )) as Rc<slint::VecModel<LiveKv>>
    };
    ui.set_live_strategy_rows(kv_model(view.strategy_rows).into());
    ui.set_live_position_rows(kv_model(view.position_rows).into());
    ui.set_live_risk_rows(kv_model(view.risk_rows).into());
    ui.set_live_broker_rows(kv_model(view.broker_rows).into());
    ui.set_live_recon_rows(kv_model(view.recon_rows).into());
    ui.set_live_positions(
        Rc::new(slint::VecModel::from(
            view.positions
                .into_iter()
                .map(|p| LivePosition {
                    symbol: p.symbol.into(),
                    side: p.side.into(),
                    qty: p.qty.into(),
                    entry: p.entry.into(),
                    current: p.current.into(),
                    pnl: p.pnl.into(),
                    pnl_tone: p.pnl_tone,
                    status: p.status.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_orders(
        Rc::new(slint::VecModel::from(
            view.orders
                .into_iter()
                .map(|o| LiveOrder {
                    order_id: o.order_id.into(),
                    strategy: o.strategy.into(),
                    symbol: o.symbol.into(),
                    side: o.side.into(),
                    qty: o.qty.into(),
                    order_type: o.order_type.into(),
                    price: o.price.into(),
                    status: o.status.into(),
                    time: o.time.into(),
                    broker: o.broker.into(),
                    status_tone: o.status_tone,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_fills(
        Rc::new(slint::VecModel::from(
            view.fills
                .into_iter()
                .map(|f| LiveFill {
                    time: f.time.into(),
                    symbol: f.symbol.into(),
                    side: f.side.into(),
                    qty: f.qty.into(),
                    price: f.price.into(),
                    order_id: f.order_id.into(),
                    strategy: f.strategy.into(),
                    side_tone: f.side_tone,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_stats(
        Rc::new(slint::VecModel::from(
            view.stats
                .into_iter()
                .map(|s| LiveStat {
                    label: s.label.into(),
                    value: s.value.into(),
                    tone: s.tone,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_events(
        Rc::new(slint::VecModel::from(
            view.events
                .into_iter()
                .map(|e| LiveEventRow {
                    timestamp: e.timestamp.into(),
                    strategy: e.strategy.into(),
                    symbol: e.symbol.into(),
                    event: e.event.into(),
                    status: e.status.into(),
                    status_tone: e.status_tone,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_event_types(
        Rc::new(slint::VecModel::from(
            view.event_types
                .into_iter()
                .map(<_>::into)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_live_event_type_index(view.event_type_index);
    ui.set_live_event_filter(view.event_filter.into());
}

fn refresh_live(handle: &slint::Weak<AppWindow>, state: &Rc<RefCell<LiveState>>) {
    if let Some(ui) = handle.upgrade() {
        apply_live(&ui, &state.borrow());
    }
}

/// Wire LIVE interactions: the Slint surface only reports actions; the
/// view-model validates centrally (fail-closed) and re-projects.
pub fn wire_live(ui: &AppWindow, state: Rc<RefCell<LiveState>>) {
    macro_rules! wire_int {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move |arg: i32| {
                $action(&mut strong.borrow_mut(), arg);
                refresh_live(&handle, &strong);
            });
        }};
    }
    macro_rules! wire_text {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move |text: slint::SharedString| {
                $action(&mut strong.borrow_mut(), text.as_str());
                refresh_live(&handle, &strong);
            });
        }};
    }
    macro_rules! wire_unit {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move || {
                $action(&mut strong.borrow_mut());
                refresh_live(&handle, &strong);
            });
        }};
    }
    wire_int!(on_live_mode_picked, |s: &mut LiveState, i: i32| {
        s.set_mode(ExecMode::from_kind(i));
    });
    // ComboBox reports values (Slint `selected`): resolve centrally against
    // the canonical option lists (unknown values are honest no-ops).
    wire_text!(on_live_strategy_picked, LiveState::select_strategy_value);
    wire_int!(on_live_symbol_toggled, |s: &mut LiveState, i: i32| {
        s.toggle_symbol(i.max(0) as usize);
    });
    wire_text!(on_live_timeframe_picked, LiveState::select_timeframe_value);
    wire_text!(on_live_event_type_picked, LiveState::apply_event_type);
    wire_text!(on_live_symbol_filter_changed, LiveState::set_symbol_filter);
    wire_text!(on_live_event_filter_changed, LiveState::set_event_filter);
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_live_quantity_edited(move |text: slint::SharedString| {
            strong.borrow_mut().set_quantity(text.as_str());
            refresh_live(&handle, &strong);
        });
    }
    wire_unit!(on_live_start_requested, LiveState::start);
    wire_unit!(on_live_stop_requested, LiveState::stop);
    wire_unit!(on_live_arm_requested, LiveState::arm);
    wire_unit!(on_live_halt_requested, LiveState::halt);
    wire_unit!(on_live_confirm_requested, LiveState::confirm_live);
    wire_unit!(on_live_inspector_toggled, LiveState::toggle_inspector);
    {
        // CONFIGURE BROKER is navigation intent — the shell owns the
        // screen switch; no broker logic lives in the LIVE surface.
        let handle = ui.as_weak();
        ui.on_live_configure_broker(move || {
            if let Some(ui) = handle.upgrade() {
                select(&ui, ShellScreen::Broker);
            }
        });
    }
}

/// Representative LIVE state for the standalone shell binary — the exact
/// static readiness of this workstation (`--check-live` facts): no live
/// venue adapter, PAPER default, real strategy library and store head,
/// empty execution tables. Nothing is fabricated.
pub fn demo_live_state() -> LiveState {
    let gate = |name: &str, reason: &str| Gate {
        name: name.into(),
        status: GateStatus::Ready,
        reason: reason.into(),
    };
    let blocked = |name: &str, reason: &str| Gate {
        name: name.into(),
        status: GateStatus::NotReady,
        reason: reason.into(),
    };
    LiveState {
        mode: ExecMode::Paper,
        broker: live::BrokerFacts {
            name: "NOT CONFIGURED".into(),
            status: String::new(),
            connected: None,
            reason: "no live venue adapter is registered".into(),
            environment: "paper".into(),
            capabilities: vec!["historical_data.candles".into()],
            latency_ms: None,
            last_heartbeat: String::new(),
        },
        gates: vec![
            blocked(
                "BROKER_ADAPTER_READY",
                "live gate off: LIVE_TRADING_ENABLED; live gate off: BROKER_LIVE_ENABLED",
            ),
            blocked(
                "CREDENTIALS_READY",
                "missing account_id; secret not resolvable: API_KEY; secret not resolvable: API_SECRET",
            ),
            blocked("ACCOUNT_CONFIRMED", "no adapter to confirm against"),
            gate("RISK_CONFIGURATION_VALID", ""),
            gate("EXECUTION_SAFETY_ENABLED", ""),
            gate("STRATEGY", ""),
            gate("MARKET DATA", ""),
            gate("CLOCK", "local clock only"),
            blocked("ENVIRONMENT", "requested=live configured=none"),
            blocked("ARMING", "DISARMED — no live session exists to arm"),
        ],
        risk_status: live::RiskStatus::Ready,
        reconciliation: live::ReconciliationFacts {
            status: "NOT CONFIGURED".into(),
            positions: "N/A".into(),
            orders: "N/A".into(),
            last_check: String::new(),
            mismatches: String::new(),
            blocks_live: true,
        },
        strategies: vec!["OBR".into(), "Untitled Strategy".into()],
        symbols: [
            "360ONE", "AARTIIND", "ABCAPITAL", "ABFRL", "ADANIENT", "ADANIPORTS", "AMBUJACEM",
            "ANGELONE", "APOLLOHOSP", "ASIANPAINT", "BAJFINANCE", "BHARTIARTL", "DMART", "HAL",
            "HDFCBANK", "ICICIBANK", "INFY", "IRCTC", "ITC", "JSWSTEEL", "LT", "NESTLEIND",
            "RELIANCE", "SBIN", "SUNPHARMA", "TATACHEM", "TATASTEEL", "TCS", "TITAN",
        ]
        .into_iter()
        .map(|symbol| SymbolPick {
            symbol: symbol.into(),
            checked: false,
        })
        .collect(),
        store_total: Some(527),
        timeframes: [
            "1m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W",
        ]
        .into_iter()
        .map(String::from)
        .collect(),
        timeframe_index: Some(3),
        market_timeframe: "15m".into(),
        ..LiveState::default()
    }
}

/// Representative RUNNING paper-session shape for render-matrix
/// verification ONLY — the snapshot layout a wired bridge would feed
/// (position/order/fill rows follow the service snapshot schema). Never
/// product data; never reachable from the normal startup path.
pub fn demo_live_state_running() -> LiveState {
    LiveState {
        session: live::SessionStatus::Running,
        lifecycle: "RUNNING".into(),
        bridge_wired: true,
        strategy_index: Some(0),
        symbols: [
            "RELIANCE",
            "TCS",
            "INFY",
            "HDFCBANK",
            "ICICIBANK",
            "SBIN",
            "ITC",
            "LT",
        ]
        .into_iter()
        .map(|symbol| SymbolPick {
            symbol: symbol.into(),
            checked: matches!(symbol, "RELIANCE" | "TCS"),
        })
        .collect(),
        store_total: Some(527),
        timeframe_index: Some(3),
        quantity: 10.0,
        positions: vec![live::PositionRow {
            symbol: "RELIANCE".into(),
            side: "LONG".into(),
            quantity: "10".into(),
            entry: "2801.10".into(),
            current: "2812.40".into(),
            pnl: "+113.00".into(),
            status: "OPEN".into(),
        }],
        position_facts: Some(live::PositionFacts {
            instrument: "RELIANCE".into(),
            side: "LONG".into(),
            quantity: "10".into(),
            avg_price: "2801.10".into(),
            current_price: "2812.40".into(),
            unrealized: "+113.00".into(),
            realized: "+0.00".into(),
            exposure: "28124.00".into(),
            risk_utilization: "—".into(),
        }),
        orders: vec![live::OrderRow {
            order_id: "cid-7f3a1".into(),
            strategy: "OBR".into(),
            symbol: "RELIANCE".into(),
            side: "BUY".into(),
            quantity: "10".into(),
            order_type: "LIMIT".into(),
            price: "2801.10".into(),
            status: "COMPLETE".into(),
            time: "13:00:02".into(),
            broker: "paper".into(),
        }],
        fills: vec![live::FillRow {
            time: "13:00:04".into(),
            symbol: "RELIANCE".into(),
            side: "BUY".into(),
            quantity: "10".into(),
            price: "2801.10".into(),
            order_id: "cid-7f3a1".into(),
            strategy: "OBR".into(),
        }],
        strategy_facts: Some(live::StrategyFacts {
            id: "OBR".into(),
            version: "1.0".into(),
            status: "RUNNING".into(),
            mode: "PAPER".into(),
            instrument: "RELIANCE, TCS".into(),
            timeframe: "15m".into(),
            live_supported: Some(true),
            warmup: Some(60),
            state: "RELIANCE: no signal yet; TCS: no signal yet".into(),
            params: vec!["range_minutes=15".into(), "target_atr=2.0".into()],
        }),
        pnl: live::Pnl {
            realized: Some(0.0),
            unrealized: Some(113.0),
            exposure: Some(28124.0),
            orders: Some(1),
            fills: Some(1),
            wins: Some(1),
            losses: Some(0),
        },
        events: vec![
            live::LiveEvent {
                timestamp: "13:00:02".into(),
                strategy: "OBR".into(),
                symbol: "RELIANCE".into(),
                event: "ORDER_SUBMITTED".into(),
                status: "ok".into(),
            },
            live::LiveEvent {
                timestamp: "13:00:04".into(),
                strategy: "OBR".into(),
                symbol: "RELIANCE".into(),
                event: "ORDER_FILL".into(),
                status: "ok".into(),
            },
        ],
        market_state: live::DataState::Ready,
        market_symbol: "RELIANCE".into(),
        market_timeframe: "15m".into(),
        market_last_price: Some(2812.4),
        market_bar_count: 480,
        bars: (0..48)
            .map(|i| {
                let base = 2790.0 + (i as f64) * 0.6 + ((i * i) as f64 % 7.0);
                live::Candle {
                    open: base,
                    high: base + 3.4,
                    low: base - 2.1,
                    close: if i % 3 == 0 { base - 1.2 } else { base + 1.8 },
                }
            })
            .collect(),
        ..demo_live_state()
    }
}

/// Representative backend-fed snapshot (paper venue, healthy, gates not
/// green). Real wiring replaces this with the live BrokerPanel.
pub fn demo_snapshot() -> BrokerPanel {
    BrokerPanel {
        broker_id: "paper".into(),
        display_name: "Paper".into(),
        environment: Environment::Paper,
        capabilities: vec![
            crate::view_model::CapabilityRow {
                id: "orders.market".into(),
                status: CapabilityStatus::Supported,
            },
            crate::view_model::CapabilityRow {
                id: "account.funds".into(),
                status: CapabilityStatus::Supported,
            },
            crate::view_model::CapabilityRow {
                id: "orders.modify".into(),
                status: CapabilityStatus::NotSupported,
            },
        ],
        health: crate::view_model::HealthState::Connected,
        live_gates_ready: false,
        blockers: vec!["LIVE broker is not configured".into()],
        // Representative parity facts matching the CONNECTED claim above
        // (real wiring replaces this with the manager snapshot).
        status_raw: "CONNECTED".to_string(),
        checks_raw: [
            "connection",
            "account",
            "funds",
            "positions",
            "orders",
            "market_data",
        ]
        .into_iter()
        .map(|id| (id.to_string(), "READY".to_string()))
        .collect(),
        configured: true,
        can_login: true,
        can_disconnect: true,
        can_refresh: true,
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Once;

    fn init_backend() {
        static ONCE: Once = Once::new();
        ONCE.call_once(i_slint_backend_testing::init_no_event_loop);
    }

    fn row(model: &slint::ModelRc<BrokerCheckRow>, index: usize) -> BrokerCheckRow {
        model
            .row_data(index)
            .unwrap_or_else(|| panic!("missing row {index}"))
    }

    #[test]
    fn capability_projection_maps_status_kinds() {
        let panel = demo_snapshot();
        let rows = capability_rows(&panel);
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0].id, "orders.market");
        assert_eq!(rows[0].label, "SUPPORTED");
        assert_eq!(rows[0].kind, 0);
        assert_eq!(rows[2].label, "NOT_SUPPORTED");
        assert_eq!(rows[2].kind, 1);
    }

    #[test]
    fn initial_screen_parses_cli_flag() {
        let args = |items: &[&str]| items.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(initial_screen(&[]), ShellScreen::Lab);
        assert_eq!(
            initial_screen(&args(&["--screen", "portfolio"])),
            ShellScreen::Portfolio
        );
        assert_eq!(initial_screen(&args(&["--screen=live"])), ShellScreen::Live);
        assert_eq!(
            initial_screen(&args(&["--screen", "bogus"])),
            ShellScreen::Lab
        );
        assert_eq!(initial_screen(&args(&["--screen"])), ShellScreen::Lab);
        // First known screen wins; unknown values never invent a screen.
        assert_eq!(
            initial_screen(&args(&["--screen", "bogus", "--screen", "research"])),
            ShellScreen::Research
        );
        for (name, screen) in [
            ("chart", ShellScreen::Chart),
            ("lab", ShellScreen::Lab),
            ("research", ShellScreen::Research),
            ("portfolio", ShellScreen::Portfolio),
            ("live", ShellScreen::Live),
            ("broker", ShellScreen::Broker),
            ("data", ShellScreen::Data),
        ] {
            assert_eq!(screen_from_name(name), Some(screen));
        }
        assert_eq!(screen_from_name("PORTFOLIO"), None);
    }

    #[test]
    fn ui_bindings_and_navigation() {
        // Slint's testing backend owns process-global platform/event-loop
        // state: all window-instantiation assertions run in this one test,
        // sequentially.
        init_backend();

        let ui = AppWindow::new().unwrap();
        apply(&ui, &demo_snapshot());
        assert_eq!(ui.get_broker_name(), "Paper");
        assert_eq!(ui.get_broker_id(), "paper");
        assert_eq!(ui.get_env_label(), "PAPER");
        assert_eq!(ui.get_env_kind(), 0);
        assert_eq!(ui.get_health_label(), "CONNECTED");
        assert!(ui.get_health_connected());
        // paper venue is never live-ready, regardless of gate state
        assert!(!ui.get_live_ready());
        assert_eq!(ui.get_status_label(), "CONNECTED");
        assert_eq!(ui.get_conn_label(), "Healthy");
        assert_eq!(ui.get_health_summary(), "✓ HEALTHY");
        let checks = ui.get_check_rows();
        assert_eq!(checks.row_count(), 6);
        assert_eq!(row(&checks, 0).label, "CONNECTION");
        assert_eq!(row(&checks, 0).value, "✓ Ready");
        assert_eq!(ui.get_login_label(), "RE-AUTHENTICATE");
        assert!(ui.get_login_enabled());
        assert_eq!(ui.get_configure_label(), "SETTINGS");
        assert!(ui.get_refresh_visible() && ui.get_refresh_enabled());
        assert!(ui.get_disconnect_visible() && ui.get_disconnect_enabled());
        assert!(ui.get_remove_visible());
        let blockers = ui.get_blockers();
        assert_eq!(blockers.row_count(), 1);
        assert_eq!(
            blockers.row_data(0).unwrap(),
            "LIVE broker is not configured"
        );

        select(&ui, ShellScreen::Broker);
        assert_eq!(ui.get_active_screen(), ShellScreen::Broker);
        assert_eq!(ui.get_screen_title(), "BROKER");
        assert!(!ui.get_screen_pending());
        select(&ui, ShellScreen::Lab);
        assert_eq!(ui.get_active_screen(), ShellScreen::Lab);
        assert_eq!(ui.get_screen_title(), "STRATEGY LAB");
        // Strategy Lab has a native surface now (this migration slice).
        assert!(!ui.get_screen_pending());
        select(&ui, ShellScreen::Broker);
        assert!(!ui.get_screen_pending());
        select(&ui, ShellScreen::Research);
        // Research has a native surface now (this migration slice).
        assert!(!ui.get_screen_pending());
        select(&ui, ShellScreen::Broker);
        assert!(!ui.get_screen_pending());

        // ── Research: Rust view-model drives the screen ──
        let research_state = Rc::new(RefCell::new(research_state::demo_research_state()));
        apply_research(&ui, &research_state.borrow());
        assert_eq!(ui.get_research_strategy_name(), "OBR");
        assert_eq!(ui.get_research_state_label(), "○ NO EXPERIMENT");
        assert!(!ui.get_research_show_results());
        // Bridge unwired: RUN stays inert, creation still works on real input.
        assert!(!ui.get_research_run_enabled());
        assert!(!ui.get_research_run_blocked_reason().is_empty());
        assert_eq!(ui.get_research_strategies().row_count(), 2);
        assert_eq!(ui.get_research_config_groups().row_count(), 5);
        assert_eq!(
            ui.get_research_config_groups()
                .row_data(0)
                .unwrap()
                .fields
                .row_count(),
            2
        );
        // Unexecuted state: the inspector still carries live-form facts.
        assert!(ui.get_research_inspector_rows().row_count() >= 11);
        assert_eq!(ui.get_research_metrics().row_count(), 10);
        assert_eq!(ui.get_research_metrics().row_data(0).unwrap().value, "—");

        wire_research(&ui, research_state.clone());
        ui.invoke_research_hypothesis_edited("opening momentum persists".into());
        assert!(research_state.borrow().hypothesis.contains("momentum"));
        ui.invoke_research_create_pressed();
        assert_eq!(
            research_state.borrow().run,
            research_state::ResearchRun::Draft
        );
        assert_eq!(ui.get_research_state_label(), "○ DRAFT");
        ui.invoke_research_tab_picked(3);
        assert_eq!(ui.get_research_tab(), 3);
        assert_eq!(research_state.borrow().tab, 3);
        ui.invoke_research_config_option("timeframe".into(), "5m".into());
        assert_eq!(research_state.borrow().timeframe_idx, 2);
        ui.invoke_research_config_edited("symbols".into(), "RELIANCE".into());
        assert!(ui.get_research_context_line().contains("RELIANCE"));
        ui.invoke_research_strategy_picked(1);
        assert_eq!(ui.get_research_strategy_name(), "SMA");

        // ── Strategy Lab: Rust view-model drives the screen ──
        let lab_state = Rc::new(RefCell::new(demo_lab_state()));
        apply_lab(&ui, &lab_state.borrow());
        let header = ui.get_lab();
        assert!(header.has_strategy);
        assert_eq!(header.name, "OBR");
        // Selection is reflected immediately — no "no strategy" mismatch.
        assert_eq!(header.state_label, "● READY");
        assert!(!header.show_results);
        assert!(!header.run_enabled); // engine bridge not wired yet: no fake run
        assert_eq!(ui.get_lab_library().row_count(), 2);
        assert!(ui.get_lab_library().row_data(0).unwrap().selected);

        let no_fetch: Rc<dyn Fn(String)> = Rc::new(|_| ());
        let no_run: Rc<dyn Fn(LabRunRequest)> = Rc::new(|_| ());
        wire_lab(&ui, lab_state.clone(), no_fetch, no_run);
        ui.invoke_lab_library_picked(1);
        assert_eq!(ui.get_lab().name, "SMA");
        assert!(ui.get_lab_library().row_data(1).unwrap().selected);
        assert!(!ui.get_lab_library().row_data(0).unwrap().selected);

        ui.invoke_lab_mode_picked(1);
        assert_eq!(ui.get_lab().mode, 1);
        assert_eq!(lab_state.borrow().mode, LabMode::Short);

        ui.invoke_lab_search_changed("ob".into());
        assert_eq!(ui.get_lab_library().row_count(), 1);
        ui.invoke_lab_search_changed("".into());

        // KPI placeholders use the legacy tile vocabulary ("--", 8 tiles —
        // no SORTINO tile ever existed), never fabricated numbers.
        let kpis = ui.get_lab_kpis();
        assert_eq!(kpis.row_count(), 8);
        assert!(kpis.row_data(0).unwrap().emphasized);
        assert_eq!(kpis.row_data(0).unwrap().value, "--");

        wire(&ui);
        ui.invoke_nav_selected(ShellScreen::Research);
        assert_eq!(ui.get_active_screen(), ShellScreen::Research);
        assert_eq!(ui.get_screen_title(), "RESEARCH");
        assert!(!ui.get_screen_pending());
        ui.invoke_nav_selected(ShellScreen::Broker);
        assert_eq!(ui.get_active_screen(), ShellScreen::Broker);
        assert!(!ui.get_screen_pending());

        // Reset Zoom action: UI trigger resets centrally-owned Rust state.
        let zoom = Rc::new(RefCell::new(ChartViewportZoom::default()));
        zoom.borrow_mut().set_level(2.5);
        apply_zoom(&ui, &zoom.borrow());
        assert_eq!(ui.get_chart_zoom_label(), "250%");
        wire_zoom(&ui, zoom.clone());
        ui.invoke_reset_zoom();
        assert!(zoom.borrow().is_default());
        assert_eq!(ui.get_chart_zoom_level(), 1.0);
        assert_eq!(ui.get_chart_zoom_label(), "100%");
        // Resetting an already-default viewport is a no-op, still consistent.
        ui.invoke_reset_zoom();
        assert!(zoom.borrow().is_default());
        assert_eq!(ui.get_chart_zoom_label(), "100%");

        // ── Portfolio: Rust view-model drives the responsive screen ──
        // Unconfigured account: truthful empty states, never zero-filled.
        let portfolio_state = Rc::new(RefCell::new(demo_portfolio_state()));
        apply_portfolio(&ui, &portfolio_state.borrow());
        assert_eq!(ui.get_portfolio_account_title(), "ACCOUNT NOT CONFIGURED");
        assert_eq!(ui.get_portfolio_kpis_primary().row_count(), 4);
        assert_eq!(
            ui.get_portfolio_kpis_primary().row_data(0).unwrap().value,
            "Unavailable"
        );
        assert!(!ui.get_portfolio_perf_has_data());
        assert!(!ui.get_portfolio_has_positions());
        assert_eq!(
            ui.get_portfolio_positions_empty_title(),
            "ACCOUNT NOT CONFIGURED"
        );
        assert!(!ui.get_portfolio_alloc_has_data());
        assert_eq!(ui.get_portfolio_active_empty_title(), "ORDERS UNAVAILABLE");
        assert!(!ui.get_portfolio_active_has_rows());
        assert_eq!(ui.get_portfolio_health_label(), "NOT CONFIGURED");
        assert!(!ui.get_portfolio_risk_has_data());
        assert!(ui.get_portfolio_gates().row_count() == 4);

        // Portfolio has a native surface now (this migration slice).
        select(&ui, ShellScreen::Portfolio);
        assert_eq!(ui.get_active_screen(), ShellScreen::Portfolio);
        assert_eq!(ui.get_screen_title(), "PORTFOLIO");
        assert!(!ui.get_screen_pending());

        // Funded account: hierarchy renders in order, one projection pass.
        portfolio_state.replace(demo_portfolio_configured());
        apply_portfolio(&ui, &portfolio_state.borrow());
        assert_eq!(ui.get_portfolio_account_title(), "ACCOUNT CONNECTED");
        assert!(ui
            .get_portfolio_kpis_primary()
            .row_data(0)
            .unwrap()
            .value
            .contains("100,000"));
        assert!(ui.get_portfolio_perf_has_data());
        assert_eq!(ui.get_portfolio_perf_winrate(), "100.0%");
        assert!(ui.get_portfolio_has_positions());
        assert_eq!(ui.get_portfolio_positions().row_count(), 1);
        assert_eq!(
            ui.get_portfolio_positions().row_data(0).unwrap().alloc,
            "1.1%"
        );
        assert_eq!(ui.get_portfolio_positions_count(), "1 open position");
        assert!(ui.get_portfolio_alloc_has_data());
        assert_eq!(
            ui.get_portfolio_allocation().row_data(0).unwrap().symbol,
            "TEST"
        );
        assert_eq!(ui.get_portfolio_health_label(), "HEALTHY");
        assert!(ui.get_portfolio_risk_has_data());
        assert!(ui.get_portfolio_active_has_rows());
        assert_eq!(ui.get_portfolio_orders().row_count(), 1);

        // Interactions mutate Rust state centrally and re-project.
        wire_portfolio(&ui, portfolio_state.clone());
        ui.invoke_portfolio_orders_tab_picked(1);
        assert_eq!(ui.get_portfolio_orders_tab(), 1);
        assert_eq!(portfolio_state.borrow().orders_tab, 1);
        assert!(ui.get_portfolio_active_has_rows());
        ui.invoke_portfolio_orders_tab_picked(0);
        assert_eq!(ui.get_portfolio_orders_tab(), 0);
        ui.invoke_portfolio_position_picked(0);
        assert_eq!(ui.get_portfolio_selected_position(), 0);
        assert_eq!(ui.get_portfolio_detail_symbol(), "TEST");
        assert_eq!(ui.get_portfolio_detail_qty(), "10.00");
        ui.invoke_portfolio_refresh_requested();
        assert_eq!(ui.get_portfolio_detail_symbol(), "TEST");
        // Out-of-range selection is a no-op, never a broken layout.
        ui.invoke_portfolio_position_picked(99);
        assert_eq!(ui.get_portfolio_selected_position(), 0);

        // ── Research breakpoints derive from the content width ──
        ui.window().set_size(slint::PhysicalSize::new(1920, 1080));
        assert!(ui.get_research_wide());
        assert!(ui.get_research_medium());
        // Without a 104px vertical navigation rail, content width equals
        // window width; 1176px is just below the 1180px wide threshold.
        ui.window().set_size(slint::PhysicalSize::new(1176, 800));
        assert!(!ui.get_research_wide());
        assert!(ui.get_research_medium());
        ui.window().set_size(slint::PhysicalSize::new(1024, 640));
        assert!(!ui.get_research_wide());
        assert!(ui.get_research_medium());

        // ── Research viewport matrix (same thread, sequentially: the
        // testing backend owns process-global platform state, so all
        // window assertions live in this one test) ──
        use crate::research_harness_ui::{
            ResearchHarness, ResearchMetric, ResearchSignalRow, ResearchTradeRow,
        };
        let harness = ResearchHarness::new().unwrap();
        // Populated completed shape — content must survive every recomposition.
        harness.set_show_results(true);
        harness.set_state_label("✓ COMPLETED".into());
        harness.set_metrics(
            Rc::new(slint::VecModel::from(vec![
                ResearchMetric {
                    label: "NET P&L".into(),
                    value: "+₹12.00".into(),
                    tone: 2,
                    emphasized: true,
                },
                ResearchMetric {
                    label: "TRADES".into(),
                    value: "4".into(),
                    tone: 1,
                    emphasized: false,
                },
                ResearchMetric {
                    label: "WIN RATE".into(),
                    value: "75.0%".into(),
                    tone: 1,
                    emphasized: false,
                },
            ]))
            .into(),
        );
        harness.set_signals(
            Rc::new(slint::VecModel::from(vec![ResearchSignalRow {
                time: "2024-01-02 09:15:00".into(),
                symbol: "AAA".into(),
                tf: "5m".into(),
                side: "BUY".into(),
                price: "100.00".into(),
                event: "BUY".into(),
                strategy: "OBR".into(),
                exp: "EXP-T1".into(),
            }]))
            .into(),
        );
        harness.set_trades(
            Rc::new(slint::VecModel::from(vec![ResearchTradeRow {
                no: "1".into(),
                entry: "2024-01-02 09:15:00".into(),
                exit: "2024-01-02 10:15:00".into(),
                side: "LONG".into(),
                qty: "10.00".into(),
                pnl: "+120.50".into(),
                reason: "SIGNAL".into(),
                hold: "12".into(),
                pnl_tone: 2,
            }]))
            .into(),
        );
        harness.set_tab(0);
        // Harness widths are CONTENT widths (the shell subtracts the 104px
        // rail first); labels name the corresponding window size.
        // (content-width, height, layout, form-cols, metric-cols, inspector)
        let matrix = [
            (1816, 1080, "wide", 3, 5, true),             // 1920x1080
            (1496, 900, "wide", 3, 5, true),              // 1600x900
            (1336, 900, "wide", 3, 5, true),              // 1440x900
            (1262, 768, "wide", 3, 5, true),              // 1366x768
            (1176, 720, "medium-navigator", 2, 3, false), // 1280x720
            (996, 700, "medium-navigator", 2, 3, false),  // ~1100 window
            (796, 700, "medium-navigator", 2, 3, false),
            (596, 600, "narrow-workspace", 1, 2, false),
            (396, 700, "narrow-workspace", 1, 2, false),
            (256, 640, "narrow-workspace", 1, 2, false), // 360 window
        ];
        for (width, height, layout, form, metric, inspector) in matrix {
            harness.set_wide(width >= 1180);
            harness.set_medium(width >= 760);
            harness.set_inspector_open(false);
            harness.set_narrow_view(0);
            harness
                .window()
                .set_size(slint::PhysicalSize::new(width, height));
            assert_eq!(harness.get_layout_id().as_str(), layout, "{width}x{height}");
            assert_eq!(harness.get_form_cols(), form, "{width}x{height}");
            assert_eq!(harness.get_metric_cols(), metric, "{width}x{height}");
            assert_eq!(harness.get_inspector_shown(), inspector, "{width}x{height}");
            // Populated content survives every recomposition, unclipped by
            // construction (scroll regions own overflow, never fixed heights).
            assert_eq!(harness.get_metrics().row_count(), 3);
            assert_eq!(harness.get_signals().row_count(), 1);
            assert_eq!(harness.get_trades().row_count(), 1);
        }
        // Medium drawer and narrow tabs recompose on demand — never squeeze.
        harness.set_wide(false);
        harness.set_medium(true);
        harness.set_inspector_open(true);
        assert!(harness.get_inspector_shown());
        harness.set_medium(false);
        harness.set_narrow_view(2);
        assert_eq!(harness.get_layout_id().as_str(), "narrow-inspector");
        assert!(harness.get_inspector_shown());
        harness.set_narrow_view(1);
        assert_eq!(harness.get_layout_id().as_str(), "narrow-navigator");
        assert!(!harness.get_inspector_shown());

        // ── LIVE: backend facts drive every displayed verdict ──
        // Production tier derivation (LiveTiers at window level): with
        // top navigation (0 px rail), content width equals window width.
        ui.window().set_size(slint::PhysicalSize::new(1920, 1080));
        assert!(ui.get_live_dock_inspector());
        assert!(ui.get_live_bar_extended());
        assert!(ui.get_live_bar_secondary());
        assert!(ui.get_live_blotter_side());
        assert!(ui.get_live_tall_inspector());
        assert!((ui.get_live_inspector_w() - 400.0).abs() < 1.0); // capped
        ui.window().set_size(slint::PhysicalSize::new(1176, 720));
        assert!(ui.get_live_dock_inspector()); // 1176 content ≥ 800 floor
        assert!(ui.get_live_bar_extended());
        assert!(!ui.get_live_blotter_side()); // 1176 < 1240
        assert!(!ui.get_live_tall_inspector()); // 720 − 50 − 24 < 720
        ui.window().set_size(slint::PhysicalSize::new(1024, 640));
        assert!(ui.get_live_dock_inspector()); // 920 ≥ 800
        assert!(!ui.get_live_bar_secondary()); // 920 < 1040 — pills fold away
        assert!(!ui.get_live_bar_extended());
        assert!(ui.get_live_bar_core()); // HALT spine + mode stay
        assert!(
            (ui.get_live_inspector_w() - 340.0).abs() < 1.0,
            "inspector holds its usable floor"
        );
        let live_state = Rc::new(RefCell::new(demo_live_state()));
        apply_live(&ui, &live_state.borrow());
        select(&ui, ShellScreen::Live);
        // LIVE has a native surface now (this migration slice).
        assert!(!ui.get_screen_pending());
        assert_eq!(ui.get_active_screen(), ShellScreen::Live);
        let bar = ui.get_live_bar();
        assert_eq!(bar.exec_label, "● ENABLED");
        assert_eq!(bar.broker_label, "Broker: NOT CONFIGURED");
        assert_eq!(bar.conn_label, "Connection: N/A");
        assert!(!bar.halted);
        // Halt follows real state: an idle session cannot be halted.
        assert!(!bar.halt_enabled);
        let setup = ui.get_live_setup();
        assert_eq!(setup.session_label, "● STOPPED");
        assert!(!setup.can_start); // bridge not wired: no fake run affordance
        assert!(!setup.can_stop);
        assert!(!setup.can_arm); // venue gates fail: no fake readiness
        let gates = ui.get_live_gates();
        assert_eq!(gates.row_count(), 10);
        assert_eq!(
            gates.row_data(0).unwrap().reason,
            "live gate off: LIVE_TRADING_ENABLED; live gate off: BROKER_LIVE_ENABLED"
        );
        // Start blockers are visible lines, not hidden behind a dead button.
        let blockers = ui.get_live_blockers();
        assert!(blockers.row_count() >= 3);
        // Empty tables stay empty — never invented rows or zero-filled P&L.
        assert_eq!(ui.get_live_positions().row_count(), 0);
        assert_eq!(ui.get_live_orders().row_count(), 0);
        assert!(ui.get_live_stats().row_data(0).unwrap().value == "N/A");
        assert_eq!(ui.get_live_market().state_label, "NO DATA");

        wire_live(&ui, live_state.clone());
        // Watchlist filter keeps real indices; toggling selects the store row.
        ui.invoke_live_symbol_filter_changed("reliance".into());
        let symbols = ui.get_live_symbols();
        assert_eq!(symbols.row_count(), 1);
        let reliance = symbols.row_data(0).unwrap();
        assert_eq!(reliance.name, "RELIANCE");
        ui.invoke_live_symbol_toggled(reliance.real_index);
        assert!(ui.get_live_symbols().row_data(0).unwrap().checked);
        assert_eq!(live_state.borrow().checked_symbols(), vec!["RELIANCE"]);
        ui.invoke_live_symbol_filter_changed("".into());

        // LIVE mode intent is fail-closed while venue gates block.
        ui.invoke_live_mode_picked(2);
        assert_eq!(live_state.borrow().mode, live::ExecMode::Paper);
        assert!(ui.get_live_bar().note.contains("LIVE refused"));

        // Quantity edits validate centrally; garbage keeps the old value.
        ui.invoke_live_quantity_edited("25".into());
        assert_eq!(live_state.borrow().quantity, 25.0);
        ui.invoke_live_quantity_edited("abc".into());
        assert_eq!(live_state.borrow().quantity, 25.0);
        assert!(ui.get_live_bar().note.contains("invalid quantity"));

        // CONFIGURE BROKER is navigation intent — shell switches screens.
        ui.invoke_live_configure_broker();
        assert_eq!(ui.get_active_screen(), ShellScreen::Broker);
        select(&ui, ShellScreen::Live);
        use crate::live_harness_ui::{LiveBar, LiveHarness, LiveMarket, LiveSetup};
        init_backend();
        let harness = LiveHarness::new().unwrap();
        // Representative NOT-CONFIGURED desktop (empty tables): the honest
        // idle shape — content must survive every recomposition tier.
        harness.set_bar(LiveBar {
            mode: 0,
            broker_label: "Broker: NOT CONFIGURED".into(),
            broker_tone: 0,
            conn_label: "Connection: N/A".into(),
            conn_tone: 0,
            strategy_label: "Strategy: —".into(),
            strategy_tone: 0,
            risk_label: "Risk: READY".into(),
            risk_tone: 1,
            recon_label: "Reconciliation: NOT CONFIGURED".into(),
            recon_tone: 0,
            exec_label: "● ENABLED".into(),
            exec_tone: 1,
            halted: false,
            halt_enabled: false,
            inspector_open: false,
            note: String::default().into(),
            note_tone: 0,
        });
        harness.set_setup(LiveSetup {
            quantity: "0.00".into(),
            session_label: "● STOPPED".into(),
            session_tone: 0,
            can_start: false,
            can_stop: false,
            can_arm: false,
            needs_live_confirm: false,
            confirmed_live: false,
            symbol_total: "527 in store · 0 selected".into(),
        });
        harness.set_market(LiveMarket {
            has_data: false,
            state_label: "NO DATA".into(),
            tone: 0,
            title: "NO MARKET DATA".into(),
            detail: "Symbol: — · Timeframe: 15m".into(),
            header: "— 15m".into(),
        });
        harness.set_inspector_open(true);

        // (width, height, docked?, blotters-side-by-side?, extended bar?)
        // The harness hosts the screen WITHOUT the shell rail, so these
        // widths are raw screen widths; in production the 104 px rail
        // subtracts from the window width before the same decisions apply.
        let matrix: [(u32, u32, bool, bool, bool); 15] = [
            (1920, 1080, true, true, true),
            (1600, 900, true, true, true),
            (1440, 900, true, true, true),
            (1366, 768, true, true, true),
            (1280, 720, true, true, true),
            (1280, 1024, true, true, true),
            (1150, 800, true, false, true),
            (1024, 700, true, false, false),
            (980, 680, true, false, false),
            (900, 680, true, false, false),
            (800, 640, true, false, false),
            (799, 640, false, false, false),
            (600, 700, false, false, false),
            (480, 640, false, false, false),
            (360, 640, false, false, false),
        ];
        for (width, height, docked, side_by_side, extended) in matrix {
            harness
                .window()
                .set_size(slint::PhysicalSize::new(width, height));
            // Slint windows have a platform minimum; only assert sizes the
            // platform honored.
            let applied = harness.window().size();
            if applied.width < width {
                continue;
            }
            let avail = applied.width as f64;
            // The row's expected tiers must agree with the production
            // thresholds (content minima — pinned here as the decision
            // table itself).
            assert_eq!(docked, avail >= 800.0, "{width}x{height} dock floor");
            assert_eq!(side_by_side, avail >= 1240.0, "{width}x{height} blotters");
            assert_eq!(extended, avail >= 1120.0, "{width}x{height} extended bar");
            // Drive the tiers exactly as app.slint's LiveTiers computes them.
            let inspector_w = (avail * 0.32).clamp(340.0, 400.0);
            harness.set_tier_dock(docked);
            harness.set_tier_core(avail >= 760.0);
            harness.set_tier_secondary(avail >= 1040.0);
            harness.set_tier_extended(extended);
            harness.set_tier_blotter(side_by_side);
            harness.set_tier_tall(applied.height as f64 >= 720.0);
            harness.set_tier_inspector_w(inspector_w as f32);
            // THE invariant: whenever the inspector is shown docked, the
            // workspace keeps its 440 px floor (padding-left math at real
            // applied size) — clipping is structurally impossible.
            assert!(harness.get_show_inspector(), "{width}x{height}");
            assert_eq!(harness.get_drawer_open(), !docked, "{width}x{height}");
            assert!(harness.get_workspace_fits(), "{width}x{height}");
            if docked {
                assert!(
                    avail - inspector_w - 1.0 >= 440.0,
                    "{width}x{height} docked workspace floor"
                );
            }
            // No-data recomposition: the chart region stays COMPACT.
            assert!(
                harness.get_chart_h() <= 160.0,
                "{width}x{height} chart must not void"
            );
        }

        // Collapse, never squeeze: closing the inspector returns the full
        // width to the workspace at ANY tier.
        harness.set_inspector_open(false);
        assert!(!harness.get_show_inspector());
        assert!(!harness.get_drawer_open());
        assert!(harness.get_workspace_fits());
        harness.set_inspector_open(true);
        assert!(harness.get_show_inspector());

        // With real bars the chart expands into the remaining height (§8) —
        // the same screen, data-driven, no mode switch.
        harness.set_market(LiveMarket {
            has_data: true,
            state_label: "READY".into(),
            tone: 1,
            title: "RELIANCE · 15m".into(),
            detail: "500 bars · source: market store (SQLite)".into(),
            header: "RELIANCE 15m".into(),
        });
        harness
            .window()
            .set_size(slint::PhysicalSize::new(1600, 900));
        assert!(
            harness.get_chart_h() > 300.0,
            "chart owns remaining height with data"
        );
        harness
            .window()
            .set_size(slint::PhysicalSize::new(1600, 620));
        assert!(
            harness.get_chart_h() >= 220.0,
            "chart keeps its usable floor; page scrolls"
        );

        select(&ui, ShellScreen::Broker);
        assert!(!ui.get_screen_pending());
    }

    #[test]
    fn lab_run_request_gathers_backend_echoes() {
        let mut state = demo_lab_state();
        // No selection → honestly no request (RUN stays disabled).
        state.selected = None;
        assert!(LabRunRequest::gather(&state).is_none());
        // Selection + backend echoes → a complete request.
        state.selected = Some(1);
        state.engine_wired = true;
        state.universe_symbols = vec!["RELIANCE".into(), "TCS".into()];
        state.universe_selected = vec!["TCS".into()];
        state.timeframes = vec!["15m".into(), "1h".into()];
        state.timeframe_index = 1;
        state.cfg_dates_start = "2026-01-01".into();
        state.cfg_dates_end = "2026-06-10".into();
        state.cfg_capital = "₹10,00,000".into();
        let request = LabRunRequest::gather(&state).expect("request");
        assert_eq!(request.strategy, "SMA");
        assert_eq!(request.symbols, vec!["TCS".to_string()]);
        assert_eq!(request.timeframe, "1h");
        assert_eq!(request.start, "2026-01-01");
        assert_eq!(request.end, "2026-06-10");
        assert_eq!(request.capital, 1000000.0);
        assert_eq!(request.mode, "buy");
        // Unknown symbols in the echo can never be queued.
        state.universe_selected = vec!["BOGUS".into()];
        state.cfg_universe_csv = "RELIANCE, BOGUS".into();
        let request = LabRunRequest::gather(&state).expect("request");
        assert_eq!(request.symbols, vec!["RELIANCE".to_string()]);
    }

    #[test]
    fn lab_capital_parses_display_echoes() {
        assert_eq!(parse_capital("₹10,00,000"), Some(1000000.0));
        assert_eq!(parse_capital("1000000"), Some(1000000.0));
        assert_eq!(parse_capital(""), None);
        assert_eq!(parse_capital("—"), None);
    }
}
