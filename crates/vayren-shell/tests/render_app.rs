//! Pixel renders of the REAL shell window (AppWindow, software renderer).
//!
//! The harness bypasses shell chrome; this binary renders the actual
//! application window (rail + content + status bar) with RESEARCH selected,
//! which is what a user screenshots. Run:
//! `cargo test -p vayren-shell --test render_app`.

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, PremultipliedRgbaColor, RepaintBufferType, TargetPixel,
};
use slint::platform::{Platform, PlatformError, WindowAdapter};
use slint::ComponentHandle;
use std::rc::Rc;
use vayren_shell::lab::Tone;
use vayren_shell::research_state::{
    ExperimentItem, Metric, ResearchResults, ResearchRun, ResearchState, ResearchStrategy,
    RobustRow, SignalRow,
};
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

fn completed_state() -> ResearchState {
    let mut state = ResearchState {
        strategies: vec![ResearchStrategy {
            name: "OBR".into(),
            description: "Opening Breakout Strategy".into(),
            version: "1.0".into(),
        }],
        ..ResearchState::default()
    };
    state.selected = Some(0);
    state.hypothesis = "OBR opening momentum persists intraday.".into();
    state.question = "Does OBR work in the opening session?".into();
    assert!(state.create());
    state.engine_wired = true;
    assert!(state.start_run());
    state.apply_result(ResearchResults {
        experiment_id: "EXP-03774B".into(),
        metrics: vec![
            Metric {
                label: "NET P&L".into(),
                value: "-₹32,277.93".into(),
                tone: Tone::Negative,
                emphasized: true,
            },
            Metric {
                label: "TRADES".into(),
                value: "37".into(),
                tone: Tone::Neutral,
                emphasized: false,
            },
            Metric {
                label: "SIGNALS".into(),
                value: "82".into(),
                tone: Tone::Neutral,
                emphasized: false,
            },
        ],
        signal_total: 82,
        trade_total: 37,
        signals: vec![SignalRow {
            time: "2024-01-02 09:15:00".into(),
            symbol: "RELIANCE".into(),
            tf: "5m".into(),
            side: "BUY".into(),
            price: "2451.10".into(),
            event: "BUY".into(),
            strategy: "OBR".into(),
            exp: "EXP-03774B".into(),
        }],
        trades: vec![],
        robustness: vec![RobustRow {
            test: "parameter_sensitivity".into(),
            input: "refIndex=3.0".into(),
            stability: "WARNING".into(),
            stability_tone: Tone::Warning,
            evidence: "exp 12.40 → 11.90".into(),
        }],
        validation_status: "FAIL".into(),
        validation_summary: "WEAK; OOS WARNING".into(),
        conclusion: "Performance is unstable across tested dimensions.".into(),
        fingerprint_line: "result f39d958be429 · config d4d6b182bf71".into(),
        executed_at: "2026-09-13T10:00:00".into(),
        ..ResearchResults::default()
    });
    state.experiments = vec![ExperimentItem {
        id: "EXP-03774B".into(),
        strategy: "OBR".into(),
        status_label: "✓ COMPLETED".into(),
        status_tone: Tone::Positive,
    }];
    state.selected_experiment = Some(0);
    assert_eq!(state.run, ResearchRun::Completed);
    state
}

#[test]
fn render_app_research() {
    let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(SoftwarePlatform {
        window: window.clone(),
    }))
    .expect("software platform");
    let out = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("target")
        .join("render-app");
    std::fs::create_dir_all(&out).unwrap();

    let ui = AppWindow::new().unwrap();
    let research = Rc::new(std::cell::RefCell::new(completed_state()));
    vayren_shell::shell::wire_research(&ui, research.clone());
    vayren_shell::shell::apply_research(&ui, &research.borrow());
    vayren_shell::shell::select(&ui, ShellScreen::Research);

    for (name, width, height) in [
        ("research", 1920, 1080),
        ("research", 1366, 768),
        ("research", 1280, 720),
    ] {
        window.set_size(slint::LogicalSize::new(width as f32, height as f32));
        ui.window().request_redraw();
        slint::platform::update_timers_and_animations();
        let mut buffer = vec![Rgb8Pixel::default(); (width * height) as usize];
        let mut painted = false;
        window.draw_if_needed(|renderer| {
            renderer.render(&mut buffer, width as usize);
            painted = true;
        });
        assert!(painted, "nothing painted for {name} {width}x{height}");
        let path = out.join(format!("{name}_{width}x{height}.bmp"));
        save_bmp(&path, &buffer, width, height);
        println!("rendered {}", path.display());
    }
}
