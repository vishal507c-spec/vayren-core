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

fn count_near(buffer: &SharedPixelBuffer<Rgb8Pixel>, rgb: [u8; 3], tol: u8) -> usize {
    buffer
        .as_slice()
        .iter()
        .filter(|p| {
            p.r.abs_diff(rgb[0]) <= tol
                && p.g.abs_diff(rgb[1]) <= tol
                && p.b.abs_diff(rgb[2]) <= tol
        })
        .count()
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
    save_ppm(&out_dir().join(format!("{name}.ppm")), &buffer);
    buffer
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
    shell::apply_live(&ui, &shell::demo_live_state());
    shell::select(&ui, ShellScreen::Live);

    // â”€â”€ disconnected / NOT CONFIGURED desktop (the honest idle shape) â”€â”€
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
    ];
    for (name, w, h) in cases {
        let buffer = render_frame(&ui, &win, name, w, h);
        let px = (w * h) as usize;
        let surface = count_near(&buffer, [16, 23, 32], 6);
        let accent = count_near(&buffer, [0, 199, 183], 24);
        let neg = count_near(&buffer, [240, 90, 103], 24);
        let text =
            count_near(&buffer, [230, 237, 243], 26) + count_near(&buffer, [139, 152, 167], 18);
        // Panels paint (rail + command bar + section surfaces), the teal
        // identity/nav renders, the danger spine (NOT READY verdicts, HALT
        // control) renders, and real glyphs rasterize in every frame.
        assert!(surface > px / 400, "{name}: panels missing ({surface})");
        assert!(accent > px / 6000, "{name}: accent missing ({accent})");
        assert!(text > px / 1500, "{name}: text missing ({text})");
        // On short viewports the readiness panel scrolls below the fold and
        // an idle (never-faked-enabled) HALT button is neutral — so red is
        // only mandatory where the verdicts actually fit.
        if h >= 680 {
            assert!(neg > px / 12000, "{name}: danger tone missing ({neg})");
        }
        // No giant flat void: at least 3% of every frame is non-background.
        let bg = count_near(&buffer, [7, 11, 16], 3);
        assert!(bg < px * 97 / 100, "{name}: frame is a void ({bg}/{px})");
    }

    // â”€â”€ RUNNING session shape: tables, P&L band, halt armed â”€â”€
    shell::apply_live(&ui, &shell::demo_live_state_running());
    let running = render_frame(&ui, &win, "running_1920x1080", 1920, 1080);
    let px = (1920 * 1080) as usize;
    let green = count_near(&running, [33, 197, 139], 24); // POS: +P&L / ENABLED
    let neg = count_near(&running, [240, 90, 103], 24); // NEG: halt spine
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
    // The halt adds a quiet banner strip right under the command bar
    // (rows 36..70): its surface panel + 3px semantic left edge fill a band
    // the running frame leaves empty. (Global Banner = left-edge, never a
    // full-bleed block.)
    let strip_fill = |buffer: &SharedPixelBuffer<slint::Rgb8Pixel>| {
        let size = buffer.size();
        let w = size.width as usize;
        buffer
            .as_slice()
            .iter()
            .enumerate()
            .filter(|(i, p)| {
                let y = i / w;
                let x = i % w;
                // Banner's 3px semantic RED edge (left of the inspector
                // column) — the exact quiet-strip signature.
                y > 36
                    && y < 70
                    && x < 1000
                    && p.r.abs_diff(240) <= 30
                    && p.g.abs_diff(90) <= 30
                    && p.b.abs_diff(103) <= 30
            })
            .count()
    };
    let banner_band = strip_fill(&halt_shot);
    let plain_band = strip_fill(&running_1440);
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
    let bg_c = count_near(&c, [7, 11, 16], 3) as i64;
    let bg_o = count_near(&o, [7, 11, 16], 3) as i64;
    assert!(
        (bg_c - bg_o).abs() > 2000,
        "collapse/expand must recompose the frame, not just hide pixels"
    );
    println!("live render probes written to {}", out_dir().display());
}
