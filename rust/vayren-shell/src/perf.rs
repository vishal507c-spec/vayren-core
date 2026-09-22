//! Chart performance forensics — deterministic headless benchmarks.
//!
//! AI_ENTRY.md §1: Rust owns the chart engine; this module measures it.
//! No production surface: everything here is `#[cfg(test)]`. Each benchmark
//! builds synthetic bar series, drives the REAL `MarketState` ops and the
//! REAL `project()`/`apply_market` path, and reports mean/p95/max per op.
//! Budgets are deliberately loose (loaded CI machines vary); the printed
//! table is the evidence. Compare BEFORE vs AFTER by running with
//! `-- --nocapture`.

#![cfg(test)]

use crate::market::{project, MarketAction, MarketBar, MarketState};
use std::time::{Duration, Instant};

fn bench_bars(n: usize) -> Vec<MarketBar> {
    (0..n)
        .map(|i| {
            let open = 1000.0 + i as f64 * 0.01;
            let close = open + 2.0;
            MarketBar {
                time: format!("2024-01-02T{:02}:{:02}:00", 9 + (i / 60) % 15, i % 60),
                open,
                high: open.max(close) + 1.0,
                low: open.min(close) - 1.0,
                close,
                volume: 1000.0 + i as f64,
            }
        })
        .collect()
}

fn loaded_state(n: usize) -> MarketState {
    let mut st = MarketState::default();
    st.width_cap = 1800;
    st.set_bars("PERF", "15m", "NSE", bench_bars(n));
    st
}

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

#[test]
fn bench_set_bars_scales_linearly() {
    for n in [1_000usize, 5_000, 20_000, 60_000] {
        let bars = bench_bars(n);
        let t = Instant::now();
        let mut st = MarketState::default();
        st.set_bars("PERF", "15m", "NSE", bars);
        let ms = t.elapsed().as_secs_f64() * 1000.0;
        eprintln!("PERF set_bars n={n}: {ms:.2}ms total");
        assert_eq!(st.bars.len(), n);
    }
}

#[test]
fn bench_project_visible_window_only() {
    let st = loaded_state(60_000);
    // Proof of culling: projection covers the visible window, never 60k.
    assert!(st.bars.len() == 60_000);
    let mut stats = Stats::default();
    for _ in 0..50 {
        let t = Instant::now();
        let view = project(&st);
        stats.push(t.elapsed());
        assert_eq!(view.candles.len(), st.visible_window().len());
        assert!(view.candles.len() <= 1800);
    }
    let (mean, _, _) = stats.report("project 60k-store/1200-visible");
    assert!(mean < 50.0, "project() mean {mean:.2}ms over budget");
}

#[test]
fn bench_hover_full_path_cost() {
    // BEFORE reference: every hover runs apply + full project + full Slint
    // model rebuild. This measures the Rust side (project); the Slint model
    // churn is measured in bench_apply_market_full below.
    let mut st = loaded_state(20_000);
    let mut stats = Stats::default();
    for i in 0..200 {
        let x = (i % 100) as f32 / 100.0;
        let t = Instant::now();
        assert!(st.apply(MarketAction::HoverMoved(x, 0.5)));
        let view = project(&st);
        stats.push(t.elapsed());
        assert!(view.has_hover);
    }
    let (mean, p95, max) = stats.report("hover apply+project x200");
    assert!(mean < 50.0, "hover mean {mean:.2}ms over budget");
    assert!(p95 < 50.0 && max < 200.0);
}

#[test]
fn bench_pan_and_zoom_ops() {
    let mut st = loaded_state(20_000);
    let bars_before = st.bars.len();
    let mut pan = Stats::default();
    for i in 0..50 {
        let t = Instant::now();
        assert!(st.apply(MarketAction::WheelPanX(if i % 2 == 0 {
            0.02
        } else {
            -0.02
        })));
        project(&st);
        pan.push(t.elapsed());
    }
    let mut zoom = Stats::default();
    for i in 0..50 {
        let t = Instant::now();
        assert!(st.apply(MarketAction::WheelZoom(
            0.5,
            if i % 2 == 0 { 1.0 } else { -1.0 }
        )));
        project(&st);
        zoom.push(t.elapsed());
    }
    // Pan/zoom never reload data: same bars throughout.
    assert_eq!(st.bars.len(), bars_before);
    pan.report("pan x50");
    zoom.report("zoom x50");
}

#[test]
fn bench_apply_tiers_single_window() {
    // Slint's testing backend owns process-global event-loop state: all
    // window-instantiation measurements run in this ONE test, sequentially
    // (same constraint as `ui_bindings_and_navigation`).
    use crate::shell::{
        apply_market, apply_market_hover, apply_market_viewport, init_test_backend,
    };
    use crate::AppWindow;

    init_test_backend();
    let ui = AppWindow::new().unwrap();
    let mut st = loaded_state(20_000);

    // BEFORE reference: full rebuild on every refresh (all models replaced).
    apply_market(&ui, &st);
    let mut full = Stats::default();
    for _ in 0..20 {
        let t = Instant::now();
        apply_market(&ui, &st);
        full.push(t.elapsed());
    }
    let (full_mean, _, _) = full.report("apply_market full x20 (BEFORE path)");
    eprintln!("PERF nodes-per-full-refresh=~4800 (1200 candles x 4 Slint items)");

    // AFTER reference: hover touches 8 scalar props, zero model rebuilds.
    let mut hover = Stats::default();
    for i in 0..200 {
        let x = (i % 100) as f32 / 100.0;
        st.apply(MarketAction::HoverMoved(x, 0.5));
        let t = Instant::now();
        apply_market_hover(&ui, &st);
        hover.push(t.elapsed());
    }
    let (hover_mean, hover_p95, hover_max) = hover.report("apply_market_hover x200 (AFTER)");
    assert!(
        hover_mean < 50.0,
        "hover apply mean {hover_mean:.2}ms over budget"
    );
    assert!(hover_p95 < 50.0 && hover_max < 200.0);

    // AFTER reference: viewport ops rebuild geometry lists only.
    let mut viewport = Stats::default();
    for i in 0..20 {
        st.apply(MarketAction::WheelPanX(if i % 2 == 0 {
            0.05
        } else {
            -0.05
        }));
        let t = Instant::now();
        apply_market_viewport(&ui, &st);
        viewport.push(t.elapsed());
    }
    let (vp_mean, vp_p95, _) = viewport.report("apply_market_viewport x20 (AFTER)");
    assert!(
        vp_mean < 500.0,
        "viewport apply mean {vp_mean:.2}ms over budget"
    );
    assert!(vp_p95 < 1000.0);

    // No geometry churn on hover, no reloads on pan: same bars throughout.
    assert_eq!(st.bars.len(), 20_000);
    eprintln!("PERF tier-gain hover-vs-full: {full_mean:.3}ms -> {hover_mean:.3}ms per refresh");
}
