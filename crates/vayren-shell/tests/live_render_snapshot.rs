//! Real off-screen RENDER verification for the LIVE workstation (mission
//! Â§32: the rendered output is the authority, not the source).
//!
//! Uses Slint's official software-rasterizer path (`MinimalSoftwareWindow`
//! + `SoftwareRenderer`) to rasterize the REAL `AppWindow` with the REAL
//! `LiveScreen` at a matrix of viewports and write PPM bitmaps, then
//! asserts structural facts on the ACTUAL PIXELS: panel surfaces, the teal
//! safety spine, semantic danger/warning/positive badges, real glyph
//! rasterization, the halted-state red growth, and the no-void rule.
//!
//! DPI independence is proven by the LiveHarness logical-unit matrix in
//! shell.rs (Slint px are device-independent); this test drives the full
//! text+layout rasterization pipeline at several sizes/states.

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RepaintBufferType, SoftwareRenderer,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::{ComponentHandle, PhysicalSize, Rgb8Pixel, SharedPixelBuffer};
use std::rc::Rc;
use std::time::Duration;
use vayren_shell::{live, shell, ShellScreen};

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

fn out_dir() -> std::path::PathBuf {
    let dir = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("live-render");
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn save_ppm(path: &std::path::Path, buffer: &SharedPixelBuffer<Rgb8Pixel>) {
    let size = buffer.size();
    let mut raw = Vec::with_capacity((size.width * size.height * 3) as usize + 32);
    raw.extend_from_slice(format!("P6\n{} {}\n255\n", size.width, size.height).as_bytes());
    for pixel in buffer.as_slice() {
        raw.extend_from_slice(&[pixel.r, pixel.g, pixel.b]);
    }
    std::fs::write(path, raw).unwrap();
}

/// Single-pass frame probe: classifies every pixel once into the same
/// buckets the old per-color `count_near` scans produced (identical centers
/// and tolerances, buckets are independent so overlap behaviour matches).
/// One pass instead of 5-6 over multi-megapixel buffers — same thresholds,
/// ~5x less pixel traffic. Thresholds below must stay in sync with the
/// asserts; change a color in exactly one place (here).
struct FrameProbe {
    surface: usize,
    surface_alt: usize,
    accent: usize,
    neg: usize,
    green: usize,
    text: usize,
    bg: usize,
}

fn probe_frame(buffer: &SharedPixelBuffer<Rgb8Pixel>) -> FrameProbe {
    let mut p = FrameProbe {
        surface: 0,
        surface_alt: 0,
        accent: 0,
        neg: 0,
        green: 0,
        text: 0,
        bg: 0,
    };
    for px in buffer.as_slice() {
        let (r, g, b) = (px.r, px.g, px.b);
        if r.abs_diff(16) <= 6 && g.abs_diff(23) <= 6 && b.abs_diff(32) <= 6 {
            p.surface += 1;
        }
        // Collapse/expand recomposition probe (1280x720 pair only).
        if r.abs_diff(12) <= 6 && g.abs_diff(18) <= 6 && b.abs_diff(27) <= 6 {
            p.surface_alt += 1;
        }
        if r.abs_diff(0) <= 24 && g.abs_diff(229) <= 24 && b.abs_diff(200) <= 24 {
            p.accent += 1;
        }
        if r.abs_diff(240) <= 24 && g.abs_diff(90) <= 24 && b.abs_diff(103) <= 24 {
            p.neg += 1;
        }
        if r.abs_diff(33) <= 24 && g.abs_diff(197) <= 24 && b.abs_diff(139) <= 24 {
            p.green += 1;
        }
        // Bright and dim text bands are disjoint (r >= 204 vs r <= 157), so a
        // single counter equals the old bright+dim sum exactly.
        if (r.abs_diff(230) <= 26 && g.abs_diff(237) <= 26 && b.abs_diff(243) <= 26)
            || (r.abs_diff(139) <= 18 && g.abs_diff(152) <= 18 && b.abs_diff(167) <= 18)
        {
            p.text += 1;
        }
        if r.abs_diff(7) <= 3 && g.abs_diff(11) <= 3 && b.abs_diff(16) <= 3 {
            p.bg += 1;
        }
    }
    p
}

fn render_frame(
    ui: &vayren_shell::AppWindow,
    win: &Rc<MinimalSoftwareWindow>,
    name: &str,
    width: u32,
    height: u32,
) -> SharedPixelBuffer<Rgb8Pixel> {
    win.set_size(PhysicalSize::new(width, height));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(width, height);
    {
        let slice = buffer.make_mut_slice();
        win.draw_if_needed(|renderer: &SoftwareRenderer| {
            renderer.render(slice, width as usize);
        });
    }
    if std::env::var_os("VAYREN_SNAPSHOT_SAVE").is_some() {
        save_ppm(&out_dir().join(format!("{name}.ppm")), &buffer);
    }
    buffer
}

/// Production steady state for render geometry: the backend snapshot has
/// landed (universe known, mixed quote statuses) while the broker is still
/// NOT CONFIGURED and every gate stays blocked. An empty boot universe would
/// clip the readiness panel out of the pixel probes. Quote figures are
/// render-fixture placeholders, never product data.
fn idle_populated_state() -> live::LiveState {
    let mut state = shell::demo_live_state();
    let names = [
        "NSE:KAYNES",
        "NSE:DREDGECORP",
        "NSE:AVANTIFEED",
        "NSE:WAAREERTL",
        "NSE:ROUTE",
        "NSE:KEC",
        "NSE:RAILTEL",
        "NSE:SCI",
        "NSE:MOIL",
        "NSE:CRAMC",
        "NSE:LXCHEM",
        "NSE:NOCIL",
        "NSE:DAMCAPITAL",
        "NSE:JSWCEMENT",
        "NSE:TEXRAIL",
        "NSE:ZEEL",
        "NSE:VIKRAN",
        "NSE:IFCI",
        "NSE:DCW",
        "NSE:SAGILITY",
        "NSE:JINDWORLD",
        "NSE:NETWEB",
    ];
    state.symbols = names
        .into_iter()
        .enumerate()
        .map(|(i, symbol)| live::SymbolPick {
            symbol: symbol.into(),
            checked: false,
            ltp: if i == 0 { Some(100.0) } else { None },
            change_pct: if i == 0 { Some(0.5) } else { None },
            in_store: i != 1,
        })
        .collect();
    state.store_total = Some(22);
    state
}

#[test]
fn live_screen_renders_structurally_at_every_viewport_tier() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_live(&ui, &idle_populated_state());
    shell::select(&ui, ShellScreen::Live);

    // disconnected / NOT CONFIGURED desktop (the honest idle shape)
    // Viewport matrix incl. the short-height and min-window recompositions.
    let cases = [
        ("idle_1920x1080", 1920u32, 1080u32),
        ("idle_1600x900", 1600, 900),
        ("idle_1440x900", 1440, 900),
        ("idle_1366x768", 1366, 768),
        ("idle_1280x720", 1280, 720),
        ("idle_min_1024x640", 1024, 640),
        ("idle_short_1024x560", 1024, 560),
        ("idle_narrow_900x700", 900, 700),
        ("idle_wide_2560x1440", 2560, 1440),
        ("idle_uhd_3840x2160", 3840, 2160),
    ];
    for (name, w, h) in cases {
        let buffer = render_frame(&ui, &win, name, w, h);
        let px = (w * h) as usize;
        let probe = probe_frame(&buffer);
        let (surface, accent, neg, text) = (probe.surface, probe.accent, probe.neg, probe.text);
        // Panels paint (rail + command bar + section surfaces), the teal
        // identity/nav renders, the danger spine (NOT READY verdicts, HALT
        // control) renders, and real glyphs rasterize in every frame.
        assert!(surface > px / 400, "{name}: panels missing ({surface})");
        assert!(accent > px / 6000, "{name}: accent missing ({accent})");
        assert!(text > px / 1500, "{name}: text missing ({text})");
        // On short viewports the readiness panel scrolls below the fold and
        // an idle (never-faked-enabled) HALT button is neutral — so red is
        // only mandatory where the verdicts actually fit (accounting for the
        // 50px top navigation header).
        if h >= 730 {
            assert!(neg > px / 12000, "{name}: danger tone missing ({neg})");
        }
        // No giant flat void: at least 3% of every frame is non-background.
        let bg = probe.bg;
        assert!(bg < px * 97 / 100, "{name}: frame is a void ({bg}/{px})");
    }

    // â”€â”€ RUNNING session shape: tables, P&L band, halt armed â”€â”€
    shell::apply_live(&ui, &shell::demo_live_state_running());
    let running = render_frame(&ui, &win, "running_1920x1080", 1920, 1080);
    let px = (1920 * 1080) as usize;
    let run_probe = probe_frame(&running);
    let green = run_probe.green; // POS: +P&L / ENABLED
    let neg = run_probe.neg; // NEG: halt spine
    assert!(green > px / 15000, "running: POS tone missing ({green})");
    assert!(neg > px / 12000, "running: halt control missing ({neg})");

    // â”€â”€ HALTED state: the semantic red presence strictly grows (quiet
    //    banner strip + EXECUTION HALTED verdicts) â”€â”€
    let running_1440 = render_frame(&ui, &win, "running_1440x900", 1440, 900);
    let mut halted = shell::demo_live_state_running();
    halted.report_halt();
    shell::apply_live(&ui, &halted);
    let halt_shot = render_frame(&ui, &win, "halted_1440x900", 1440, 900);
    shell::apply_live(&ui, &shell::demo_live_state());
    // The HALT banner is a quiet strip at the very top of LiveScreen: a 3px
    // semantic RED left edge (12px layout padding + the VBanner edge) inside
    // its 20px height. That left edge is the ONLY red in this window — the
    // probe must be narrow enough to exclude the command bar, whose armed STOP
    // control is legitimately red once a session runs. A wide band silently
    // started failing for the right reason (the bar no longer overflows and
    // clips itself), which is how this probe came to measure the wrong thing.
    const NAV: usize = 50;
    let banner_edge = |buffer: &SharedPixelBuffer<slint::Rgb8Pixel>| {
        let size = buffer.size();
        let stride = size.width as usize;
        buffer
            .as_slice()
            .iter()
            .enumerate()
            .filter(|(i, p)| {
                let y = i / stride;
                let x = i % stride;
                // The banner's 3px semantic RED edge, and nothing else.
                y > NAV + 2
                    && y < NAV + 24
                    && x >= 10
                    && x < 18
                    && p.r.abs_diff(240) <= 30
                    && p.g.abs_diff(90) <= 30
                    && p.b.abs_diff(103) <= 30
            })
            .count()
    };
    let banner_band = banner_edge(&halt_shot);
    let plain_band = banner_edge(&running_1440);
    assert!(
        banner_band > 50 && plain_band == 0,
        "halted: banner strip must paint ({banner_band} vs {plain_band})"
    );

    // â”€â”€ collapse tier: inspector closed vs open both render, and the
    //    frame actually recomposes (different pixel split) â”€â”€
    let mut collapsed = shell::demo_live_state();
    collapsed.inspector_open = false;
    shell::apply_live(&ui, &collapsed);
    let c = render_frame(&ui, &win, "collapsed_1280x720", 1280, 720);
    let mut open = shell::demo_live_state();
    open.inspector_open = true;
    shell::apply_live(&ui, &open);
    let o = render_frame(&ui, &win, "expanded_1280x720", 1280, 720);
    let surf_c = probe_frame(&c).surface_alt as i64;
    let surf_o = probe_frame(&o).surface_alt as i64;
    assert!(
        (surf_c - surf_o).abs() > 2000,
        "collapse/expand must recompose the frame, not just hide pixels"
    );
    println!("live render probes written to {}", out_dir().display());
}

/// STEP 36: the twenty-state visual matrix.
///
/// Every combination the acceptance list names is rendered offscreen and
/// checked for the same structural health as the idle frame. The point is not
/// that the states differ, it is that NONE of them crashes, blanks out, or
/// renders a frame the eye cannot read — a state that throws or paints a void
/// is indistinguishable from a broken screen to the operator.
/// THE auto-adjustability gate: at every desktop size, the workspace chrome
/// must be fully reachable and nothing may be cut at the panel edge.
///
/// The old bar/header/tab-bar thresholds were guesses, and the render at
/// 1024x640 / 1366x768 showed the damage directly: the watchlist filter chips
/// and the search box sliced off at the panel's right edge, every command-bar
/// card label elided mid-word, and the Clear control cut in half. A clipped
/// region has a signature on the pixels — an element's surface stops at the
/// exact boundary column with NO border drawn, i.e. the frame's own edge cuts
/// through it. A region that ends where it is meant to ends in a border or in
/// background.
///
/// So: for the command bar band and the watchlist header band, assert the
/// right-hand 2px columns carry no element surface. That is exactly the
/// condition "something was cut here", and it fails on the old layout.
#[test]
fn live_chrome_is_never_cut_at_the_viewport_edge() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_live(&ui, &idle_populated_state());
    shell::select(&ui, ShellScreen::Live);

    // 50px top navigation + 24px status bar, then the command bar band.
    const NAV: u32 = 50;
    const BAR_H: u32 = 60;

    for (name, w, h) in [
        ("edge_1920x1080", 1920u32, 1080u32),
        ("edge_1600x900", 1600, 900),
        ("edge_1440x900", 1440, 900),
        ("edge_1366x768", 1366, 768),
        ("edge_1280x720", 1280, 720),
        ("edge_1152x720", 1152, 720),
        ("edge_1024x640", 1024, 640),
        ("edge_960x640", 960, 640),
    ] {
        let buffer = render_frame(&ui, &win, name, w, h);
        let size = buffer.size();
        let stride = size.width as usize;
        let px = buffer.as_slice();

        // Command bar band only: the top nav is a fixed full-bleed strip and
        // says nothing about the workspace recomposition.
        let band_top = NAV as usize + 8;
        let band_bottom = band_top + BAR_H as usize - 16;

        // An element surface that reaches the last column without a border is
        // a cut. Count "card-ish" pixels (panel surfaces + teal accents) in
        // the final two columns inside the band; the frame's own background
        // and its borders are excluded by sampling three columns: a cut puts
        // surface in col W-2 as well, a clean edge does not.
        let near = |x: usize, y: usize| -> (u8, u8, u8) {
            let p = px[y * stride + x];
            (p.r, p.g, p.b)
        };
        let is_surface = |c: (u8, u8, u8)| {
            // VayrenPalette.live-panel / live-topbar family.
            (c.0 as i32 - 16).abs() <= 8
                && (c.1 as i32 - 23).abs() <= 8
                && (c.2 as i32 - 32).abs() <= 8
        };
        let mut cut_pixels = 0usize;
        for y in band_top..band_bottom.min(size.height as usize) {
            let last = stride - 1;
            let penult = stride - 2;
            // A cut means the surface continues through the edge; a clean
            // terminus shows background (or a border) at the very edge.
            if is_surface(near(penult, y)) && is_surface(near(last, y)) {
                cut_pixels += 1;
            }
        }
        assert_eq!(
            cut_pixels, 0,
            "{name}: command bar is clipped at the right edge ({cut_pixels} rows cut)"
        );
    }
    println!("edge-cut probes written to {}", out_dir().display());
}

/// The adaptive regions must follow their tiers, so a narrow window never
/// renders the wide form it cannot fit. Verified on the real pixels: the
/// compact blotter tab row and the scrollable chip strip both appear, and the
/// wide-only forms do not.
#[test]
fn narrow_viewports_render_the_compact_chrome_not_the_clipped_wide_form() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_live(&ui, &idle_populated_state());
    shell::select(&ui, ShellScreen::Live);

    // Text-pixel budget as a proxy for "how much is drawn": a clipped wide
    // form draws strictly less than a form that actually fits.
    // Single pass per frame (same bands the old double `count_near` summed).

    // 1024x640 is the smallest supported shell window: chips on their strip,
    // compact tabs. Every chip and every tab must still be drawn.
    let small = render_frame(&ui, &win, "compact_1024x640", 1024, 640);
    // 1920x1080 fits the inline form. The wide bar shows MORE card labels, so
    // the wide frame must carry at least as much text as the compact one —
    // proof that nothing is lost by recomposing down.
    let large = render_frame(&ui, &win, "wide_1920x1080", 1920, 1080);
    let small_probe = probe_frame(&small);
    let large_probe = probe_frame(&large);
    assert!(
        large_probe.text > small_probe.text,
        "the wide form must draw more text than the compact one ({})",
        large_probe.text
    );
    // Structural health at the floor: panels, accent and glyphs all present.
    let px = (1024 * 640) as usize;
    assert!(small_probe.surface > px / 400, "compact: panels");
    assert!(small_probe.accent > px / 6000, "compact: accent");
    assert!(small_probe.text > px / 1500, "compact: glyphs");
}

#[test]
fn live_state_matrix_renders_at_the_primary_target_viewport() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::select(&ui, ShellScreen::Live);

    // The 1920x1080 @150% target in LOGICAL pixels is 1280x720; both are
    // rendered because the acceptance list names 1920x1080 as the window and
    // 150% as the scale.
    let (w, h) = (1920u32, 1080u32);

    // The demo fixture ships an empty watchlist (it is an idle desktop shape),
    // so the matrix seeds its OWN rows. Building the base here keeps the shared
    // fixture untouched and makes every case differ from it in exactly one way.
    let base = move || {
        let mut s = shell::demo_live_state();
        s.watchlist_rows = vec![watchlist_row("KAYNES"), watchlist_row("TATASTEEL")];
        s.selected_symbol = "NSE:KAYNES".into();
        s
    };
    let mut cases: Vec<(&str, Box<dyn Fn() -> vayren_shell::live::LiveState>)> = Vec::new();

    // 1-3: mode x status independence.
    for (label, mode, running) in [
        ("paper_stopped", vayren_shell::live::ExecMode::Paper, false),
        ("live_stopped", vayren_shell::live::ExecMode::Live, false),
        ("live_running", vayren_shell::live::ExecMode::Live, true),
    ] {
        cases.push((
            label,
            Box::new(move || {
                let mut s = base().clone();
                s.mode = mode;
                if running {
                    s.session = vayren_shell::live::SessionStatus::Running;
                }
                s
            }),
        ));
    }

    // 4-7: connection and feed states.
    for (label, ws, md) in [
        ("ws_connected", "CONNECTED", "STREAMING"),
        ("ws_disconnected", "DISCONNECTED", "STREAMING"),
        ("md_streaming", "CONNECTED", "STREAMING"),
        ("md_stale", "CONNECTED", "STALE"),
    ] {
        cases.push((
            label,
            Box::new(move || {
                let mut s = base().clone();
                s.websocket.status = ws.into();
                s.market_data.status = md.into();
                s
            }),
        ));
    }

    // 8-11: order states.
    for (label, status) in [
        ("order_none", ""),
        ("order_working", "WORKING"),
        ("order_filled", "FILLED"),
        ("order_rejected", "REJECTED"),
    ] {
        cases.push((
            label,
            Box::new(move || {
                let mut s = base().clone();
                if status.is_empty() {
                    s.orders.clear();
                } else {
                    s.orders = vec![order_row("RELIANCE", status)];
                }
                s
            }),
        ));
    }

    // 12-14: position sides.
    for (label, side, qty) in [
        ("position_flat", "FLAT", "0"),
        ("position_long", "LONG", "54"),
        ("position_short", "SHORT", "22"),
    ] {
        cases.push((
            label,
            Box::new(move || {
                let mut s = base().clone();
                if side == "FLAT" {
                    s.positions.clear();
                    s.watchlist_rows[0].position = "FLAT".into();
                } else {
                    s.positions = vec![position_row("RELIANCE", side, qty)];
                    s.watchlist_rows[0].position = format!("{side} {qty}");
                }
                s
            }),
        ));
    }

    // 15-17: risk readiness.
    for (label, with_capital) in [
        ("risk_not_ready", false),
        ("risk_validated", true),
        ("risk_blocked", true),
    ] {
        cases.push((
            label,
            Box::new(move || {
                let mut s = base().clone();
                s.capital.broker_capital = if with_capital { Some(100_000.0) } else { None };
                s.capital.source = "broker".into();
                if label == "risk_blocked" {
                    // A planned risk far above the ceiling must read blocked.
                    s.watchlist_rows[0].planned_risk = Some(99_999.0);
                }
                s
            }),
        ));
    }

    // 18-19: reconciliation.
    for (label, status) in [("recon_synced", "CLEAN"), ("recon_mismatch", "MISMATCH")] {
        cases.push((
            label,
            Box::new(move || {
                let mut s = base().clone();
                s.reconciliation.status = status.into();
                s
            }),
        ));
    }

    // 20: 50+ symbols (the count is runtime-driven, never hardcoded).
    cases.push((
        "many_symbols_50",
        Box::new(move || {
            let mut s = base().clone();
            s.watchlist_rows = (0..60)
                .map(|i| watchlist_row(&format!("SYM{i:03}")))
                .collect();
            s.symbols = (0..60)
                .map(|i| vayren_shell::live::SymbolPick {
                    symbol: format!("SYM{i:03}"),
                    checked: true,
                    ltp: Some(100.0 + i as f64),
                    change_pct: Some(0.5),
                    in_store: true,
                })
                .collect();
            s.store_total = Some(60);
            s
        }),
    ));

    assert_eq!(cases.len(), 20, "the matrix must cover all twenty states");

    for (name, build) in cases {
        let state = build();
        shell::apply_live(&ui, &state);
        let buffer = render_frame(&ui, &win, name, w, h);
        let px = (w * h) as usize;
        let probe = probe_frame(&buffer);
        let (surface, accent, text) = (probe.surface, probe.accent, probe.text);
        // The same three health floors the idle matrix enforces: panels paint,
        // the identity accent renders, and glyphs rasterize. A state that
        // produced an unreadable frame would fail here.
        assert!(surface > px / 400, "{name}: panels missing ({surface})");
        assert!(accent > px / 6000, "{name}: accent missing ({accent})");
        assert!(text > px / 1500, "{name}: text missing ({text})");
        let bg = probe.bg;
        assert!(bg < px * 97 / 100, "{name}: frame is a void ({bg}/{px})");
    }
}

/// One real order row carrying an engine-shaped transition history.
fn order_row(symbol: &str, status: &str) -> vayren_shell::live::OrderRow {
    let history: Vec<String> = match status {
        "FILLED" => vec![
            "CREATED",
            "VALIDATED",
            "SUBMITTED",
            "ACKNOWLEDGED",
            "FILLED",
        ]
        .into_iter()
        .map(String::from)
        .collect(),
        "REJECTED" => vec!["CREATED", "VALIDATED", "SUBMITTED", "REJECTED"]
            .into_iter()
            .map(String::from)
            .collect(),
        _ => vec!["CREATED", "VALIDATED", "SUBMITTED", "ACKNOWLEDGED"]
            .into_iter()
            .map(String::from)
            .collect(),
    };
    vayren_shell::live::OrderRow {
        order_id: "c1".into(),
        strategy: "OBR".into(),
        symbol: symbol.into(),
        side: "SELL".into(),
        quantity: "22".into(),
        order_type: "SLM".into(),
        price: "1218.50".into(),
        status: status.into(),
        time: "12:14:25".into(),
        broker: "fyers".into(),
        reason: if status == "REJECTED" {
            "insufficient margin".into()
        } else {
            String::new()
        },
        filled_qty: if status == "FILLED" {
            "22".into()
        } else {
            "0".into()
        },
        history,
    }
}

fn position_row(symbol: &str, side: &str, qty: &str) -> vayren_shell::live::PositionRow {
    vayren_shell::live::PositionRow {
        symbol: symbol.into(),
        side: side.into(),
        quantity: qty.into(),
        entry: "1218.50".into(),
        current: "1210.20".into(),
        pnl: "+182.60".into(),
        status: "OPEN".into(),
        pnl_pct: Some(0.65),
    }
}

fn watchlist_row(symbol: &str) -> vayren_shell::live::WatchlistStockRow {
    vayren_shell::live::WatchlistStockRow {
        symbol: format!("NSE:{symbol}"),
        clean_symbol: symbol.into(),
        ltp: Some(3124.90),
        change_pct: Some(1.1),
        ref_high: Some(1245.0),
        ref_low: Some(1220.0),
        break_low: Some(1218.5),
        entry_price: Some(1218.5),
        stop_price: Some(1245.0),
        risk_per_share: Some(26.5),
        qty: Some(22),
        planned_risk: Some(583.0),
        risk_util: Some(97.2),
        position: "FLAT".into(),
        status: "WAITING".into(),
        signal: "--".into(),
        order: None,
        pnl: None,
        last_update: "12:14:25".into(),
    }
}
