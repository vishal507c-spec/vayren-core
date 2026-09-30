//! Trade-blotter performance forensics — deterministic headless benchmarks.
//!
//! Same discipline as `perf.rs` (chart) and `perf_table.rs` (ranking grid): no
//! production surface (everything `#[cfg(test)]`), synthetic datasets, the REAL
//! `LabState` ops and the REAL `project()` path, mean/p95/max per op. The
//! printed table is the evidence; budgets are structural plus deliberately
//! loose, because a shared CI runner stretches wall-clock numbers.
//!
//! What these prove (spec §18/§32): rendered work tracks the VIEWPORT, not the
//! dataset — at 10k, 100k and 1M trades the UI receives the same ~20 rows.

#![cfg(test)]

use crate::lab::{
    apply_snapshot_json, project, LabResults, LabState, LabStrategy, RankRow, RunState, Tone,
    TradeRow,
};
use std::fmt::Write as _;
use std::time::{Duration, Instant};

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

    fn median(&self) -> f64 {
        if self.samples.is_empty() {
            return 0.0;
        }
        let mut sorted = self.samples.clone();
        sorted.sort_unstable();
        let mid = sorted.len() / 2;
        if sorted.len() % 2 == 0 {
            (sorted[mid - 1].as_secs_f64() + sorted[mid].as_secs_f64()) * 0.5 * 1000.0
        } else {
            sorted[mid].as_secs_f64() * 1000.0
        }
    }
}

fn bench_trades(n: usize) -> Vec<TradeRow> {
    (0..n)
        .map(|i| {
            let net = 1000.0 + (i % 977) as f64 * 3.5 - 900.0;
            let wins = i % 3 != 0;
            TradeRow {
                no: (i + 1).to_string(),
                abs_index: i as i32,
                symbol: format!("SYM{:05}", i % 997),
                side: if i % 2 == 0 {
                    "LONG".into()
                } else {
                    "SHORT".into()
                },
                entry: format!("2026-01-{:02} 09:{:02}", (i % 28) + 1, (i % 60)),
                entry_px: format!("{:.2}", 1000.0 + i as f64 * 0.01),
                exit: format!("2026-01-{:02} 15:{:02}", (i % 28) + 1, (i % 60)),
                exit_px: format!("{:.2}", 1000.0 + i as f64 * 0.02),
                pnl: format!("{net:+.2}"),
                r: format!("{:.2}", net / 1000.0),
                bars: (1 + i % 400).to_string(),
                reason: if wins { "TARGET".into() } else { "STOP".into() },
                pnl_tone: if wins { Tone::Positive } else { Tone::Negative },
                selected: false,
                sort: [
                    29_000.0 + i as f64,
                    net,
                    net / 1000.0,
                    1.0 + (i % 400) as f64,
                ],
            }
        })
        .collect()
}

fn empty_ranking() -> Vec<RankRow> {
    Vec::new()
}

fn loaded_state(n: usize) -> LabState {
    let mut st = LabState::default();
    st.engine_wired = true;
    st.strategies = vec![LabStrategy {
        name: "PERF".into(),
        description: "trade blotter benchmark".into(),
        tags: vec!["BENCH".into()],
        version: "v1".into(),
        modified: "2026-09-28".into(),
        favorite: false,
        last_backtest: "—".into(),
    }];
    st.selected = Some(0);
    st.results = Some(LabResults {
        ranking: empty_ranking(),
        trades: bench_trades(n),
        ..LabResults::default()
    });
    st.run = RunState::Complete;
    st.rebuild_trade_view();
    st
}

fn viewport_rows() -> usize {
    14
}

#[test]
fn bench_trade_projection_is_independent_of_dataset_size() {
    // The core budget: projecting the Lab (which now carries a trade blotter)
    // must not scale with the trade count. Only the window may be cloned.
    let mut medians = Vec::new();
    for n in [1_000usize, 10_000, 100_000] {
        let st = loaded_state(n);
        let mut stats = Stats::default();
        for _ in 0..20 {
            let t = Instant::now();
            let view = project(&st);
            stats.push(t.elapsed());
            assert!(
                view.trades.len() <= viewport_rows() * 3,
                "n={n}: {} trade rows rendered for a {}-row viewport",
                view.trades.len(),
                viewport_rows()
            );
            assert_eq!(view.trade_total as usize, n, "n={n}: true trade count");
        }
        let (mean, p95, _) = stats.report(&format!("project n={n} trades viewport=14"));
        assert!(mean < 20.0, "project() mean {mean:.2}ms over budget");
        assert!(p95 < 40.0, "project() p95 {p95:.2}ms over budget");
        medians.push(stats.median());
    }
    let floor = 0.05;
    assert!(
        medians[2] < medians[0].max(floor) * 10.0,
        "trade projection scales with the dataset: 1k={:.3}ms 100k={:.3}ms",
        medians[0],
        medians[2]
    );
}

#[test]
fn bench_trade_scroll_stays_inside_the_frame_budget() {
    // Scroll is the critical path (spec §19): a long flick across 100k trades.
    let mut st = loaded_state(100_000);
    let mut stats = Stats::default();
    for _ in 0..600 {
        let t = Instant::now();
        st.interaction_trade_scroll(60.0);
        let view = project(&st);
        stats.push(t.elapsed());
        assert!(
            view.trades.len() <= viewport_rows() * 3,
            "scroll rendered too many"
        );
    }
    let (mean, p95, max) = stats.report("trade scroll+project n=100k x600");
    assert!(mean < 8.0, "scroll mean {mean:.2}ms over budget");
    assert!(p95 < 16.0, "scroll p95 {p95:.2}ms over budget");
    assert!(max < 50.0, "scroll max {max:.2}ms over budget");
}

#[test]
fn bench_trade_search_and_filter_stay_bounded() {
    // Per-keystroke path: the criterion is unchanged, so a keystroke only
    // filters the existing index (no re-sort) (spec §15).
    let mut st = loaded_state(100_000);
    let mut stats = Stats::default();
    for i in 0..30 {
        let t = Instant::now();
        st.interaction_tradefilter(&format!("SYM{:05}", i));
        let view = project(&st);
        stats.push(t.elapsed());
        assert!(view.trades.len() <= viewport_rows() * 3);
    }
    let (mean, p95, _) = stats.report("trade keystroke filter n=100k x30");
    assert!(mean < 40.0, "keystroke mean {mean:.2}ms over budget");
    assert!(p95 < 80.0, "keystroke p95 {p95:.2}ms over budget");
}

#[test]
fn bench_trade_sort_reorders_within_budget() {
    // Changing the sort column is the ONE O(N log N) blotter operation, and it
    // is user-initiated (a dropdown click), not per frame.
    let mut st = loaded_state(100_000);
    let mut stats = Stats::default();
    for field in 0..=4 {
        let t = Instant::now();
        st.interaction_tradesort(field);
        let view = project(&st);
        stats.push(t.elapsed());
        assert!(view.trades.len() <= viewport_rows() * 3);
    }
    let (mean, p95, _) = stats.report("trade sort change n=100k x5");
    assert!(mean < 200.0, "sort mean {mean:.2}ms over budget");
    assert!(p95 < 400.0, "sort p95 {p95:.2}ms over budget");
}

#[test]
fn bench_million_trade_dataset_stays_functional() {
    // Spec §33 stress: 1M synthetic trades through the real parse → index →
    // project path. This is a STRESS construction, not a claim about the
    // production dataset (spec §35: the real data is what it is).
    let t0 = Instant::now();
    let text = trade_snapshot_text(1_000_000);
    let build_ms = t0.elapsed().as_secs_f64() * 1000.0;
    eprintln!("PERF build 1M-trade snapshot text: {build_ms:.0}ms");
    let value: serde_json::Value = serde_json::from_str(&text).expect("valid snapshot");
    let mut st = LabState::default();
    st.engine_wired = true;
    st.strategies = vec![LabStrategy {
        name: "PERF".into(),
        description: "stress".into(),
        tags: vec![],
        version: "v1".into(),
        modified: "2026-09-28".into(),
        favorite: false,
        last_backtest: "—".into(),
    }];
    st.selected = Some(0);
    let t = Instant::now();
    apply_snapshot_json(&mut st, &value);
    let ingest_ms = t.elapsed().as_secs_f64() * 1000.0;
    eprintln!("PERF apply_snapshot 1M trades: {ingest_ms:.0}ms");
    assert_eq!(
        st.results.as_ref().expect("results").trades.len(),
        1_000_000,
        "every trade must reach the data layer"
    );
    let t = Instant::now();
    let view = project(&st);
    let project_ms = t.elapsed().as_secs_f64() * 1000.0;
    eprintln!("PERF project 1M trades: {project_ms:.2}ms");
    assert_eq!(view.trade_total, 1_000_000, "true count, not the band");
    assert!(
        view.trades.len() <= viewport_rows() * 3,
        "1M trades rendered {} rows",
        view.trades.len()
    );
    // A scroll through the middle still renders a viewport-sized band.
    st.interaction_trade_scroll(28.0 * 500_000.0);
    let mid = project(&st);
    assert!(mid.trades.len() <= viewport_rows() * 3);
    assert!(!mid.trades.is_empty(), "the middle must have rows");
    assert!(
        project_ms < 200.0,
        "project at 1M trades took {project_ms:.2}ms"
    );
}

fn trade_snapshot_text(n: usize) -> String {
    let mut rows = String::with_capacity(n * 160);
    for i in 0..n {
        let net = 1000.0 + (i % 977) as f64 * 3.5 - 900.0;
        let wins = i % 3 != 0;
        let _ = write!(
            rows,
            concat!(
                r#"{{"symbol":"SYM{:05}","side":"{}","entry_time":"2026-01-{:02}T09:{:02}:00","#,
                r#""exit_time":"2026-01-{:02}T15:{:02}:00","entry_px":{:.2},"exit_px":{:.2},"#,
                r#""pnl":{:.2},"r_multiple":{:.4},"bars":{},"reason":"{}","winning":{}}}"#
            ),
            i % 997,
            if i % 2 == 0 { "LONG" } else { "SHORT" },
            (i % 28) + 1,
            i % 60,
            (i % 28) + 1,
            i % 60,
            1000.0 + i as f64 * 0.01,
            1000.0 + i as f64 * 0.02,
            net,
            net / 1000.0,
            1 + i % 400,
            if wins { "TARGET" } else { "STOP" },
            wins
        );
        if i + 1 < n {
            rows.push(',');
        }
    }
    format!(
        concat!(
            r#"{{"selected_name":"PERF","run":"complete","engine_wired":true,"#,
            r#""cfg_edit":{{"universe_csv":"SYM00001","timeframes":["15m"],"timeframe":"15m","#,
            r#""dates_start":"2026-01-01","dates_end":"2026-01-28","capital":10000}},"#,
            r#""universe":{{"symbols":["SYM00001"],"selected":["SYM00001"]}},"#,
            r#""rankby":{{"labels":["Net P&L"],"current":0,"search":"","desc":true}},"#,
            r#""results":{{"metrics":{{"net_profit":1.0,"total_trades":1}},"ranking":[],"#,
            r#""trades":[{rows}],"equity_curve":[],"risk_notes":[]}}}}"#
        ),
        rows = rows
    )
}
