//! LIVE screen LIVE TRADE STOCKS row — rendered through the real `AppWindow`.
//!
//! Asserts the model the Live stock row binds to (exchange, chip count, symbols,
//! refresh action queueing), then rasterizes the real Live screen and saves PPMs
//! to verify the UI layout offscreen.

use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RepaintBufferType, SoftwareRenderer,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::{ComponentHandle, Model, PhysicalSize, Rgb8Pixel, SharedPixelBuffer};
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

fn save_ppm(name: &str, buffer: &SharedPixelBuffer<Rgb8Pixel>) -> std::path::PathBuf {
    let size = buffer.size();
    let dir = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("live-stock-row");
    std::fs::create_dir_all(&dir).unwrap();
    let mut raw = Vec::with_capacity((size.width * size.height * 3) as usize + 32);
    raw.extend_from_slice(format!("P6\n{} {}\n255\n", size.width, size.height).as_bytes());
    for p in buffer.as_slice() {
        raw.extend_from_slice(&[p.r, p.g, p.b]);
    }
    let path = dir.join(format!("{name}.ppm"));
    std::fs::write(&path, raw).unwrap();
    path
}

fn live_chips(ui: &vayren_shell::AppWindow) -> Vec<String> {
    let chips = ui.get_live_setup().stock_universe;
    (0..chips.row_count())
        .map(|i| chips.row_data(i).unwrap().to_string())
        .collect()
}

#[test]
fn live_stock_row_renders_twenty_two_chips_and_exchange() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    let live_state = Rc::new(RefCell::new(shell::demo_live_state()));
    shell::apply(&ui, &shell::demo_snapshot());
    shell::wire_live(&ui, live_state.clone());
    shell::select(&ui, ShellScreen::Live);

    let symbols: Vec<&str> = vec![
        "NSE:ABB", "NSE:ADANIENT", "NSE:ADANIPORTS", "NSE:APOLLOHOSP",
        "NSE:ASIANPAINT", "NSE:AXISBANK", "NSE:BAJAJ-AUTO", "NSE:BAJFINANCE",
        "NSE:BAJAJFINSV", "NSE:BPCL", "NSE:BHARTIARTL", "NSE:BRITANNIA",
        "NSE:CIPLA", "NSE:COALINDIA", "NSE:DIVISLAB", "NSE:DRREDDY",
        "NSE:EICHERMOT", "NSE:GRASIM", "NSE:HCLTECH", "NSE:HDFCBANK",
        "NSE:HDFCLIFE", "NSE:HEROMOTOCO",
    ];

    // Backend snapshot arrives with OBR C1C4, 22 saved stocks, and connected broker capital
    live_state.borrow_mut().apply_snapshot(&serde_json::json!({
        "strategy": {"id": "OBR C1C4"},
        "available_strategies": ["OBR C1C4", "EMA Crossover"],
        "available_symbols": symbols,
        "selected_symbols": symbols,
        "capital": {
            "broker_name": "FYERS",
            "available_margin": 540250.0,
            "currency": "INR",
            "status": "CONNECTED",
            "label": "Available margin",
            "last_updated": "10:15:30"
        }
    }));
    shell::apply_live(&ui, &live_state.borrow());

    let chips = live_chips(&ui);
    assert_eq!(chips.len(), 22);
    assert_eq!(chips[0], "NSE:ABB");
    assert_eq!(chips[21], "NSE:HEROMOTOCO");
    assert_eq!(ui.get_live_setup().universe_exchange, "NSE");
    assert_eq!(ui.get_live_strategy_selected(), 0);

    let bar = ui.get_live_bar();
    assert_eq!(bar.capital_broker_name, "FYERS");
    assert_eq!(bar.capital_value, "₹5,40,250.00");
    assert_eq!(bar.capital_status, "CONNECTED");
    assert_eq!(bar.capital_tone, 1);
    assert_eq!(bar.capital_last_updated, "10:15:30");

    // Verify stock refresh button invokes wire callback and queues host action
    while live_state.borrow_mut().take_action().is_some() {}
    ui.invoke_live_refresh_stocks();
    let action = live_state.borrow_mut().take_action();
    assert!(action.is_some(), "invoking refresh-stocks must queue an action");
    let parsed: serde_json::Value = serde_json::from_str(&action.unwrap()).unwrap();
    assert_eq!(parsed["action"], "refresh");

    // Verify broker capital refresh button invokes wire callback and queues host action
    ui.invoke_broker_capital_refresh();
    let cap_action = live_state.borrow_mut().take_action();
    assert!(cap_action.is_some(), "invoking broker-capital-refresh must queue an action");
    let cap_parsed: serde_json::Value = serde_json::from_str(&cap_action.unwrap()).unwrap();
    assert_eq!(cap_parsed["action"], "refresh_capital");

    // Render offscreen frame with 22 chips
    win.set_size(PhysicalSize::new(1920, 1080));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(1920, 1080);
    {
        let slice = buffer.make_mut_slice();
        win.draw_if_needed(|renderer: &SoftwareRenderer| {
            renderer.render(slice, 1920);
        });
    }
    let path = save_ppm("saved-twenty-two", &buffer);
    eprintln!("rendered Live stock row frame (22 stocks): {}", path.display());

    // Switch to empty strategy: verify empty state renders
    live_state.borrow_mut().apply_snapshot(&serde_json::json!({
        "strategy": {"id": "EMA Crossover"},
        "available_strategies": ["OBR C1C4", "EMA Crossover"],
        "available_symbols": [],
        "selected_symbols": [],
    }));
    shell::apply_live(&ui, &live_state.borrow());

    let empty_chips = live_chips(&ui);
    assert!(empty_chips.is_empty());
    assert_eq!(ui.get_live_strategy_selected(), 1);

    ui.window().request_redraw();
    {
        let slice = buffer.make_mut_slice();
        win.draw_if_needed(|renderer: &SoftwareRenderer| {
            renderer.render(slice, 1920);
        });
    }
    let empty_path = save_ppm("empty-live", &buffer);
    eprintln!("rendered Live stock row frame (0 stocks): {}", empty_path.display());
}
