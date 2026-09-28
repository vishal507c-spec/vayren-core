//! Ranking-table performance forensics ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â deterministic headless benchmarks.
//!
//! Mirrors `perf.rs` (chart) for the Lab ranking table: no production surface
//! (everything `#[cfg(test)]`), synthetic datasets, the REAL `LabState` ops and
//! the REAL `project()` path, mean/p95/max per op. Budgets are loose (loaded CI
//! machines vary); the printed table is the evidence. Compare BEFORE vs AFTER
//! with `cargo test -p vayren-shell perf_table -- --nocapture`.
//!
//! The table's promise (spec): rendered work must track the VIEWPORT, never the
//! dataset ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â so these benchmarks assert `rows rendered ÃƒÂ¢Ã¢â‚¬Â°Ã‹â€  visible + overscan`
//! while the dataset grows 1k ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ 10k+, and they time the scroll path a frame
//! actually pays for.

#![cfg(test)]

use crate::lab::{
    apply_snapshot_json, project, LabResults, LabState, LabStrategy, RankRow, RunState, Tone,
};
use std::fmt::Write as _;
use std::time::{Duration, Instant};

/// Fixed row height in logical pixels ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the stable-layout contract (`spec Ãƒâ€šÃ‚Â§12`):
/// a predictable height is what makes the virtual scroll height exact and the
/// window arithmetic integer.
pub const ROW_H: f32 = 40.0;

#[derive(Debug, Default)]
struct Stats {
    samples: Vec<Duration>,
}

impl Stats {
    fn push(&mut self, d: Duration) {
        self.samples.push(d);
    }

    fn report(&mut self, label: &str) -> (f64, f64, f64) {
        self.samples.sort_unstable();
        let n = self.samples.len().max(1);
        let mean = self.samples.iter().sum::<Duration>().as_secs_f64() / n as f64 * 1000.0;
        let p95 = self.samples[(n * 95 / 100).min(n - 1)].as_secs_f64() * 1000.0;
        let max = self.samples[n - 1].as_secs_f64() * 1000.0;
        eprintln!("PERF {label}: n={n} mean={mean:.3}ms p95={p95:.3}ms max={max:.3}ms");
        (mean, p95, max)
    }
}

fn symbol(i: usize) -> String {
    format!("SYM{i:06}")
}

/// Raw numbers for one synthetic row ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the data layer keeps these as the sort
/// keys (`spec Ãƒâ€šÃ‚Â§6/Ãƒâ€šÃ‚Â§7`: compare and format never touch the display strings).
fn bench_row_numbers(i: usize) -> [f64; 7] {
    [
        1000.0 + (i % 977) as f64 * 3.5 - 900.0,
        (i % 313) as f64 * 0.31 - 48.0,
        1.0 + (i % 480) as f64,
        20.0 + (i % 600) as f64 * 0.1,
        0.4 + (i % 260) as f64 * 0.01,
        1.0 + (i % 700) as f64 * 0.05,
        -2.0 + (i % 400) as f64 * 0.01,
    ]
}

/// Rows already in the data layer (what a parsed snapshot produces).
fn bench_rows(n: usize) -> Vec<RankRow> {
    (0..n)
        .map(|i| {
            let [net, ret, trades, win, pf, dd, sharpe] = bench_row_numbers(i);
            RankRow {
                rank: (i + 1).to_string(),
                symbol: symbol(i),
                pnl: format!("ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{net:.2}"),
                ret: format!("{ret:+.2}%"),
                trades: format!("{trades:.0}"),
                win: format!("{win:.1}%"),
                pf: format!("{pf:.2}"),
                dd: format!("-{dd:.2}%"),
                sharpe: format!("{sharpe:.2}"),
                pnl_tone: if net >= 0.0 {
                    Tone::Positive
                } else {
                    Tone::Negative
                },
                pf_tone: if pf > 1.0 {
                    Tone::Positive
                } else {
                    Tone::Negative
                },
                unranked: i % 50 == 0,
                sort: [net, ret, trades, win, pf, dd, sharpe],
            }
        })
        .collect()
}

/// The bridge snapshot, built as text: a parsed 100k-row payload is the ingest
/// cost the spec wants measured, and `serde_json::from_str` keeps the test
/// itself cheap to compile.
fn bench_snapshot_text(n: usize) -> String {
    let mut csv = String::new();
    let mut rows = String::new();
    for i in 0..n {
        if i > 0 {
            csv.push(',');
        }
        let _ = write!(csv, "{}", symbol(i));
        let [net, ret, trades, win, pf, dd, sharpe] = bench_row_numbers(i);
        let _ = write!(
            rows,
            concat!(
                r#"{{"rank":{},"symbol":"{}","status":"{}","net_profit":{:.2},"#,
                r#""return_pct":{:.2},"total_trades":{:.0},"win_rate":{:.1},"#,
                r#""profit_factor":{:.2},"max_drawdown_pct":{:.2},"sharpe_ratio":{:.2}}}"#
            ),
            i + 1,
            symbol(i),
            if i % 50 == 0 { "unranked" } else { "ranked" },
            net,
            ret,
            trades,
            win,
            pf,
            dd,
            sharpe,
        );
        if i + 1 < n {
            rows.push(',');
        }
    }
    format!(
        concat!(
            r#"{{"selected_name":"PERF","run":"complete","engine_wired":true,"#,
            r#""cfg_edit":{{"universe_csv":"{csv}","timeframes":["5m","15m","1h"],"#,
            r#""timeframe":"15m","dates_start":"2024-01-01","dates_end":"2024-06-30","#,
            r#""capital":10000}},"#,
            r#""universe":{{"symbols":["{csv}"],"selected":["{csv}"]}},"#,
            r#""rankby":{{"labels":["Net P&L","Return %","Trades","Win %","Profit Factor","Max DD"],"#,
            r#""current":0,"search":"","desc":true}},"#,
            r#""results":{{"metrics":{{"net_profit":1000.0,"total_trades":10}},"#,
            r#""ranking":[{rows}],"trades":[],"equity_curve":[],"risk_notes":[]}}}}"#
        ),
        csv = csv,
        rows = rows,
    )
}

/// Frame-path state: the data layer already holds every row (this is what the
/// snapshot produced) and a completed run is showing. `rebuild_rank_view` is
/// called once here ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the same call the snapshot path makes.
fn loaded_state(n: usize) -> LabState {
    let mut st = LabState::default();
    st.engine_wired = true;
    st.strategies = vec![LabStrategy {
        name: "PERF".into(),
        description: "synthetic ranking benchmark".into(),
        tags: vec!["BENCH".into()],
        version: "v1".into(),
        modified: "2026-09-28".into(),
        favorite: false,
        last_backtest: "ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â".into(),
    }];
    st.selected = Some(0);
    st.rankby_labels = vec![
        "Net P&L".into(),
        "Return %".into(),
        "Trades".into(),
        "Win %".into(),
        "Profit Factor".into(),
        "Max DD".into(),
    ];
    st.rank_desc = true;
    st.cfg_universe_csv = (0..n).map(symbol).collect::<Vec<_>>().join(",");
    st.cfg_universe_count = n;
    st.universe_selected = (0..n).map(symbol).collect();
    st.results = Some(LabResults {
        ranking: bench_rows(n),
        ..LabResults::default()
    });
    st.run = RunState::Complete;
    st.rebuild_rank_view();
    st
}

/// Viewport in rows: the 10-row initial window the spec asks for.
fn viewport_rows() -> usize {
    10
}

#[test]
fn bench_snapshot_ingest_keeps_every_row() {
    // Pipeline stage 1: the bridge snapshot. Cells are formatted once, here, on
    // arrival ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the frame path must never redo that work (`spec Ãƒâ€šÃ‚Â§7`).
    for n in [1_000usize, 10_000] {
        let text = bench_snapshot_text(n);
        let value: serde_json::Value = serde_json::from_str(&text).expect("valid snapshot");
        let mut st = LabState::default();
        let t = Instant::now();
        apply_snapshot_json(&mut st, &value);
        let ms = t.elapsed().as_secs_f64() * 1000.0;
        eprintln!("PERF apply_snapshot n={n}: {ms:.2}ms");
        assert_eq!(
            st.results.as_ref().expect("results").ranking.len(),
            n,
            "every row must reach the data layer (no truncation on ingest)"
        );
    }
}

#[test]
fn bench_project_cost_is_independent_of_dataset_size() {
    // The core budget: a projection must not scale with the dataset. Only the
    // virtual window may be cloned per frame.
    let mut means = Vec::new();
    for n in [1_000usize, 10_000, 50_000] {
        let st = loaded_state(n);
        let mut stats = Stats::default();
        for _ in 0..20 {
            let t = Instant::now();
            let view = project(&st);
            stats.push(t.elapsed());
            assert!(
                view.ranking.len() <= viewport_rows() * 4,
                "n={n}: {} rows rendered for a {}-row viewport",
                view.ranking.len(),
                viewport_rows()
            );
        }
        let (mean, p95, _) = stats.report(&format!("project n={n} viewport=10"));
        assert!(mean < 20.0, "project() mean {mean:.2}ms over budget");
        assert!(p95 < 40.0, "project() p95 {p95:.2}ms over budget");
        means.push(mean);
    }
    // 50x the data must not cost 50x the frame.
    assert!(
        means[2] < means[0] * 6.0,
        "projection scales with dataset: 1k={:.3}ms 50k={:.3}ms",
        means[0],
        means[2]
    );
}

#[test]
fn bench_keystroke_filter_stays_bounded() {
    // Typing (`spec Ãƒâ€šÃ‚Â§15`): the sort criterion is unchanged, so a keystroke only
    // filters the EXISTING order ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no re-sort. This is the per-keystroke path.
    let mut st = loaded_state(50_000);
    let mut stats = Stats::default();
    for i in 0..30 {
        let t = Instant::now();
        st.interaction_ranksearch(&format!("SYM{:05}", i));
        let view = project(&st);
        stats.push(t.elapsed());
        assert!(view.ranking.len() <= viewport_rows() * 4);
    }
    let (mean, p95, _) = stats.report("keystroke filter n=50k x30");
    assert!(mean < 40.0, "keystroke mean {mean:.2}ms over budget");
    assert!(p95 < 80.0, "keystroke p95 {p95:.2}ms over budget");
}

#[test]
fn bench_sort_change_reorders_within_budget() {
    // Changing the criterion (`spec Ãƒâ€šÃ‚Â§16`) is the ONE O(N log N) operation in the
    // table, and it is user-initiated (a dropdown click), not per-frame. Budget
    // is set for an unoptimised build ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â this is the number the dev build pays;
    // the release build sorts the same 50k indices several times faster.
    let mut st = loaded_state(50_000);
    let mut stats = Stats::default();
    for i in 0..12 {
        let t = Instant::now();
        st.interaction_rankby(&st.rankby_labels[(i % 6) as usize].clone());
        let view = project(&st);
        stats.push(t.elapsed());
        assert!(view.ranking.len() <= viewport_rows() * 4);
    }
    let (mean, p95, _) = stats.report("sort change n=50k x12 (index re-sort)");
    assert!(mean < 150.0, "sort-change mean {mean:.2}ms over budget");
    assert!(p95 < 250.0, "sort-change p95 {p95:.2}ms over budget");
}

#[test]
fn bench_scroll_path_stays_inside_the_frame_budget() {
    // Scroll is the critical path (`spec Ãƒâ€šÃ‚Â§8/Ãƒâ€šÃ‚Â§14`): a long flick across a
    // 50k-row dataset, worst case at the far end.
    let mut st = loaded_state(50_000);
    let mut stats = Stats::default();
    for _ in 0..600 {
        let t = Instant::now();
        st.interaction_rank_scroll(ROW_H * 3.0);
        let view = project(&st);
        stats.push(t.elapsed());
        assert!(
            view.ranking.len() <= viewport_rows() * 4,
            "scroll rendered {} rows",
            view.ranking.len()
        );
    }
    let (mean, p95, max) = stats.report("scroll+project n=50k x600");
    assert!(mean < 8.0, "scroll mean {mean:.2}ms over budget");
    assert!(p95 < 16.0, "scroll p95 {p95:.2}ms over budget");
    assert!(max < 50.0, "scroll max {max:.2}ms over budget");
}

#[test]
fn bench_fast_flick_grows_overscan_and_idle_shrinks_it_back() {
    // Adaptive quality (`spec Ãƒâ€šÃ‚Â§2/Ãƒâ€šÃ‚Â§9`): a fast scroll pre-renders more rows
    // than an idle table, and the band shrinks back once scrolling stops.
    let mut st = loaded_state(50_000);
    let idle = project(&st).ranking.len();
    st.interaction_rank_scroll(ROW_H * 40.0); // a flick
    let flick = project(&st).ranking.len();
    assert!(
        flick > idle,
        "a fast scroll must pre-render more rows: idle={idle} flick={flick}"
    );
    st.interaction_rank_idle();
    let settled = project(&st).ranking.len();
    assert!(
        settled < flick,
        "an idle table must shrink its band: flick={flick} settled={settled}"
    );
    eprintln!("PERF overscan: idle={idle} flick={flick} settled={settled}");
}

#[test]
fn bench_top_bottom_top_returns_the_same_window() {
    // Scroll-position integrity (`spec Ãƒâ€šÃ‚Â§11`): no blank rows, no jumping, no
    // wrong order ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â and no leaked state (the window must be identical to the
    // start, not merely plausible).
    let mut st = loaded_state(50_000);
    let first: Vec<String> = project(&st)
        .ranking
        .iter()
        .map(|r| r.symbol.clone())
        .collect();
    // Walk the whole dataset in viewport-sized steps, then jump to the very end
    // so the clamp (not the loop count) decides where "bottom" is.
    let mut notches = 0;
    while st.rank.max_scroll() > st.rank.scroll_px && notches < 10_000 {
        st.interaction_rank_scroll(ROW_H * 20.0);
        let view = project(&st);
        assert!(
            !view.ranking.is_empty(),
            "scrolled window must never be empty"
        );
        notches += 1;
    }
    st.interaction_rank_scroll(f32::MAX);
    let bottom = project(&st);
    assert_eq!(
        bottom.rank_window.scroll_px, bottom.rank_window.max_scroll_px,
        "an overshooting notch must land exactly on the end (no overshoot)"
    );
    st.interaction_rank_scroll_to(0.0);
    let back = project(&st);
    let back_symbols: Vec<String> = back.ranking.iter().map(|r| r.symbol.clone()).collect();
    assert_eq!(
        first, back_symbols,
        "TOP -> BOTTOM -> TOP must be identical"
    );
}
