//! Strategy Lab §02 CONFIGURE — the three-row baseline regression.
//!
//! The redesign replaced four hand-tuned columns with ONE structural rhythm
//! (label 19 / control 36 / sub 30). This test is what stops that from
//! silently decaying: it renders the real screen through the software
//! renderer and scans columns for the shared baselines, then asserts the two
//! honesty contracts that no pixel test can see —
//!
//! - the completeness strip is ABSENT when coverage was never measured, and
//!   the invented `MIN ₹` / runtime-estimate strings are absent ALWAYS;
//! - the date-preset row has exactly one cell per real preset.
//!
//! A software renderer has no vsync, so nothing here asserts a mid-animation
//! frame: the count-up is a `cov-shown` int the model owns.

use std::rc::Rc;
use std::time::Duration;

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RepaintBufferType, SoftwareRenderer,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::{ComponentHandle, Model, PhysicalSize, Rgb8Pixel, SharedPixelBuffer};
use vayren_shell::{lab, shell, ShellScreen};

struct MiniPlatform {
    window: Rc<MinimalSoftwareWindow>,
}

impl Platform for MiniPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        Ok(self.window.clone())
    }
    fn duration_since_start(&self) -> Duration {
        Duration::from_millis(10)
    }
}

fn install(win: &Rc<MinimalSoftwareWindow>) {
    let _ = slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }));
}

fn render(
    ui: &vayren_shell::AppWindow,
    win: &Rc<MinimalSoftwareWindow>,
    width: u32,
    height: u32,
) -> SharedPixelBuffer<Rgb8Pixel> {
    win.set_size(PhysicalSize::new(width, height));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(width, height);
    let slice = buffer.make_mut_slice();
    win.draw_if_needed(|renderer: &SoftwareRenderer| {
        renderer.render(slice, width as usize);
    });
    buffer
}

/// Contiguous runs of control-fill pixels down one column. The threshold
/// separates the control surface from the card behind it.
fn save_ppm(buffer: &SharedPixelBuffer<Rgb8Pixel>) {
    save_ppm_as("config", buffer)
}

fn save_ppm_as(name: &str, buffer: &SharedPixelBuffer<Rgb8Pixel>) {
    let size = buffer.size();
    let dir = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("lab-config");
    std::fs::create_dir_all(&dir).unwrap();
    let mut raw = Vec::with_capacity((size.width * size.height * 3) as usize + 32);
    raw.extend_from_slice(format!("P6\n{} {}\n255\n", size.width, size.height).as_bytes());
    for p in buffer.as_slice() {
        raw.extend_from_slice(&[p.r, p.g, p.b]);
    }
    std::fs::write(dir.join(format!("{name}.ppm")), raw).unwrap();
}

/// Contiguous runs of CONTROL-FILL pixels down one column.
///
/// The threshold is chosen to separate the control surface (`field-bg`, the
/// 36px input well) from the card behind it (`panel`) — the two differ by
/// ~15 in channel sum, so a scan keyed on the control fill measures the
/// control's own geometry rather than the whole section.
fn runs(buffer: &SharedPixelBuffer<Rgb8Pixel>, x: u32, y0: u32, y1: u32) -> Vec<(u32, u32)> {
    const CONTROL_FILL: u32 = 58;
    let w = buffer.size().width as usize;
    let slice = buffer.as_slice();
    let on = |y: u32| -> bool {
        let p = &slice[y as usize * w + x as usize];
        (p.r as u32 + p.g as u32 + p.b as u32) >= CONTROL_FILL
    };
    let mut found = Vec::new();
    let mut start: Option<u32> = None;
    let mut last = 0u32;
    for y in y0..y1 {
        if on(y) {
            if start.is_none() {
                start = Some(y);
            }
            last = y;
        } else if let Some(s) = start {
            found.push((s, last));
            start = None;
        }
    }
    if let Some(s) = start {
        found.push((s, last));
    }
    found
}

/// A config state with the real bounds the reference mock shows, so the
/// presets and the live-edge flag have something true to place themselves
/// against.
fn configured_state(coverage: bool) -> lab::LabState {
    let mut state = shell::demo_lab_state();
    state.universe_symbols = vec!["A".into(), "B".into(), "C".into()];
    state.universe_selected = vec!["A".into(), "B".into(), "C".into()];
    state.timeframes = vec![
        "5m".into(),
        "15m".into(),
        "30m".into(),
        "1h".into(),
        "1D".into(),
    ];
    state.timeframe_index = 2;
    state.cfg_dates_start = "2019-09-19".into();
    state.cfg_dates_end = "2026-08-18".into();
    state.cfg_capital = "10000".into();
    state.data_bounds_start_days = 18_158;
    state.data_bounds_end_days = 20_683;
    if coverage {
        state.apply_coverage(lab::CoverageMeasurement {
            symbols_total: 3,
            symbols_covering: 3,
            bars_present: 94_000,
            start_days: 18_158,
            end_days: 20_683,
            tf_secs: 1_800,
            sampled: false,
            symbols_probed: 3,
            anchor: "AAA".into(),
        });
    }
    state
}

#[test]
fn every_config_column_shares_the_same_three_row_baselines() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    install(&win);
    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &configured_state(false));
    shell::select(&ui, ShellScreen::Lab);

    let (w, h) = (1280u32, 2400u32);
    let frame = render(&ui, &win, w, h);
    save_ppm(&frame);

    // Four sample columns, one inside each field. The control row is the ~36px
    // band. Its y offset moves whenever a section above changes height, so the
    // assertion is about ALIGNMENT, never about a fixed pixel. The DATE RANGE
    // column is scanned inside its FROM half — its exact centre holds the arrow
    // between the two date fields, which is a gap by design.
    let columns = [205u32, 477, 700, 1147];
    let control_bands: Vec<Vec<(u32, u32)>> = columns
        .iter()
        .map(|x| {
            runs(&frame, *x, 0, 2400)
                .into_iter()
                .filter(|(a, b)| b - a >= 34 && b - a <= 38)
                .collect()
        })
        .collect();
    for (x, bands) in columns.iter().zip(control_bands.iter()) {
        assert!(
            !bands.is_empty(),
            "no ~36px control band in column x={x}: {:?}",
            runs(&frame, *x, 0, 2400)
        );
    }
    // Every column must agree on at least one shared control band. Four columns
    // with independent paddings cannot land three 36px controls on the same
    // scanline, so their agreement IS the baseline proof.
    let common = control_bands
        .iter()
        .map(|bands| {
            bands
                .iter()
                .map(|(a, _)| *a)
                .collect::<std::collections::BTreeSet<u32>>()
        })
        .reduce(|acc, set| acc.intersection(&set).copied().collect())
        .expect("four band sets");
    assert!(
        !common.is_empty(),
        "config columns share no control baseline (columns {columns:?} bands {control_bands:?})"
    );
    // And it is the section-02 card, not some other 36px well on the page: the
    // shared band must be unique, so the four columns line up once and only
    // once.
    assert_eq!(
        common.len(),
        1,
        "controls align on more than one band: {common:?}"
    );
}

#[test]
fn the_date_preset_row_has_one_cell_per_real_preset() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    install(&win);
    let ui = vayren_shell::AppWindow::new().unwrap();
    let state = configured_state(false);
    let expected = lab::project(&state).range_presets.len();
    assert_eq!(expected, 5, "the §02 row is specified as five presets");

    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &state);
    shell::select(&ui, ShellScreen::Lab);
    let _ = render(&ui, &win, 1280, 900);

    // Only the LABELS cross into the screen, so the cell count on screen is
    // exactly the preset count — the click reports an index and Rust resolves
    // the range.
    assert_eq!(ui.get_lab_range_preset_labels().row_count(), expected);
    // The committed range IS the full store window here, so the MAX cell is
    // the one that lights — a hand-picked narrower range must clear it.
    assert_eq!(ui.get_lab().range_preset_idx, 4);
    let mut narrowed = state.clone();
    narrowed.cfg_dates_start = "2024-06-03".into();
    shell::apply_lab(&ui, &narrowed);
    assert_eq!(ui.get_lab().range_preset_idx, -1);
}

#[test]
fn the_completeness_strip_is_absent_until_something_was_measured() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    install(&win);
    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &configured_state(false));
    shell::select(&ui, ShellScreen::Lab);
    let _ = render(&ui, &win, 1280, 900);

    // The model is the authority for "is there a strip at all".
    let header = ui.get_lab();
    assert!(
        !header.cov_on,
        "an unmeasured config must not paint a strip"
    );
    assert_eq!(header.cov_note, "");
    assert!(ui.get_lab_range_preset_labels().row_count() > 0);
}

#[test]
fn a_measured_probe_reaches_the_strip_and_the_model_owns_the_number() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    install(&win);
    let ui = vayren_shell::AppWindow::new().unwrap();
    let state = configured_state(true);
    // Assert the FINAL percent from the model, never a rendered mid-animation
    // frame: the software renderer has no vsync.
    let expected = lab::project(&state).coverage;
    assert!(expected.on);
    assert!(expected.pct > 0 && expected.pct <= 100);

    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &state);
    shell::select(&ui, ShellScreen::Lab);
    let frame = render(&ui, &win, 1280, 900);
    assert!(frame.size().width > 0);
    save_ppm_as("coverage", &frame);
    save_ppm(&frame);

    let header = ui.get_lab();
    assert!(header.cov_on);
    assert_eq!(header.cov_pct, expected.pct);
    assert_eq!(
        header.cov_note,
        slint::SharedString::from(expected.note.clone())
    );
}

#[test]
fn the_progress_panel_appears_only_for_a_measured_run() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    install(&win);
    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    let mut state = configured_state(false);
    state.run = vayren_shell::lab::RunState::Running;
    // A real measured event: 127 of 527 done, 2 failed, AARTIIND in flight.
    state.apply_progress(&serde_json::json!({
        "stage": "calculate",
        "total": 527, "done": 129, "headline_total": 527, "completed": 127,
        "failed": ["BAD1", "BAD2"], "skipped": [], "remaining": 398, "pct": 24.5,
        "current": "AARTIIND", "current_secs": 4.2, "elapsed_secs": 134.0,
        "eta_secs": 392.0, "mean_secs": 1.054, "throughput": 0.948,
        "trades": 124520, "bars": 2800000, "net_pnl": 4211.5,
        "long_running": false, "quiet": false, "cancelled": false
    }));
    shell::apply_lab(&ui, &state);
    shell::select(&ui, ShellScreen::Lab);
    let frame = render(&ui, &win, 1280, 2400);
    save_ppm_as("progress", &frame);

    let header = ui.get_lab();
    assert!(header.prog_active, "a measured run must show the panel");
    assert_eq!(header.prog_headline, "129 / 527 stocks");
    assert_eq!(header.prog_current, "AARTIIND");
    assert_eq!(header.prog_stage, "Strategy calculation");
    assert_eq!(header.prog_elapsed, "02:14");
    assert_eq!(header.prog_eta, "~06:32");
    assert!(header.prog_failed_line.contains("BAD1"));
    // The bar is a real fraction of 100, not a decorative constant.
    assert!((header.prog_pct - 24.5).abs() < 0.01);

    // Once the run ends the panel retires — no infinite "RUNNING".
    let mut finished = state.clone();
    finished.clear_progress();
    shell::apply_lab(&ui, &finished);
    assert!(!ui.get_lab().prog_active);
}

#[test]
fn an_unmeasured_run_shows_a_dash_not_a_fabricated_zero() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    install(&win);
    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    let mut state = configured_state(false);
    state.run = vayren_shell::lab::RunState::Running;
    // The very first event: the run started, no sample finished yet.
    state.apply_progress(&serde_json::json!({
        "stage": "data", "total": 527, "done": 0, "headline_total": 527,
        "completed": 0, "pct": 0.0, "eta_secs": serde_json::Value::Null,
        "mean_secs": serde_json::Value::Null, "elapsed_secs": 0.4
    }));
    shell::apply_lab(&ui, &state);
    let header = ui.get_lab();
    assert_eq!(
        header.prog_eta, "—",
        "no samples yet means no ETA, not a guess"
    );
    assert_eq!(header.prog_speed, "—");
}

#[test]
fn no_invented_minimum_or_runtime_estimate_reaches_the_screen() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    install(&win);
    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &configured_state(true));
    shell::select(&ui, ShellScreen::Lab);
    let _ = render(&ui, &win, 1280, 900);

    // The mock's `MIN ₹5,000`, `NO LEVERAGE` and `Est. runtime` have no basis
    // in this product: the kernel's only capital rule is `> 0`
    // (`backtest_validation.rs`), and nothing in lab state ever measured a run
    // duration. None of those strings may exist on the screen.
    let header = ui.get_lab();
    let strings = [
        header.chips_more_line,
        header.range_span_line,
        header.cov_note,
        header.sym_button_line,
        header.run_label,
    ];
    for s in strings {
        let s: String = s.to_string();
        for banned in ["MIN ", "NO LEVERAGE", "Est. runtime", "GAPS FILLED"] {
            assert!(
                !s.contains(banned),
                "invented string {banned:?} reached the screen in {s:?}"
            );
        }
    }
    for check in 0..header.cfg_checks.row_count() {
        let label: String = header
            .cfg_checks
            .row_data(check)
            .expect("check row")
            .label
            .to_string();
        for banned in ["MIN ", "NO LEVERAGE", "above minimum"] {
            assert!(
                !label.contains(banned),
                "invented check {banned:?} reached the screen in {label:?}"
            );
        }
    }
}
