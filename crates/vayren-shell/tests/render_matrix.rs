//! Pixel renders of the Research screen (headless software renderer).
//!
//! Renders the harness at the required viewport matrix and saves BMPs for
//! human inspection. Also asserts structural invariants that pixels prove:
//! output size matches, content actually painted (not blank), background
//! matches the theme. Run: `cargo test -p vayren-shell --test render_matrix`.

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, PremultipliedRgbaColor, RepaintBufferType, TargetPixel,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::ComponentHandle;
use std::rc::Rc;

/// Plain RGB pixel for screenshots (same blend math as the renderer's own
/// Rgb8Pixel, which is not re-exported through the `slint` facade).
#[derive(Copy, Clone, Default, PartialEq, Eq)]
struct Rgb8Pixel {
    r: u8,
    g: u8,
    b: u8,
}

impl TargetPixel for Rgb8Pixel {
    fn blend(&mut self, color: PremultipliedRgbaColor) {
        let a = (u8::MAX - color.alpha) as u16;
        self.r = (self.r as u16 * a / 255) as u8 + color.red;
        self.g = (self.g as u16 * a / 255) as u8 + color.green;
        self.b = (self.b as u16 * a / 255) as u8 + color.blue;
    }

    fn from_rgb(r: u8, g: u8, b: u8) -> Self {
        Rgb8Pixel { r, g, b }
    }

    fn background() -> Self {
        Rgb8Pixel::from_rgb(0, 0, 0)
    }
}
use vayren_shell::research_harness_ui::{
    ResearchConfigGroup, ResearchEvidenceDim, ResearchEvidenceWhy, ResearchField, ResearchHarness,
    ResearchKvGroup, ResearchMetric, ResearchSignalRow, ResearchTradeRow,
};

struct SoftwarePlatform {
    windows: Rc<std::cell::RefCell<Vec<Rc<MinimalSoftwareWindow>>>>,
}

impl Platform for SoftwarePlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        // One adapter per window: sharing a single adapter across component
        // roots mashes their trees together at paint time.
        let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        self.windows.borrow_mut().push(window.clone());
        Ok(window)
    }
}

fn save_bmp(path: &std::path::Path, pixels: &[Rgb8Pixel], width: u32, height: u32) {
    let stride = ((width * 3 + 3) / 4) * 4;
    let mut data = vec![0u8; (stride * height) as usize];
    for y in 0..height {
        for x in 0..width {
            let p = &pixels[(y * width + x) as usize];
            let out = ((height - 1 - y) * stride + x * 3) as usize;
            data[out] = p.b;
            data[out + 1] = p.g;
            data[out + 2] = p.r;
        }
    }
    let file_size = 54 + data.len() as u32;
    let mut header = vec![0u8; 54];
    header[0] = b'B';
    header[1] = b'M';
    header[2..6].copy_from_slice(&file_size.to_le_bytes());
    header[10] = 54;
    header[14] = 40;
    header[18..22].copy_from_slice(&width.to_le_bytes());
    header[22..26].copy_from_slice(&height.to_le_bytes());
    header[26] = 1;
    header[28] = 24;
    let mut file = std::fs::File::create(path).unwrap();
    use std::io::Write;
    file.write_all(&header).unwrap();
    file.write_all(&data).unwrap();
}

fn populate_completed(harness: &ResearchHarness) {
    populate_chrome(harness);
    harness.set_show_results(true);
    harness.set_state_label("✓ COMPLETED".into());
    harness.set_has_strategy(true);
    harness.set_strategy_name("OBR".into());
    harness.set_context_line("OBR · RELIANCE, TCS · 5m · 2024-01-01 → 2024-03-31".into());
    harness.set_metrics(
        Rc::new(slint::VecModel::from(vec![
            research_metric("NET P&L", "-₹32,277.93", 3, true),
            research_metric("TRADES", "37", 1, false),
            research_metric("SIGNALS", "82", 1, false),
            research_metric("WIN RATE", "37.8%", 1, false),
            research_metric("PROFIT FACTOR", "0.72", 3, false),
            research_metric("EXPECTANCY", "-872.38", 3, false),
            research_metric("MAX DD", "12.40%", 0, false),
            research_metric("SHARPE", "-0.42", 3, false),
            research_metric("SORTINO", "—", 0, false),
            research_metric("RETURN", "-3.23%", 3, false),
        ]))
        .into(),
    );
    harness.set_signal_count_line("82 SIGNALS".into());
    harness.set_trade_count_line("37 TRADES".into());
    harness.set_signals(
        Rc::new(slint::VecModel::from(vec![
            research_signal(
                "2024-01-02 09:15:00",
                "RELIANCE",
                "BUY",
                "2451.10",
                "EXP-03774B",
            ),
            research_signal("2024-01-02 09:20:00", "TCS", "BUY", "3802.55", "EXP-03774B"),
        ]))
        .into(),
    );
    harness.set_trades(
        Rc::new(slint::VecModel::from(vec![research_trade(
            "1",
            "2024-01-02 09:15:00",
            "2024-01-02 10:15:00",
            "-1,240.50",
            "12",
        )]))
        .into(),
    );
    harness.set_robustness(Rc::new(slint::VecModel::from(vec![research_robust()])).into());
    harness.set_validation_status("FAIL".into());
    harness.set_validation_summary("WEAK; OOS WARNING".into());
    // Evidence-first layer: honest verdict + why + supporting dimensions.
    harness.set_evidence_verdict("FAIL".into());
    harness.set_evidence_tone(3);
    harness.set_evidence_grade("WEAK".into());
    harness.set_evidence_why(
        Rc::new(slint::VecModel::from(vec![
            research_why("FAIL", "negative expectancy"),
            research_why("WARN", "Low trade count (<10) — insufficient evidence"),
            research_why("NOTE", "in-sample only"),
        ]))
        .into(),
    );
    harness.set_evidence_dims(
        Rc::new(slint::VecModel::from(vec![
            research_dim("OUT-OF-SAMPLE", "WARNING", 2, "exp 12.40 → -3.10"),
            research_dim("CPCV", "PASS", 1, "10 paths · 70% above"),
            research_dim("PBO", "PASS", 1, "PBO 0.12"),
            research_dim("DSR", "WARNING", 2, "DSR 0.40"),
            research_dim("COST STRESS", "FAIL", 3, "4 scenarios"),
            research_dim("TEMPORAL", "WARNING", 2, "best 1.20 · worst -0.30"),
            research_dim("LEAKAGE", "PASS", 1, "PASS"),
            research_dim("WALK-FORWARD", "MIXED", 0, "6 folds · exp -872.38"),
        ]))
        .into(),
    );
    harness.set_stats_rows(
        Rc::new(slint::VecModel::from(vec![
            research_kv("EXPECTANCY", "−₹872.38"),
            research_kv("95% CI", "-1402 → -342"),
            research_kv("p-VALUE", "0.002"),
        ]))
        .into(),
    );
    harness.set_montecarlo_rows(
        Rc::new(slint::VecModel::from(vec![
            research_kv("P5 P&L", "−₹94,120.00"),
            research_kv("MEDIAN P&L", "−₹31,005.50"),
            research_kv("PROB. PROFIT", "12%"),
        ]))
        .into(),
    );
    harness.set_benchmark_rows(
        Rc::new(slint::VecModel::from(vec![
            research_kv("STRATEGY", "-3.23%"),
            research_kv("BUY & HOLD", "+6.10%"),
            research_kv("EXCESS", "-9.33%"),
        ]))
        .into(),
    );
    harness.set_inspector_groups(
        Rc::new(slint::VecModel::from(vec![
            research_group_kv(
                "IDENTITY",
                vec![
                    ("EXPERIMENT", "EXP-03774B"),
                    ("STRATEGY", "OBR"),
                    ("VERSION", "d258c55b"),
                    ("STATUS", "COMPLETED"),
                ],
            ),
            research_group_kv(
                "EXECUTION",
                vec![
                    ("TIMEFRAME", "5m"),
                    ("SIDE", "LONG"),
                    ("EXECUTED", "2026-09-13T10:00:00"),
                ],
            ),
            research_group_kv(
                "DATA",
                vec![
                    ("PERIOD", "2024-01-01 → 2024-03-31"),
                    ("DATASET", "a1b2c3d4e5f6"),
                ],
            ),
            research_group_kv(
                "EVIDENCE",
                vec![("RESULT", "f39d958be429"), ("CONFIG", "d4d6b182bf71")],
            ),
        ]))
        .into(),
    );
    harness.set_conclusion("Performance is unstable across tested dimensions.".into());
    harness.set_fingerprint_line("result f39d958be429 · config d4d6b182bf71".into());
    harness.set_tab(0);
    harness.set_log_status("Experiment completed — 37 trades, 82 signals.".into());
    harness.set_log_expanded(false);
}

fn research_row(
    name: &str,
    description: &str,
    selected: bool,
) -> vayren_shell::research_harness_ui::ResearchStrategyRow {
    vayren_shell::research_harness_ui::ResearchStrategyRow {
        name: name.into(),
        description: description.into(),
        version: "1.0".into(),
        selected,
    }
}

fn research_field(
    key: &str,
    label: &str,
    value: &str,
) -> vayren_shell::research_harness_ui::ResearchField {
    vayren_shell::research_harness_ui::ResearchField {
        key: key.into(),
        label: label.into(),
        value: value.into(),
        kind: 0,
        options: std::rc::Rc::new(slint::VecModel::from(Vec::<slint::SharedString>::new())).into(),
        selected: 0,
    }
}

fn research_metric(label: &str, value: &str, tone: i32, emphasized: bool) -> ResearchMetric {
    ResearchMetric {
        label: label.into(),
        value: value.into(),
        tone,
        emphasized,
    }
}

#[allow(clippy::too_many_arguments)]
fn research_signal(
    time: &str,
    symbol: &str,
    side: &str,
    price: &str,
    exp: &str,
) -> ResearchSignalRow {
    ResearchSignalRow {
        time: time.into(),
        symbol: symbol.into(),
        tf: "5m".into(),
        side: side.into(),
        price: price.into(),
        event: side.into(),
        strategy: "OBR".into(),
        exp: exp.into(),
    }
}

fn research_trade(no: &str, entry: &str, exit: &str, pnl: &str, hold: &str) -> ResearchTradeRow {
    ResearchTradeRow {
        no: no.into(),
        entry: entry.into(),
        exit: exit.into(),
        side: "LONG".into(),
        qty: "10.00".into(),
        pnl: pnl.into(),
        reason: "SIGNAL".into(),
        hold: hold.into(),
        pnl_tone: if pnl.trim_start().starts_with('+') {
            2
        } else {
            3
        },
    }
}

fn research_robust() -> vayren_shell::research_harness_ui::ResearchRobustRow {
    vayren_shell::research_harness_ui::ResearchRobustRow {
        test: "parameter_sensitivity".into(),
        input: "refIndex=3.0".into(),
        stability: "WARNING".into(),
        stability_tone: 2,
        evidence: "exp 12.40 → 11.90".into(),
    }
}

fn research_select(key: &str, label: &str, options: &[&str], selected: i32) -> ResearchField {
    ResearchField {
        key: key.into(),
        label: label.into(),
        value: "".into(),
        kind: 1,
        options: Rc::new(slint::VecModel::from(
            options
                .iter()
                .map(|o| slint::SharedString::from(*o))
                .collect::<Vec<_>>(),
        ))
        .into(),
        selected,
    }
}

fn research_group(title: &str, hint: &str, fields: Vec<ResearchField>) -> ResearchConfigGroup {
    ResearchConfigGroup {
        title: title.into(),
        hint: hint.into(),
        fields: Rc::new(slint::VecModel::from(fields)).into(),
    }
}

fn research_why(kind: &str, text: &str) -> ResearchEvidenceWhy {
    ResearchEvidenceWhy {
        kind: kind.into(),
        text: text.into(),
    }
}

fn research_dim(name: &str, status: &str, tone: i32, detail: &str) -> ResearchEvidenceDim {
    ResearchEvidenceDim {
        name: name.into(),
        status: status.into(),
        tone,
        detail: detail.into(),
    }
}

fn research_group_kv(title: &str, rows: Vec<(&str, &str)>) -> ResearchKvGroup {
    ResearchKvGroup {
        title: title.into(),
        rows: Rc::new(slint::VecModel::from(
            rows.into_iter()
                .map(|(l, v)| research_kv(l, v))
                .collect::<Vec<_>>(),
        ))
        .into(),
    }
}

fn populate_chrome(harness: &ResearchHarness) {
    harness.set_strategies(
        Rc::new(slint::VecModel::from(vec![
            research_row("OBR", "Opening Breakout Strategy", true),
            research_row("SMA", "SMA crossover", false),
        ]))
        .into(),
    );
    harness.set_strategy_count("2 total".into());
    harness.set_experiments(
        Rc::new(slint::VecModel::from(vec![
            vayren_shell::research_harness_ui::ResearchExperimentRow {
                id: "EXP-03774B".into(),
                strategy: "OBR".into(),
                status: "✓ COMPLETED".into(),
                status_tone: 1,
                selected: true,
            },
        ]))
        .into(),
    );
    harness.set_experiment_count("1 total".into());
    harness.set_config_groups(
        Rc::new(slint::VecModel::from(vec![
            research_group(
                "DATASET",
                "Universe defines the symbol pool",
                vec![
                    research_field("universe", "UNIVERSE", "NIFTY 500"),
                    research_field("symbols", "SYMBOLS", "RELIANCE, TCS"),
                ],
            ),
            research_group(
                "PERIOD",
                "Backtest window",
                vec![
                    research_field("start", "START", "2024-01-01"),
                    research_field("end", "END", "2024-03-31"),
                ],
            ),
            research_group(
                "CAPITAL & COSTS",
                "Sizing and execution frictions",
                vec![
                    research_field("capital", "CAPITAL", "1000000"),
                    research_field("slip", "SLIPPAGE %", "0.02"),
                    research_field("comm", "COMMISSION %", "0.03"),
                ],
            ),
            research_group(
                "EXECUTION",
                "Bar granularity and direction filter",
                vec![
                    research_select("timeframe", "TIMEFRAME", &["1m", "5m", "15m"], 1),
                    research_select("side", "SIDE", &["LONG", "SHORT", "BOTH"], 0),
                ],
            ),
            research_group(
                "PARAMETERS",
                "Strategy inputs and expected direction",
                vec![
                    research_field("params", "PARAMETERS", "refIndex=3"),
                    research_select(
                        "direction",
                        "EXPECTED DIRECTION",
                        &["ANY", "LONG", "SHORT"],
                        1,
                    ),
                ],
            ),
        ]))
        .into(),
    );
    harness.set_hypothesis("OBR opening momentum persists intraday.".into());
    harness.set_question("Does OBR work in the opening session?".into());
    harness.set_effect("Positive expectancy after costs.".into());
    harness.set_inspector_rows(
        Rc::new(slint::VecModel::from(vec![
            research_kv("EXPERIMENT", "EXP-03774B"),
            research_kv("STRATEGY", "OBR"),
            research_kv("VERSION", "d258c55b"),
            research_kv("STATUS", "COMPLETED"),
            research_kv("TIMEFRAME", "5m"),
            research_kv("PERIOD", "2024-01-01 → 2024-03-31"),
            research_kv("SIDE", "LONG"),
            research_kv("EXECUTED", "2026-09-13T10:00:00"),
            research_kv("DATASET", "a1b2c3d4e5f6"),
            research_kv("ENGINE", "9f8e7d6c5b4a"),
            research_kv("VALIDATION", "FAIL"),
        ]))
        .into(),
    );
    harness.set_conclusion("Performance is unstable across tested dimensions.".into());
    harness.set_fingerprint_line("result f39d958be429 · config d4d6b182bf71".into());
    harness.set_validation_status("FAIL".into());
    harness.set_validation_summary("WEAK; OOS WARNING".into());
    harness.set_validation_rows(
        Rc::new(slint::VecModel::from(vec![
            research_kv("OUT-OF-SAMPLE", "WARNING"),
            research_kv("CPCV", "10 paths"),
            research_kv("WALK-FORWARD", "MIXED"),
            research_kv("NOTE", "in-sample only"),
        ]))
        .into(),
    );
    harness.set_quality_rows(
        Rc::new(slint::VecModel::from(vec![
            research_kv("SYMBOLS TRADED", "2"),
            research_kv("BARS", "3064"),
            research_kv("MISSING", "CHECKED — none"),
        ]))
        .into(),
    );
    harness.set_report_sections(
        Rc::new(slint::VecModel::from(vec![
            research_kv("QUESTION", "Does OBR work in the opening session?"),
            research_kv(
                "METHODOLOGY",
                "Canonical strategy logic executed over canonical market data.",
            ),
            research_kv("LIMIT", "Sequential per-symbol execution takes minutes."),
        ]))
        .into(),
    );
    harness.set_compare_rows(
        Rc::new(slint::VecModel::from(vec![
            vayren_shell::research_harness_ui::ResearchCompareRow {
                exp: "EXP-03774B".into(),
                strategy: "OBR".into(),
                status: "COMPLETED".into(),
                detail: "net -32277.93".into(),
            },
        ]))
        .into(),
    );
    harness.set_compare_verdict("Only one experiment — save another to compare.".into());
}

fn research_kv(label: &str, value: &str) -> vayren_shell::research_harness_ui::ResearchKv {
    vayren_shell::research_harness_ui::ResearchKv {
        label: label.into(),
        value: value.into(),
    }
}

/// Render one state+size to BMP. Panics on blank output (nothing painted).
/// `window_idx` selects the adapter bound to `harness` (creation order).
fn render(
    platform: &Rc<std::cell::RefCell<Vec<Rc<MinimalSoftwareWindow>>>>,
    window_idx: usize,
    harness: &ResearchHarness,
    dir: &std::path::Path,
    name: &str,
    width: u32,
    height: u32,
    wide: bool,
    medium: bool,
) -> std::path::PathBuf {
    harness.set_wide(wide);
    harness.set_medium(medium);
    let window = platform.borrow()[window_idx].clone();
    window.set_size(slint::LogicalSize::new(width as f32, height as f32));
    harness.window().request_redraw();
    slint::platform::update_timers_and_animations();
    let mut buffer = vec![Rgb8Pixel::default(); (width * height) as usize];
    let mut painted = false;
    window.draw_if_needed(|renderer| {
        renderer.render(&mut buffer, width as usize);
        painted = true;
    });
    assert!(painted, "nothing painted for {name}");
    let first = buffer[0];
    let uniform = buffer
        .iter()
        .all(|p| p.r == first.r && p.g == first.g && p.b == first.b);
    assert!(!uniform, "blank frame for {name}");
    let path = dir.join(format!("{name}_{width}x{height}.bmp"));
    save_bmp(&path, &buffer, width, height);
    println!("rendered {}", path.display());
    path
}

#[test]
fn render_research_matrix() {
    let registry: Rc<std::cell::RefCell<Vec<Rc<MinimalSoftwareWindow>>>> =
        Rc::new(std::cell::RefCell::new(Vec::new()));
    slint::platform::set_platform(Box::new(SoftwarePlatform {
        windows: registry.clone(),
    }))
    .expect("software platform");
    let platform = &registry;
    let out = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("target")
        .join("render-research");
    std::fs::create_dir_all(&out).unwrap();

    // Completed state across the required matrix (content sizes).
    {
        let harness = ResearchHarness::new().unwrap();
        populate_completed(&harness);
        for (name, width, height) in [
            ("completed", 1816, 1017),
            ("completed", 1496, 837),
            ("completed", 1336, 837),
            ("completed", 1262, 705),
            ("completed", 1176, 657),
            ("completed", 596, 537),
            ("completed", 396, 637),
        ] {
            render(
                platform,
                0,
                &harness,
                &out,
                name,
                width,
                height,
                width >= 1180,
                width >= 760,
            );
        }
    }

    // Medium with the inspector drawer open.
    {
        let drawer = ResearchHarness::new().unwrap();
        populate_completed(&drawer);
        drawer.set_tab(2);
        drawer.set_inspector_open(true);
        render(platform, 1, &drawer, &out, "drawer", 1176, 657, false, true);
    }

    // Narrow navigator + inspector views.
    for (idx, (name, view)) in [("nav", 1), ("insp", 2)].into_iter().enumerate() {
        let narrow = ResearchHarness::new().unwrap();
        populate_completed(&narrow);
        narrow.set_narrow_view(view);
        render(
            platform,
            2 + idx,
            &narrow,
            &out,
            name,
            396,
            637,
            false,
            false,
        );
    }

    // Running state: expanded telemetry, cancel visible.
    {
        let running = ResearchHarness::new().unwrap();
        populate_chrome(&running);
        running.set_has_strategy(true);
        running.set_strategy_name("OBR".into());
        running.set_state_label("● RUNNING…".into());
        running.set_state_tone(2);
        running.set_context_line("OBR · RELIANCE, TCS · 5m · 2024-01-01 → 2024-03-31".into());
        running.set_cancel_visible(true);
        running.set_log_expanded(true);
        running.set_log_status("GENERATING SIGNALS (1/2): RELIANCE (1/2).".into());
        running.set_log_lines(
            Rc::new(slint::VecModel::from(vec![
                "Strategy load: OBR".into(),
                "Data validation started".into(),
                "Loaded RELIANCE (1/2)".into(),
                "Loaded TCS (2/2)".into(),
                "Data validation passed — 2 symbols, 3064 bars".into(),
                "Signal generation started".into(),
                "GENERATING SIGNALS (1/2): RELIANCE (1/2).".into(),
            ]))
            .into(),
        );
        render(
            platform, 4, &running, &out, "running", 1262, 705, true, true,
        );
    }

    // Every remaining tab + stale, at desktop width.
    {
        let tabs = ResearchHarness::new().unwrap();
        populate_completed(&tabs);
        tabs.set_trades(
            Rc::new(slint::VecModel::from(vec![
                research_trade(
                    "1",
                    "2024-01-02 09:15:00",
                    "2024-01-02 10:15:00",
                    "-1,240.50",
                    "12",
                ),
                research_trade(
                    "2",
                    "2024-01-03 09:15:00",
                    "2024-01-03 11:15:00",
                    "+860.25",
                    "21",
                ),
            ]))
            .into(),
        );
        tabs.set_trade_count_line("37 TRADES".into());
        for (name, tab) in [
            ("tab-trades", 1),
            ("tab-robust", 2),
            ("tab-compare", 3),
            ("tab-data", 4),
            ("tab-validation", 5),
            ("tab-report", 6),
            ("tab-visuals", 7),
        ] {
            tabs.set_tab(tab);
            render(platform, 5, &tabs, &out, name, 1496, 837, true, true);
        }
        tabs.set_stale(true);
        tabs.set_state_label("◐ STALE".into());
        tabs.set_state_tone(2);
        tabs.set_tab(0);
        render(platform, 5, &tabs, &out, "stale", 1496, 837, true, true);
    }

    // Empty state (truthful idle shape) at medium and narrow.
    let empty = ResearchHarness::new().unwrap();
    empty.set_has_strategy(true);
    empty.set_show_next_steps(true);
    empty.set_strategy_name("OBR".into());
    empty.set_context_line("OBR · — · 15m · —".into());
    empty.set_state_label("○ NO EXPERIMENT".into());
    empty.set_empty_title("NO RESEARCH EXPERIMENT".into());
    empty.set_empty_detail("Select a strategy and define a hypothesis to begin.".into());
    empty.set_strategies(
        Rc::new(slint::VecModel::from(vec![research_row(
            "OBR",
            "Opening Breakout Strategy",
            true,
        )]))
        .into(),
    );
    for (name, width, height) in [("empty", 1262, 705), ("empty", 396, 637)] {
        render(
            platform,
            6,
            &empty,
            &out,
            name,
            width,
            height,
            width >= 1180,
            width >= 760,
        );
    }
}
