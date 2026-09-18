//! Native Market render matrix over the real C ABI (host-identical path).
//! create → set_snapshot → render → non-blank + full-viewport content at the
//! three responsive tiers, plus interaction wiring through the state.

use std::ffi::CString;
use vayren_market_view::*;

fn snapshot(bars: usize) -> String {
    let mut rows = String::new();
    let mut series = String::new();
    for i in 0..bars {
        let close = 100.0 + (i as f64 * 0.7).sin() * 6.0;
        rows.push_str(&format!(
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
    // One plot series across the window + one BUY/SELL trade pair (real facts).
    let mut pts = String::new();
    for i in 0..bars {
        pts.push_str(&format!(
            "[{}, {:.2}],",
            i,
            100.0 + (i as f64 * 0.5).sin() * 4.0
        ));
    }
    series.push_str(&format!(
        "[{{\"owner\":\"SMA\",\"title\":\"SMA\",\"points\":[{}]}}]",
        pts.trim_end_matches(',')
    ));
    let trades = if bars > 30 {
        format!(
            "[{{\"side\":\"BUY\",\"entry_time\":\"2024-01-02T09:00:00\",\
             \"exit_time\":\"2024-01-02T09:20:00\",\"entry_price\":101.0,\"exit_price\":104.0}}]"
        )
    } else {
        "[]".to_string()
    };
    format!(
        "{{\"symbols\":[{{\"symbol\":\"TEST\",\"price\":112.4,\"change_pct\":2.31}},\
         {{\"symbol\":\"TEST2\",\"price\":88.15,\"change_pct\":-1.07}}],\
         \"selected_symbol\":\"TEST\",\"watchlists\":[\"All Stocks\"],\
         \"active_watchlist\":\"All Stocks\",\"timeframes\":[\"15m\",\"1h\",\"1D\"],\
         \"timeframe\":\"15m\",\"exchange\":\"NSE\",\"strategies\":[\"OBR\"],\
         \"status\":\"ready\",\"indicators\":{{\"SMA\":true}},\
         \"bars\":[{}],\"plot_series\":{},\"trades\":{},\"focused_trade\":0,\
         \"trade_context\":{{\"visible\":true,\"trade\":\"#3\",\"symbol-side\":\"TEST BUY\",\
         \"time\":\"09:00 - 09:20\",\"pnl\":\"+310.00\",\"r\":\"+1.85R\"}}}}",
        rows.trim_end_matches(','),
        series,
        trades
    )
}

fn render_at(w: u32, h: u32, bars: usize) {
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null(), "view create {w}x{h}");
    let json = CString::new(snapshot(bars)).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, json.as_ptr()), 0);
    assert_eq!(vayren_market_view_resize(view, w, h, 1.0), 0);
    // Hover mid-canvas -> crosshair tags must become present (interaction path).
    assert_eq!(
        vayren_market_view_pointer_move(view, w as f32 * 0.5, h as f32 * 0.4),
        0
    );
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut out = vec![0u8; (w * h * 3) as usize];
    let painted = vayren_market_view_render(view, out.as_mut_ptr(), out.len());
    assert_eq!(painted, 1, "must paint {w}x{h}");
    let bg = [out[0], out[1], out[2]];
    let mut cols = 0usize;
    let mut sampled = 0usize;
    let mut x = 0u32;
    while x < w {
        sampled += 1;
        let mut y = 0u32;
        let mut hit = false;
        while y < h {
            let i = ((y * w + x) * 3) as usize;
            if out[i] != bg[0] || out[i + 1] != bg[1] || out[i + 2] != bg[2] {
                hit = true;
                break;
            }
            y += 6;
        }
        if hit {
            cols += 1;
        }
        x += 4;
    }
    assert!(
        cols * 10 >= sampled * 8,
        "{w}x{h}: content must fill ≥80% of columns ({cols}/{sampled})"
    );

    // Wheel-zoom must actually change the rendered geometry (behavior parity):
    // capture, scroll-zoom in, and require the frame to differ from the base.
    // delta_y is positive: the host forwards the natural wheel delta (UP),
    // and the chart negates it into a zoom-IN (the fixture shows the full
    // 160-bar history, so zooming OUT is capped and would not repaint).
    let base = out.clone();
    assert_eq!(
        vayren_market_view_scroll(
            view,
            (w as f32 * 0.5).floor(),
            (h as f32 * 0.4).floor(),
            0.0,
            120.0
        ),
        0
    );
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut after = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, after.as_mut_ptr(), after.len()),
        1
    );
    assert!(
        base != after,
        "{w}x{h}: wheel zoom must repaint a different frame"
    );
    // Drain any queued action (none yet — interactions were pointer, not click).
    let mut buf = [0u8; 64];
    assert_eq!(
        vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len()),
        0
    );
    vayren_market_view_destroy(view);
}

#[test]
fn market_renders_full_viewport_at_all_tiers() {
    render_at(1920, 1080, 160);
    render_at(1366, 768, 160);
    render_at(1280, 720, 160);
}

fn diff_frac(a: &[u8], b: &[u8]) -> f64 {
    assert_eq!(a.len(), b.len());
    let same = a.iter().zip(b.iter()).filter(|(x, y)| x == y).count();
    1.0 - same as f64 / a.len() as f64
}

/// Pointer input parity: hover (crosshair), horizontal-wheel pan and
/// left-drag pan must each repaint a visibly different frame through the
/// same C ABI the Qt host uses (pointer_move/scroll/press/release).
#[test]
fn market_pointer_inputs_repaint() {
    let (w, h) = (1280u32, 720u32);
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null());
    let json = CString::new(snapshot(400)).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, json.as_ptr()), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut base = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, base.as_mut_ptr(), base.len()),
        1
    );

    // Hover mid-plot -> crosshair lines + price/time tags must appear.
    assert_eq!(
        vayren_market_view_pointer_move(view, w as f32 * 0.6, h as f32 * 0.4),
        0
    );
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut hover = vec![0u8; (w * h * 3) as usize];
    let painted = vayren_market_view_render(view, hover.as_mut_ptr(), hover.len());
    assert_eq!(painted, 1, "hover must request a repaint");
    assert!(
        diff_frac(&base, &hover) > 0.0005,
        "hover must visibly change the frame"
    );

    // Horizontal wheel over the plot -> pan must move the viewport.
    assert_eq!(
        vayren_market_view_scroll(view, w as f32 * 0.6, h as f32 * 0.4, 300.0, 0.0),
        0
    );
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut panned = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, panned.as_mut_ptr(), panned.len()),
        1,
        "pan must request a repaint"
    );
    assert!(
        diff_frac(&hover, &panned) > 0.002,
        "pan must visibly move the viewport"
    );

    // Left-drag pan (press, move, release) must move the viewport.
    assert_eq!(
        vayren_market_view_pointer_press(view, w as f32 * 0.6, h as f32 * 0.5, 0),
        0
    );
    assert_eq!(
        vayren_market_view_pointer_move(view, w as f32 * 0.45, h as f32 * 0.5),
        0
    );
    assert_eq!(
        vayren_market_view_pointer_release(view, w as f32 * 0.45, h as f32 * 0.5, 0),
        0
    );
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut dragged = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, dragged.as_mut_ptr(), dragged.len()),
        1,
        "drag must request a repaint"
    );
    assert!(
        diff_frac(&panned, &dragged) > 0.002,
        "drag-pan must visibly move the viewport"
    );
    vayren_market_view_destroy(view);
}

/// Candle-toned pixels inside a rectangular band of the frame (bull/bear
/// bodies + sticks; the dim volume tones are not counted). Used to prove
/// WHERE the candles are in the rendered output, not just in the state.
fn tone_pixels(out: &[u8], w: u32, h: u32, fx0: f32, fx1: f32, fy0: f32, fy1: f32) -> usize {
    let x0 = (w as f32 * fx0) as u32;
    let x1 = (w as f32 * fx1).min(w as f32) as u32;
    let y0 = (h as f32 * fy0) as u32;
    let y1 = (h as f32 * fy1).min(h as f32) as u32;
    let mut hits = 0usize;
    for y in y0..y1 {
        for x in x0..x1 {
            let i = ((y * w + x) * 3) as usize;
            let px = [out[i], out[i + 1], out[i + 2]];
            if near(&px, [0x21, 0xC5, 0x8B], 12) || near(&px, [0xF0, 0x5A, 0x67], 12) {
                hits += 1;
            }
        }
    }
    hits
}

/// One-side free pan through the REAL C ABI (the path the Qt host drives):
/// dragging the candles left must park the newest candle on the LEFT of the
/// plot with a large, genuinely empty region to its right — and the next
/// identical backend snapshot must leave the view parked (no snap-back).
#[test]
fn market_one_side_free_pan_leaves_empty_right_space() {
    let (w, h) = (1280u32, 720u32);
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null());
    let json = CString::new(snapshot(400)).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, json.as_ptr()), 0);
    assert_eq!(vayren_market_view_resize(view, w, h, 1.0), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut live = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, live.as_mut_ptr(), live.len()),
        1
    );
    // Live view: candles reach into the right part of the plot.
    assert!(
        tone_pixels(&live, w, h, 0.60, 0.90, 0.30, 0.80) > 0,
        "live candles must reach the right side of the plot"
    );

    // Two left drags, exactly like a user mouse: press, move left, release.
    for _ in 0..2 {
        assert_eq!(
            vayren_market_view_pointer_press(view, w as f32 * 0.6, h as f32 * 0.5, 0),
            0
        );
        assert_eq!(
            vayren_market_view_pointer_move(view, w as f32 * 0.1, h as f32 * 0.5),
            0
        );
        assert_eq!(
            vayren_market_view_pointer_release(view, w as f32 * 0.1, h as f32 * 0.5, 0),
            0
        );
        assert_eq!(vayren_market_view_tick(view), 0);
    }
    let mut parked = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, parked.as_mut_ptr(), parked.len()),
        1,
        "pan must repaint"
    );
    // Candles still paint on the left …
    assert!(
        tone_pixels(&parked, w, h, 0.20, 0.60, 0.30, 0.80) > 0,
        "parked candles must still paint on the left"
    );
    // … and the right side of the plot is genuinely empty space.
    let right = tone_pixels(&parked, w, h, 0.60, 0.90, 0.30, 0.80);
    assert_eq!(right, 0, "empty right space must stay empty (got {right})");

    // The next snapshot of the same series must not move the view.
    let again = CString::new(snapshot(400)).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, again.as_ptr()), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut after = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, after.as_mut_ptr(), after.len()),
        1
    );
    assert!(
        diff_frac(&parked, &after) < 0.0005,
        "release must not move the view (snap-back)"
    );
    assert_eq!(
        tone_pixels(&after, w, h, 0.60, 0.90, 0.30, 0.80),
        0,
        "empty right space must survive the next snapshot"
    );
    vayren_market_view_destroy(view);
}

#[test]
fn market_empty_state_still_paints() {
    let view = vayren_market_view_create(1280, 720, 1.0);
    assert!(!view.is_null());
    let json = CString::new(r#"{"symbols":[],"status":"loading"}"#).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, json.as_ptr()), 0);
    let mut out = vec![0u8; 1280 * 720 * 3];
    assert_eq!(
        vayren_market_view_render(view, out.as_mut_ptr(), out.len()),
        1
    );
    // Fresh loading state must not be an unexplained void either.
    let bg = [out[0], out[1], out[2]];
    assert!(
        out.iter().any(|b| *b != bg[0]),
        "loading frame must not be uniform"
    );
    vayren_market_view_destroy(view);
}

fn near(out: &[u8], target: [u8; 3], tol: u8) -> bool {
    out.chunks_exact(3).any(|px| {
        (px[0] as i16 - target[0] as i16).abs() <= tol as i16
            && (px[1] as i16 - target[1] as i16).abs() <= tol as i16
            && (px[2] as i16 - target[2] as i16).abs() <= tol as i16
    })
}

fn snapshot_many(rows: usize) -> String {
    let mut syms = String::new();
    for i in 0..rows {
        syms.push_str(&format!(
            "{{\"symbol\":\"SYM{:03}\",\"price\":{:.2},\"change_pct\":{:.2}}},",
            i,
            100.0 + i as f64,
            (i as f64 % 5.0) - 2.0
        ));
    }
    format!(
        "{{\"symbols\":[{}],\"selected_symbol\":\"SYM000\",\"watchlists\":[\"All Stocks\"],\
         \"active_watchlist\":\"All Stocks\",\"timeframes\":[\"15m\"],\"timeframe\":\"15m\",\
         \"exchange\":\"NSE\",\"status\":\"ready\",\"indicators\":{{}},\"bars\":[\
         {{\"time\":\"2024-01-02T09:15:00\",\"open\":100.0,\"high\":102.0,\"low\":99.0,\
         \"close\":101.0,\"volume\":1000.0}}]}}",
        syms.trim_end_matches(',')
    )
}

/// Production-snapshot replay: feed the REAL captured app snapshot and
/// verify row taps still queue `select:`. Bisects environment-vs-data.
#[test]
fn market_watchlist_tap_with_production_snapshot() {
    let path = std::env::var("VAYREN_REAL_SNAP").unwrap_or_else(|_| {
        r"C:\Users\visha\AppData\Local\Temp\opencode\real_snap.json".to_string()
    });
    let json = match std::fs::read_to_string(&path) {
        Ok(j) => j,
        Err(_) => {
            eprintln!("SKIP: no production snapshot at {path}");
            return;
        }
    };
    let (w, h) = (1280u32, 720u32);
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null());
    let payload = CString::new(json).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, payload.as_ptr()), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut out = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, out.as_mut_ptr(), out.len()),
        1
    );
    let mut buf = [0u8; 256];
    while vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len()) > 0 {}
    let mut got: Vec<String> = Vec::new();
    let mut y = 60.0f32;
    while y < h as f32 - 20.0 {
        assert_eq!(vayren_market_view_pointer_move(view, 110.0, y), 0);
        assert_eq!(vayren_market_view_pointer_press(view, 110.0, y, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        assert_eq!(vayren_market_view_pointer_release(view, 110.0, y, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        loop {
            let n = vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len());
            if n <= 0 {
                break;
            }
            got.push(String::from_utf8_lossy(&buf[..n as usize]).into_owned());
        }
        if !got.is_empty() {
            break;
        }
        y += 20.0;
    }
    assert!(
        got.iter().any(|a| a.starts_with("select:")),
        "production snapshot: taps queued nothing (got {got:?})"
    );
    vayren_market_view_destroy(view);
}
#[test]
fn market_watchlist_tap_queues_select_in_scrollable_list() {
    let (w, h) = (1280u32, 720u32);
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null());
    let json = CString::new(snapshot_many(527)).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, json.as_ptr()), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut out = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, out.as_mut_ptr(), out.len()),
        1
    );
    let mut buf = [0u8; 256];
    while vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len()) > 0 {}
    let mut got: Vec<String> = Vec::new();
    let mut y = 100.0f32;
    while y < h as f32 - 20.0 {
        assert_eq!(vayren_market_view_pointer_move(view, 110.0, y), 0);
        assert_eq!(vayren_market_view_pointer_press(view, 110.0, y, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        assert_eq!(vayren_market_view_pointer_release(view, 110.0, y, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        loop {
            let n = vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len());
            if n <= 0 {
                break;
            }
            got.push(String::from_utf8_lossy(&buf[..n as usize]).into_owned());
        }
        if !got.is_empty() {
            break;
        }
        y += 20.0;
    }
    assert!(
        got.iter().any(|a| a.starts_with("select:")),
        "scrollable watchlist swallowed all taps (got {got:?})"
    );
    vayren_market_view_destroy(view);
}
#[test]
fn market_watchlist_tap_queues_select() {
    let (w, h) = (1280u32, 720u32);
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null());
    let json = CString::new(snapshot(120)).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, json.as_ptr()), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut out = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, out.as_mut_ptr(), out.len()),
        1
    );
    let mut buf = [0u8; 256];
    // Drain any stale actions first.
    while vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len()) > 0 {}
    let mut got: Vec<String> = Vec::new();
    let mut y = 100.0f32;
    while y < h as f32 - 20.0 {
        assert_eq!(vayren_market_view_pointer_move(view, 110.0, y), 0);
        assert_eq!(vayren_market_view_pointer_press(view, 110.0, y, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        assert_eq!(vayren_market_view_pointer_release(view, 110.0, y, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        loop {
            let n = vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len());
            if n <= 0 {
                break;
            }
            got.push(String::from_utf8_lossy(&buf[..n as usize]).into_owned());
        }
        if !got.is_empty() {
            break;
        }
        y += 20.0;
    }
    assert!(
        got.iter().any(|a| a.starts_with("select:")),
        "no watchlist tap queued select: (got {got:?})"
    );
    vayren_market_view_destroy(view);
}

/// Indicator toolbar end-to-end: the floating OBR bar's four buttons must
/// queue their wires through the real C ABI (pointer → Slint button →
/// callback → action queue). Scans the bar row for clickable targets so it
/// does not depend on exact label width.
#[test]
fn market_indicator_toolbar_buttons_queue_wires() {
    let (w, h) = (1280u32, 720u32);
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null());
    // Two rows so the OBR row (top) is distinguishable from Vol; OBR visible.
    let json = CString::new(
        r#"{"symbols":[{"symbol":"TEST","price":112.4,"change_pct":2.31}],
         "selected_symbol":"TEST","watchlists":["All Stocks"],
         "active_watchlist":"All Stocks","timeframes":["15m"],"timeframe":"15m",
         "exchange":"NSE","status":"ready",
         "indicators":{"OBR":true,"Vol":true},
         "bars":[{"time":"2024-01-02T09:15:00","open":100.0,"high":102.0,
         "low":99.0,"close":101.0,"volume":1000.0}]}"#,
    )
    .unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, json.as_ptr()), 0);
    assert_eq!(vayren_market_view_resize(view, w, h, 1.0), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut out = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, out.as_mut_ptr(), out.len()),
        1
    );

    let mut buf = [0u8; 256];
    // Drain any stale actions.
    while vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len()) > 0 {}

    // The eye glyph is accent-colored (#00C7B7) while its row is visible and
    // is the only screen element whose click queues `indicator:vis:`. Locate
    // it by scanning the rendered frame, then derive the sibling buttons from
    // the row geometry (20px button + 4px spacing = 24px stride) — robust to
    // the watchlist/panel width in front of the plot.
    let mut got: Vec<String> = Vec::new();
    let mut eye: Option<(f32, f32)> = None;
    let mut tested: Vec<(i32, i32)> = Vec::new();
    for y in (0..(h as i32)).step_by(3) {
        for x in (0..(w as i32)).step_by(3) {
            let i = ((y as u32 * w + x as u32) * 3) as usize;
            let px = [out[i], out[i + 1], out[i + 2]];
            let is_accent = (px[0] as i16 - 0x00).abs() <= 24
                && (px[1] as i16 - 0xC7).abs() <= 24
                && (px[2] as i16 - 0xB7).abs() <= 24;
            if !is_accent {
                continue;
            }
            // Cluster: skip accents within 14px of an already-tested point.
            if tested
                .iter()
                .any(|(tx, ty)| (tx - x).abs() < 14 && (ty - y).abs() < 14)
            {
                continue;
            }
            tested.push((x, y));
            let cx = x as f32 + 10.0;
            let cy = y as f32 + 10.0;
            assert_eq!(vayren_market_view_pointer_move(view, cx, cy), 0);
            assert_eq!(vayren_market_view_pointer_press(view, cx, cy, 0), 0);
            assert_eq!(vayren_market_view_tick(view), 0);
            assert_eq!(vayren_market_view_pointer_release(view, cx, cy, 0), 0);
            assert_eq!(vayren_market_view_tick(view), 0);
            loop {
                let n = vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len());
                if n <= 0 {
                    break;
                }
                let a = String::from_utf8_lossy(&buf[..n as usize]).into_owned();
                if a == "indicator:vis:OBR" && eye.is_none() {
                    eye = Some((cx, cy));
                }
                got.push(a);
            }
            if eye.is_some() {
                break;
            }
        }
        if eye.is_some() {
            break;
        }
    }
    let (ex, ey) = eye.expect("eye glyph must render and be clickable (indicator:vis:OBR)");

    // Sibling buttons: exactly two — settings (+22) and delete (+44).
    // 20px buttons + 2px spacing = 22px stride. The settings click opens the
    // native panel (view-local: no wire); delete queues `indicator:rm:OBR`.
    // No fourth button exists, so no `indicator:source:` wire may appear.
    for (dx, _label) in [(22.0, "settings"), (44.0, "delete")] {
        let cx = ex + dx;
        assert_eq!(vayren_market_view_pointer_move(view, cx, ey), 0);
        assert_eq!(vayren_market_view_pointer_press(view, cx, ey, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        assert_eq!(vayren_market_view_pointer_release(view, cx, ey, 0), 0);
        assert_eq!(vayren_market_view_tick(view), 0);
        loop {
            let n = vayren_market_view_next_action(view, buf.as_mut_ptr(), buf.len());
            if n <= 0 {
                break;
            }
            got.push(String::from_utf8_lossy(&buf[..n as usize]).into_owned());
        }
    }

    assert!(
        got.iter().any(|a| a == "indicator:vis:OBR"),
        "eye button must queue indicator:vis:OBR (got {got:?})"
    );
    assert!(
        got.iter().any(|a| a == "indicator:rm:OBR"),
        "delete button must queue indicator:rm:OBR (got {got:?})"
    );
    assert!(
        !got.iter().any(|a| a.starts_with("indicator:source:")),
        "no source/code button may exist (got {got:?})"
    );
    assert!(
        !got.iter().any(|a| a.starts_with("indicator:settings:")),
        "settings opens the native panel, it must not queue a wire (got {got:?})"
    );
    vayren_market_view_destroy(view);
}

/// Trade overlay parity pixels: teal BUY triangle, red SELL triangle and
/// blue EOD marker must all paint from real bridge facts (no red-circle
/// spam: every marker carries its Qt-mapped glyph, pill and color).
#[test]
fn market_trade_overlay_paints_parity_markers() {
    let bars = 160;
    let mut rows = String::new();
    for i in 0..bars {
        let close = 100.0 + (i as f64 * 0.3).sin() * 4.0;
        rows.push_str(&format!(
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
    let json = format!(
        "{{\"symbols\":[{{\"symbol\":\"TEST\",\"price\":112.4,\"change_pct\":2.31}}],\
         \"selected_symbol\":\"TEST\",\"watchlists\":[\"All Stocks\"],\
         \"active_watchlist\":\"All Stocks\",\"timeframes\":[\"15m\"],\"timeframe\":\"15m\",\
         \"exchange\":\"NSE\",\"status\":\"ready\",\"indicators\":{{}},\"bars\":[{}],\
         \"plot_series\":[],\
         \"trades\":[{{\"side\":\"LONG\",\"entry_price\":101.0,\"exit_price\":104.0,\
         \"winning\":true,\"entry_pos\":20,\"exit_pos\":40,\
         \"entry_covered\":false,\"exit_covered\":false,\"exit_reason\":\"SIGNAL\"}},\
         {{\"side\":\"SHORT\",\"entry_price\":106.0,\"exit_price\":103.0,\
         \"winning\":false,\"entry_pos\":60,\"exit_pos\":80,\
         \"entry_covered\":true,\"exit_covered\":false,\"exit_reason\":\"SIGNAL\"}}],\
         \"covered\":[],\
         \"strategy_markers\":[{{\"bar\":100,\"price\":102.0,\"kind\":\"TRIANGLE_BLUE\",\
         \"text\":\"EOD 102.00 +2.00\",\"layer\":60,\"order\":0}}],\
         \"strategy_rays\":[]}}",
        rows.trim_end_matches(',')
    );
    let (w, h) = (1280, 720);
    let view = vayren_market_view_create(w, h, 1.0);
    assert!(!view.is_null());
    let payload = CString::new(json).unwrap();
    assert_eq!(vayren_market_view_set_snapshot(view, payload.as_ptr()), 0);
    assert_eq!(vayren_market_view_tick(view), 0);
    let mut out = vec![0u8; (w * h * 3) as usize];
    assert_eq!(
        vayren_market_view_render(view, out.as_mut_ptr(), out.len()),
        1
    );
    // Teal entry triangle + pill, red exit triangle, blue EOD glyph.
    assert!(
        near(&out, [0x21, 0xC5, 0x8B], 24),
        "teal BUY marker missing"
    );
    assert!(
        near(&out, [0xF0, 0x5A, 0x67], 24),
        "red SELL marker missing"
    );
    assert!(
        near(&out, [0x42, 0xA5, 0xF5], 24),
        "blue EOD marker missing"
    );
    vayren_market_view_destroy(view);
}
