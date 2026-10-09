//! Strategy Lab ranking scroll fast-path verification (headless, no pixels).
//!
//! Every scroll notch used to run the FULL `apply_lab` screen sync (~500
//! lines: library, KPIs, presets, equity, detail models rebuilt per wheel
//! tick). Scroll now pushes only the rank band (`apply_lab_rank_scroll`):
//! the window prop every notch, the row model only when the band signature
//! changes. This test pins the fast path's contract —
//!   1. a deep scroll lands exactly on `max_scroll` (no overshoot),
//!   2. the pushed band equals the projected window (count + identity),
//!   3. unrelated models (library) keep their rows (no churn).
//! Rendering itself (row `y` from `first`) is pinned by the domain geometry
//! test `rank_window_first_anchors_every_visible_row` plus the Slint formula
//! beside it; the software-renderer suites guard against visual regresssion.

use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

use slint::platform::software_renderer::{MinimalSoftwareWindow, RepaintBufferType};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::Model;
use vayren_shell::lab::{LabResults, LabState, LabStrategy, RankRow, RunState, Tone};
use vayren_shell::{shell, ShellScreen};

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

fn rank_row(i: usize) -> RankRow {
    RankRow {
        rank: (i + 1).to_string(),
        symbol: format!("SYM{i:03}"),
        pnl: format!("₹{}", 1000 + i),
        ret: "+1.00%".into(),
        trades: "1".into(),
        win: "50.0%".into(),
        pf: "1.00".into(),
        dd: "-1.00%".into(),
        sharpe: "0.00".into(),
        pnl_tone: Tone::Positive,
        pf_tone: Tone::Neutral,
        unranked: false,
        sort: [i as f64, 1.0, 1.0, 50.0, 1.0, 1.0, 0.0],
    }
}

#[test]
fn rank_scroll_fast_path_lands_exact_window_without_churn() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::select(&ui, ShellScreen::Lab);

    // Thirty ranked rows, descending net P&L: SYM029 first, SYM000 last.
    let state = Rc::new(RefCell::new(LabState::default()));
    {
        let mut guard = state.borrow_mut();
        // A real selection needs a real library row: `project` reads
        // `selected_strategy()`, so a bare `selected` index would leave
        // `has_strategy` false and the window legitimately empty.
        guard.strategies = vec![LabStrategy {
            name: "PERF".into(),
            description: "ranking scroll fixture".into(),
            tags: vec!["BENCH".into()],
            version: "v1".into(),
            modified: "2026-10-08".into(),
            favorite: false,
            last_backtest: "—".into(),
        }];
        guard.selected = Some(0);
        guard.engine_wired = true;
        guard.rank_desc = true;
        guard.rankby_labels = vec!["Net P&L".into()];
        guard.run = RunState::Complete;
        guard.apply_result(LabResults {
            ranking: (0..30).map(rank_row).collect(),
            ..LabResults::default()
        });
    }
    shell::apply_lab(&ui, &state.borrow());
    let library_rows = ui.get_lab_library().row_count();
    let expected = vayren_shell::lab::project(&state.borrow());
    assert_eq!(
        ui.get_lab_ranking().row_count(),
        expected.ranking.len(),
        "baseline band must equal the projected window"
    );

    // Deep scroll through the fast path only (no full sync).
    let max = state.borrow().rank.max_scroll();
    assert!(max > 0.0, "fixture must be scrollable");
    state.borrow_mut().interaction_rank_scroll_to(max);
    shell::apply_lab_rank_scroll(&ui, &state.borrow());

    let window = ui.get_lab_rank_window();
    assert_eq!(
        window.scroll_px, window.max_scroll_px,
        "a scroll-to(max) must land exactly on the end"
    );
    let view = vayren_shell::lab::project(&state.borrow());
    assert_eq!(
        ui.get_lab_ranking().row_count(),
        view.ranking.len(),
        "fast-path band must equal the projected window"
    );
    assert_eq!(
        view.ranking.len(),
        window.count as usize,
        "model length must equal the window count"
    );
    // Band identity: rendered row `rindex` carries dataset `first + rindex`.
    let first_symbol = view.ranking.first().expect("band has rows").symbol.clone();
    let pushed = ui
        .get_lab_ranking()
        .row_data(0)
        .expect("pushed row 0")
        .symbol;
    assert_eq!(
        pushed.as_str(),
        first_symbol.as_str(),
        "pushed band must match the window"
    );
    assert_eq!(
        first_symbol,
        format!("SYM{:03}", 29 - view.rank_window.first as usize),
        "first rendered row must be the window origin in descending order"
    );
    // No churn outside the band: the library model keeps every row.
    assert_eq!(
        ui.get_lab_library().row_count(),
        library_rows,
        "a rank scroll must not rebuild unrelated models"
    );
}
