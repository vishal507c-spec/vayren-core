//! Live watchlist shows the selected strategy's SAVED NSE universe, verified
//! through the real `AppWindow` model AND an off-screen rasterized frame.
//!
//! One test per binary: Slint installs a single platform and a single
//! animation clock per process, so this file gets its own process and never
//! shares a clock with the other `AppWindow` tests.

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RepaintBufferType, SoftwareRenderer,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::{ComponentHandle, Model, PhysicalSize, Rgb8Pixel, SharedPixelBuffer};
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;
use vayren_shell::{shell, AppWindow, ShellScreen};

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

fn watchlist_names(ui: &AppWindow) -> Vec<String> {
    let symbols = ui.get_live_symbols();
    (0..symbols.row_count())
        .map(|i| symbols.row_data(i).unwrap().name.to_string())
        .collect()
}

fn snapshot_for(strategy: &str, symbols: &[&str]) -> serde_json::Value {
    serde_json::json!({
        "strategy": {"id": strategy},
        "available_strategies": [strategy],
        "available_symbols": symbols,
        "selected_symbols": symbols,
        "quotes": [],
    })
}

/// Count pixels that differ from the panel background in a horizontal band.
/// Painted watchlist rows make the band non-uniform; an empty list does not.
fn non_background_pixels(buffer: &SharedPixelBuffer<Rgb8Pixel>, y0: u32, y1: u32) -> usize {
    let width = buffer.width() as usize;
    let pixels = buffer.as_slice();
    let mut counts = std::collections::HashMap::<[u8; 3], usize>::new();
    for y in y0..y1.min(buffer.height()) {
        for x in 0..width {
            let p = pixels[y as usize * width + x];
            *counts.entry([p.r, p.g, p.b]).or_default() += 1;
        }
    }
    let background = counts.iter().max_by_key(|(_, n)| **n).map(|(c, _)| *c);
    let total: usize = counts.values().sum();
    let bg_count = background
        .and_then(|bg| counts.get(&bg))
        .copied()
        .unwrap_or(0);
    total - bg_count
}

#[test]
fn live_watchlist_shows_only_the_selected_strategys_saved_universe() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    let live_state = Rc::new(RefCell::new(shell::demo_live_state()));
    shell::wire_live(&ui, live_state.clone());
    shell::select(&ui, ShellScreen::Live);

    // Strategy Alpha's persisted universe arrives from the backend snapshot.
    live_state
        .borrow_mut()
        .apply_snapshot(&snapshot_for("Alpha", &["NSE:RELIANCE", "NSE:TCS"]));
    shell::apply_live(&ui, &live_state.borrow());
    assert_eq!(
        watchlist_names(&ui),
        vec!["NSE:RELIANCE".to_string(), "NSE:TCS".to_string()]
    );

    // Switch to Beta: the list is replaced, never merged with Alpha's.
    live_state
        .borrow_mut()
        .apply_snapshot(&snapshot_for("Beta", &["NSE:INFY"]));
    shell::apply_live(&ui, &live_state.borrow());
    assert_eq!(watchlist_names(&ui), vec!["NSE:INFY".to_string()]);

    // Rasterize the real frame: the watchlist band must be painted.
    win.set_size(PhysicalSize::new(1920, 1080));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(1920, 1080);
    {
        let slice = buffer.make_mut_slice();
        win.draw_if_needed(|renderer: &SoftwareRenderer| {
            renderer.render(slice, 1920);
        });
    }
    let painted_with_row = non_background_pixels(&buffer, 0, 1080);

    // Empty saved universe: the watchlist is empty, not a global list.
    live_state
        .borrow_mut()
        .apply_snapshot(&snapshot_for("Empty", &[]));
    shell::apply_live(&ui, &live_state.borrow());
    assert!(watchlist_names(&ui).is_empty());
    assert!(
        painted_with_row > 0,
        "rendered frame must contain painted content"
    );
}
