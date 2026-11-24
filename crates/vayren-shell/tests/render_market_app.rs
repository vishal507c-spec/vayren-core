//! Pixel renders of the REAL shell MARKET screen (AppWindow, software
//! renderer) from a captured backend snapshot — the exact surface a user
//! screenshots. Run:
//! `cargo test -p vayren-shell --test render_market_app -- --nocapture`
//!
//! Snapshot path: `VAYREN_MARKET_SNAP` env, else the shared temp capture.
//! Without a snapshot the test skips (never invents bars).

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, PremultipliedRgbaColor, RepaintBufferType, TargetPixel,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::ComponentHandle;
use std::rc::Rc;
use vayren_domain::market;
use vayren_shell::{AppWindow, ShellScreen};

#[derive(Copy, Clone, Default, PartialEq, Eq)]
struct Rgb8Pixel {
    r: u8,
    g: u8,
    b: u8,
}

impl TargetPixel for Rgb8Pixel {
    fn blend(&mut self, color: PremultipliedRgbaColor) {
        let a = (u8::MAX - color.alpha) as u16;
        self.r = (self.r as u16 * a / 255) as u8 + color.red;
        self.g = (self.g as u16 * a / 255) as u8 + color.green;
        self.b = (self.b as u16 * a / 255) as u8 + color.blue;
    }

    fn from_rgb(r: u8, g: u8, b: u8) -> Self {
        Rgb8Pixel { r, g, b }
    }

    fn background() -> Self {
        Rgb8Pixel::from_rgb(0, 0, 0)
    }
}

struct SoftwarePlatform {
    window: Rc<MinimalSoftwareWindow>,
}

impl Platform for SoftwarePlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        Ok(self.window.clone())
    }
}

fn save_bmp(path: &std::path::Path, pixels: &[Rgb8Pixel], width: u32, height: u32) {
    let stride = ((width * 3 + 3) / 4) * 4;
    let mut data = vec![0u8; (stride * height) as usize];
    for y in 0..height {
        for x in 0..width {
            let p = &pixels[(y * width + x) as usize];
            let out = ((height - 1 - y) * stride + x * 3) as usize;
            data[out] = p.b;
            data[out + 1] = p.g;
            data[out + 2] = p.r;
        }
    }
    let file_size = 54 + data.len() as u32;
    let mut header = vec![0u8; 54];
    header[0] = b'B';
    header[1] = b'M';
    header[2..6].copy_from_slice(&file_size.to_le_bytes());
    header[10] = 54;
    header[14] = 40;
    header[18..22].copy_from_slice(&width.to_le_bytes());
    header[22..26].copy_from_slice(&height.to_le_bytes());
    header[26] = 1;
    header[28] = 24;
    let mut file = std::fs::File::create(path).unwrap();
    use std::io::Write;
    file.write_all(&header).unwrap();
    file.write_all(&data).unwrap();
}

#[test]
fn render_market_screen_from_real_snapshot() {
    let path = std::env::var("VAYREN_MARKET_SNAP").unwrap_or_else(|_| {
        r"C:\Users\visha\AppData\Local\Temp\opencode\real_market_snap.json".to_string()
    });
    let json = match std::fs::read_to_string(&path) {
        Ok(j) => j,
        Err(_) => {
            eprintln!("SKIP: no market snapshot at {path}");
            return;
        }
    };
    let value: serde_json::Value = serde_json::from_str(&json).unwrap();
    let mut state = market::MarketState::default();
    market::apply_snapshot_json(&mut state, &value);
    assert!(state.has_data(), "snapshot must carry real bars");

    let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(SoftwarePlatform {
        window: window.clone(),
    }))
    .expect("software platform");
    let out = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("target")
        .join("render-market");
    std::fs::create_dir_all(&out).unwrap();

    let ui = AppWindow::new().unwrap();
    vayren_shell::shell::apply_market(&ui, &state);
    vayren_shell::shell::select(&ui, ShellScreen::Chart);

    for (width, height) in [(1600u32, 900u32), (1280, 720)] {
        window.set_size(slint::LogicalSize::new(width as f32, height as f32));
        ui.window().request_redraw();
        slint::platform::update_timers_and_animations();
        let mut buffer = vec![Rgb8Pixel::default(); (width * height) as usize];
        let mut painted = false;
        window.draw_if_needed(|renderer| {
            renderer.render(&mut buffer, width as usize);
            painted = true;
        });
        assert!(painted, "nothing painted at {width}x{height}");
        let path = out.join(format!("market_{width}x{height}.bmp"));
        save_bmp(&path, &buffer, width, height);
        println!("rendered {}", path.display());
    }
}
