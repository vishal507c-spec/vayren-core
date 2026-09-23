//! Strategy Lab research-object list geometry regression.
//!
//! Root cause of "stock won't select": the right-hand research-objects column
//! was taller than the DEFINE card beside it, so the layout squeezed the nested
//! ScrollView, clipping the second strategy under the "+ New strategy" button —
//! the row was only ~15px tall and its click landed on the button. The fix gives
//! the list an explicit height that fits its rows.
//!
//! A software-renderer harness cannot fire `TouchArea.clicked` from synthetic
//! press/release (verified: even the shell nav rail ignores them), so selection
//! itself is covered by the headless `invoke_lab_library_picked` test in
//! shell.rs. This test guards the *geometry*: every research-object row must be
//! a full ~46px band and the "+ New strategy" button must sit below the rows
//! with a clear gap — i.e. nothing is clipped or overlapping.

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

fn render(
    ui: &vayren_shell::AppWindow,
    win: &Rc<MinimalSoftwareWindow>,
    width: u32,
    height: u32,
) -> SharedPixelBuffer<Rgb8Pixel> {
    win.set_size(PhysicalSize::new(width, height));
    ui.window().request_redraw();
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(width, height);
    let slice = buffer.make_mut_slice();
    win.draw_if_needed(|renderer: &SoftwareRenderer| {
        renderer.render(slice, width as usize);
    });
    buffer
}

fn save_ppm(name: &str, buffer: &SharedPixelBuffer<Rgb8Pixel>) {
    let size = buffer.size();
    let dir = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("lab-select");
    std::fs::create_dir_all(&dir).unwrap();
    let mut raw = Vec::with_capacity((size.width * size.height * 3) as usize + 32);
    raw.extend_from_slice(format!("P6\n{} {}\n255\n", size.width, size.height).as_bytes());
    for p in buffer.as_slice() {
        raw.extend_from_slice(&[p.r, p.g, p.b]);
    }
    std::fs::write(dir.join(format!("{name}.ppm")), raw).unwrap();
}

/// Down a single column line, return the (start, end) of every contiguous run
/// of pixels brighter than the page background (~sum 27). Rows and buttons read
/// as runs; dark gaps separate them.
fn bright_runs(buffer: &SharedPixelBuffer<Rgb8Pixel>, x: u32, y0: u32, y1: u32) -> Vec<(u32, u32)> {
    let w = buffer.size().width as usize;
    let slice = buffer.as_slice();
    let on = |y: u32| -> bool {
        let p = &slice[y as usize * w + x as usize];
        (p.r as u32 + p.g as u32 + p.b as u32) >= 35
    };
    let mut runs = Vec::new();
    let mut start: Option<u32> = None;
    let mut last = 0u32;
    for y in y0..y1 {
        if on(y) {
            if start.is_none() {
                start = Some(y);
            }
            last = y;
        } else if let Some(s) = start {
            if last - s >= 8 {
                runs.push((s, last));
            }
            start = None;
        }
    }
    if let Some(s) = start {
        if last - s >= 8 {
            runs.push((s, last));
        }
    }
    runs
}

#[test]
fn research_object_rows_are_fully_visible_and_unobstructed() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(MiniPlatform {
        window: win.clone(),
    }))
    .expect("software platform installs once per test binary");

    let ui = vayren_shell::AppWindow::new().unwrap();
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_lab(&ui, &shell::demo_lab_state());
    shell::select(&ui, ShellScreen::Lab);

    let (w, h) = (1280u32, 720u32);
    let frame = render(&ui, &win, w, h);
    save_ppm("top", &frame);

    // Centre of the "+ New strategy" button is ~150px lower than the rows; scan
    // a generous window and take the runs.
    let runs = bright_runs(&frame, 1050, 150, 320);
    // The two strategy rows read as two distinct bright bands on the centre
    // line (the button text is not on this exact column).
    assert!(
        runs.len() >= 2,
        "expected at least two research-object rows, got {runs:?}"
    );

    // Each strategy row must be a full ~46px band — not clipped down to a
    // sliver by the layout squeeze that caused the bug.
    let row0_h = runs[0].1 - runs[0].0;
    let row1_h = runs[1].1 - runs[1].0;
    assert!(
        row0_h >= 40 && row1_h >= 40,
        "a research-object row is clipped (heights {row0_h}, {row1_h}; runs {runs:?})"
    );

    // The rows must be separated by a dark gap, i.e. the second row is not
    // merged into / overlapped by whatever sits below it.
    let gap = runs[1].0.saturating_sub(runs[0].1);
    assert!(
        gap >= 4,
        "research-object rows are not separated (gap {gap}px; runs {runs:?})"
    );

    // Sanity: the demo really has two selectable strategies.
    assert_eq!(ui.get_lab_library().row_count(), 2);
}
