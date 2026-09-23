//! Strategy Lab page-scroll verification (software renderer, off-screen).
//!
//! The whole workspace must scroll: wheel events over the page drive the
//! ScrollView to its bottom (Modify + footer fully visible), and scrolling
//! back up restores the original frame. Pixels, not scrollbars, are the
//! proof.

use std::rc::Rc;
use std::time::Duration;

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RepaintBufferType, SoftwareRenderer,
};
use slint::platform::{Platform, PlatformError, WindowAdapter, WindowEvent};
use slint::{ComponentHandle, LogicalPosition, PhysicalSize, Rgb8Pixel, SharedPixelBuffer};
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

fn pixel(buffer: &SharedPixelBuffer<Rgb8Pixel>, x: u32, y: u32) -> [u8; 3] {
    let p = &buffer.as_slice()[(y as usize * buffer.size().width as usize + x as usize)];
    [p.r, p.g, p.b]
}

fn differing_pixels(a: &SharedPixelBuffer<Rgb8Pixel>, b: &SharedPixelBuffer<Rgb8Pixel>) -> usize {
    a.as_slice()
        .iter()
        .zip(b.as_slice())
        .filter(|(x, y)| x.r.abs_diff(y.r) > 2 || x.g.abs_diff(y.g) > 2 || x.b.abs_diff(y.b) > 2)
        .count()
}

fn save_ppm(name: &str, buffer: &SharedPixelBuffer<Rgb8Pixel>) {
    let size = buffer.size();
    let dir = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("lab-scroll");
    std::fs::create_dir_all(&dir).unwrap();
    let mut raw = Vec::with_capacity((size.width * size.height * 3) as usize + 32);
    raw.extend_from_slice(format!("P6\n{} {}\n255\n", size.width, size.height).as_bytes());
    for p in buffer.as_slice() {
        raw.extend_from_slice(&[p.r, p.g, p.b]);
    }
    std::fs::write(dir.join(format!("{name}.ppm")), raw).unwrap();
}

fn wheel(win: &Rc<MinimalSoftwareWindow>, at: LogicalPosition, delta_y: f32) {
    let _ = win.dispatch_event(WindowEvent::PointerMoved { position: at });
    let _ = win.dispatch_event(WindowEvent::PointerScrolled {
        position: at,
        delta_x: 0.0,
        delta_y,
    });
}

#[test]
fn lab_workspace_scrolls_to_footer_and_back() {
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
    let top = render(&ui, &win, w, h);
    save_ppm("top", &top);

    // Cursor over the page's left padding (positions are window-absolute:
    // shell nav rail is 104px wide, then 26px of page padding follows).
    // Nothing interactive ever sits under this strip, so the wheel always
    // reaches the page ScrollView.
    let at = LogicalPosition::new(117.0, 300.0);

    // Discover which wheel sign scrolls the page down (platform delta
    // conventions differ); the opposite sign must then scroll it back.
    wheel(&win, at, -240.0);
    let probe = render(&ui, &win, w, h);
    let down = if differing_pixels(&top, &probe) > 0 {
        -240.0
    } else {
        240.0
    };

    // Scroll down far past the end of the document.
    for _ in 0..40 {
        wheel(&win, at, down);
    }
    let bottom = render(&ui, &win, w, h);
    save_ppm("bottom", &bottom);
    let moved = differing_pixels(&top, &bottom);
    assert!(
        moved * 20 > (w * h) as usize,
        "content barely moved: {moved} differing pixels"
    );

    // Past the document end the view saturates — more wheel does nothing.
    for _ in 0..10 {
        wheel(&win, at, down);
    }
    let saturated = render(&ui, &win, w, h);
    assert!(
        differing_pixels(&bottom, &saturated) < 200,
        "bottom is not a scroll limit"
    );

    // At the bottom the page's final 24px spacer must sit fully visible at
    // the content-area bottom (the shell's own 24px status bar is fixed
    // chrome below it): a uniform lab-bg band, i.e. footer is on screen.
    let lab_bg = [6u8, 9, 12];
    let mut offbg = 0usize;
    for y in (h - 24 - 14)..(h - 24 - 4) {
        for x in (300..900).step_by(5) {
            let p = pixel(&bottom, x, y);
            if p[0].abs_diff(lab_bg[0]) > 3
                || p[1].abs_diff(lab_bg[1]) > 3
                || p[2].abs_diff(lab_bg[2]) > 3
            {
                offbg += 1;
            }
        }
    }
    assert!(offbg < 10, "bottom spacer band not visible at window bottom");

    // Back to the top: the original frame must return.
    for _ in 0..80 {
        wheel(&win, at, -down);
    }
    let back = render(&ui, &win, w, h);
    save_ppm("back", &back);
    let residue = differing_pixels(&top, &back);
    assert!(
        residue * 200 < (w * h) as usize,
        "upward scroll did not restore the top: {residue} px differ"
    );
}
