//! Market-status strip render proof (headless software rasterizer).
//!
//! The strip is the legacy `MarketStatusPanel` grid: market-regime facts +
//! data-status facts. Rust projects them; the screen must actually PAINT
//! them. This renders the real `AppWindow` on the chart screen three times —
//! strip closed, strip open, strip closed again — and requires the surface
//! band, the group titles and the fact glyphs to exist ONLY in the open
//! frames. A strip that stays invisible (bound but never rendered) fails here.

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RepaintBufferType, SoftwareRenderer,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::{ComponentHandle, PhysicalSize, Rgb8Pixel, SharedPixelBuffer};
use std::rc::Rc;
use vayren_shell::{market, shell, ShellScreen};

struct MiniPlatform {
    window: Rc<MinimalSoftwareWindow>,
}

impl Platform for MiniPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        Ok(self.window.clone())
    }
}

/// Real backend payload shape: a candle series plus market-status facts.
fn snapshot_json() -> String {
    let mut bars = String::new();
    for i in 0..80 {
        let close = 100.0 + (i as f64 * 0.7).sin() * 6.0;
        bars.push_str(&format!(
            "{{\"time\":\"2024-01-02T09:{:02}:00\",\"open\":{:.2},\"high\":{:.2},\
             \"low\":{:.2},\"close\":{:.2},\"volume\":{}}},",
            i % 60,
            close - 1.0,
            close + 2.0,
            close - 2.0,
            close,
            500000 + i * 1000
        ));
    }
    format!(
        "{{\"symbols\":[{{\"symbol\":\"TEST\",\"price\":112.4,\"change_pct\":2.31}}],\
         \"selected_symbol\":\"TEST\",\"watchlists\":[\"All Stocks\"],\
         \"active_watchlist\":\"All Stocks\",\"timeframes\":[\"15m\",\"1h\"],\
         \"timeframe\":\"15m\",\"exchange\":\"NSE\",\"status\":\"ready\",\
         \"indicators\":{{}},\"bars\":[{}],\
         \"market_status\":{{\"regime_current\":\"TRENDING UP\",\
         \"regime_trend\":\"0.62\",\"regime_volatility\":\"LOW\",\
         \"regime_momentum\":\"RISING\",\"provider\":\"FYERS\",\
         \"latency\":\"12 ms\",\"last_update\":\"09:41\",\
         \"bars_loaded\":\"1400\"}}}}",
        bars.trim_end_matches(',')
    )
}

fn market_state(open: bool) -> market::MarketState {
    let mut state = market::MarketState::default();
    let json = serde_json::from_str(&snapshot_json()).unwrap();
    market::apply_snapshot_json(&mut state, &json);
    state.market_status.open = open;
    state
}

fn render_frame(
    ui: &vayren_shell::AppWindow,
    win: &Rc<MinimalSoftwareWindow>,
    w: u32,
    h: u32,
) -> SharedPixelBuffer<Rgb8Pixel> {
    win.set_size(PhysicalSize::new(w, h));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(w, h);
    {
        let slice = buffer.make_mut_slice();
        win.draw_if_needed(|renderer: &SoftwareRenderer| {
            renderer.render(slice, w as usize);
        });
    }
    buffer
}

/// Count pixels matching a palette role inside the band the strip occupies:
/// the bottom fifth of the chart column.
fn band_pixels(buffer: &SharedPixelBuffer<Rgb8Pixel>, target: [u8; 3], tol: u8) -> usize {
    let (w, h) = (buffer.width() as usize, buffer.height() as usize);
    let stride = w;
    let y0 = h * 80 / 100;
    buffer
        .as_slice()
        .iter()
        .enumerate()
        .filter(|(i, p)| {
            let y = i / stride;
            y >= y0
                && p.r.abs_diff(target[0]) <= tol
                && p.g.abs_diff(target[1]) <= tol
                && p.b.abs_diff(target[2]) <= tol
        })
        .count()
}

/// Palette roles proven by the strip: surface, faint eyebrow, bright value.
const SURFACE: [u8; 3] = [16, 23, 32];
const FAINT: [u8; 3] = [89, 102, 117];
const TEXT: [u8; 3] = [230, 237, 243];

fn probe(buffer: &SharedPixelBuffer<Rgb8Pixel>) -> (usize, usize, usize) {
    (
        band_pixels(buffer, SURFACE, 2),
        band_pixels(buffer, FAINT, 10),
        band_pixels(buffer, TEXT, 10),
    )
}

#[test]
fn market_status_strip_paints_only_when_toggled_open() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::select(&ui, ShellScreen::Chart);

    // ── closed (default): the strip's surface band is absent ──
    shell::apply_market(&ui, &market_state(false));
    let closed = render_frame(&ui, &win, 1280, 720);
    let (closed_surface, closed_faint, closed_text) = probe(&closed);

    // ── open: same facts, strip on ──
    shell::apply_market(&ui, &market_state(true));
    let open = render_frame(&ui, &win, 1280, 720);
    let (open_surface, open_faint, open_text) = probe(&open);

    // The strip paints its own surface, the group titles (faint) and real
    // text glyphs; the closed frame has none of the three in that band.
    assert!(
        open_surface > closed_surface + 20000,
        "strip surface band missing (closed={closed_surface}, open={open_surface})"
    );
    assert!(
        open_text > 200 && closed_text < 50,
        "fact values must paint as real glyphs (closed={closed_text}, open={open_text})"
    );
    assert!(
        open_faint > closed_faint + 500,
        "group titles / captions must paint (closed={closed_faint}, open={open_faint})"
    );

    // ── closed again: the strip must disappear, not stay latched ──
    shell::apply_market(&ui, &market_state(false));
    let again = render_frame(&ui, &win, 1280, 720);
    let (again_surface, _, again_text) = probe(&again);
    assert!(
        again_surface < open_surface / 4 && again_text < 50,
        "strip must vanish when toggled closed (surface={again_surface}, text={again_text})"
    );

    // ── the honest unknown state: every value "--" still renders ──
    let mut unknown = market_state(true);
    unknown.market_status.regime_current = "--".to_string();
    unknown.market_status.latency = "--".to_string();
    unknown.market_status.bars_loaded = "--".to_string();
    shell::apply_market(&ui, &unknown);
    let unknown_frame = render_frame(&ui, &win, 1280, 720);
    let (unknown_surface, unknown_faint, _) = probe(&unknown_frame);
    assert!(
        unknown_surface > 20000 && unknown_faint > 500,
        "the honest-unknown strip must still paint its rows"
    );

    // ── medium tier: the cells squeeze and elide, the strip still paints ──
    shell::apply_market(&ui, &market_state(true));
    let medium = render_frame(&ui, &win, 1024, 640);
    let (medium_surface, medium_faint, _) = probe(&medium);
    assert!(
        medium_surface > 5000 && medium_faint > 100,
        "strip must survive the medium tier (surface={medium_surface}, faint={medium_faint})"
    );
}
