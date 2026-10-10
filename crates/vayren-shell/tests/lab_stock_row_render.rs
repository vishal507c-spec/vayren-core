//! Strategy Lab LIVE TRADE STOCKS row â€” rendered through the real `AppWindow`.
//!
//! One test per binary (Slint installs one platform and one animation clock
//! per process). Asserts the model the row binds to, then rasterizes the real
//! Lab screen and saves a PPM so the row can be inspected by eye.

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

fn save_ppm(name: &str, buffer: &SharedPixelBuffer<Rgb8Pixel>) -> std::path::PathBuf {
    let size = buffer.size();
    let dir = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("lab-stock-row");
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

fn saved_chips(ui: &vayren_shell::AppWindow) -> Vec<String> {
    let chips = ui.get_lab().saved_universe_chips;
    let chips: slint::ModelRc<slint::SharedString> = chips;
    (0..chips.row_count())
        .map(|i| chips.row_data(i).unwrap().to_string())
        .collect()
}

#[test]
fn lab_stock_row_renders_saved_chips_count_and_state() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    let mut state = shell::demo_lab_state();
    state.saved_universe_symbols = vec!["NSE:RELIANCE".into(), "NSE:TCS".into(), "NSE:INFY".into()];
    state.saved_universe_status = lab::SavedUniverseStatus::Ok;
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &state);
    shell::select(&ui, ShellScreen::Lab);

    let view = lab::project(&state);
    assert_eq!(view.universe_count_line, "3 stocks saved");
    assert_eq!(view.saved_universe_chips.len(), 3);

    // Model the row binds to must match the Rust projection exactly.
    assert_eq!(
        saved_chips(&ui),
        vec!["NSE:RELIANCE", "NSE:TCS", "NSE:INFY"]
    );

    win.set_size(PhysicalSize::new(1280, 2400));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(1280, 2400);
    {
        let slice = buffer.make_mut_slice();
        win.draw_if_needed(|renderer: &SoftwareRenderer| {
            renderer.render(slice, 1280);
        });
    }
    let path = save_ppm("saved-three", &buffer);
    eprintln!("rendered Lab stock row frame: {}", path.display());
}

#[test]
fn lab_stock_row_renders_empty_state_when_nothing_is_saved() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    let _ = slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }));

    let ui = vayren_shell::AppWindow::new().unwrap();
    let mut state = shell::demo_lab_state();
    state.saved_universe_symbols.clear();
    state.saved_universe_status = lab::SavedUniverseStatus::Empty;
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &state);
    shell::select(&ui, ShellScreen::Lab);

    assert!(saved_chips(&ui).is_empty());
    assert_eq!(ui.get_lab().universe_count_line, "No stocks saved");

    win.set_size(PhysicalSize::new(1280, 2400));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(1280, 2400);
    {
        let slice = buffer.make_mut_slice();
        win.draw_if_needed(|renderer: &SoftwareRenderer| {
            renderer.render(slice, 1280);
        });
    }
    save_ppm("empty", &buffer);
}

#[test]
fn lab_stock_row_empty_state_has_no_chips() {
    let mut state = shell::demo_lab_state();
    state.saved_universe_symbols.clear();
    state.saved_universe_status = lab::SavedUniverseStatus::Empty;
    let view = lab::project(&state);
    assert_eq!(view.universe_count_line, "No stocks saved");
    assert!(view.saved_universe_chips.is_empty());
}
