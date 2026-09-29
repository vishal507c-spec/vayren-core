//! Chart coordinate mapping — bar index → x, price → y.
//!
//! Pure numeric viewport math extracted from 04_chart/chart/renderer.py.
//! The full `ChartViewport` stays in Python (legacy-bound), but the hot-path
//! coordinate transforms can run in Rust for overlay painters and indicators
//! that need to map thousands of points per frame.

/// Map a bar index to x-coordinate within the visible window.
///
/// # Arguments
/// * `bar_index` - Absolute bar index in the full dataset.
/// * `first` - First visible bar index (inclusive).
/// * `count` - Number of slots in the visible window (the plot's logical
///   width, NOT the number of bars the data happens to hold — a window can
///   show empty space on the right).
/// * `bar_width` - Width allocated to each bar in pixels.
/// * `bar_spacing` - Gap between bars in pixels.
/// * `chart_left` - Left edge of the drawable chart area in pixels.
///
/// # Returns
/// The x-coordinate (left edge of the bar body) if the bar is within the
/// visible window, otherwise `None`. A bar at or past `first + count` is
/// OUTSIDE the window (that is where the empty right-hand space begins), so
/// it never gets a coordinate.
pub fn bar_to_x(
    bar_index: usize,
    first: usize,
    count: usize,
    bar_width: f64,
    bar_spacing: f64,
    chart_left: f64,
) -> Option<f64> {
    if bar_index < first {
        return None;
    }
    // Saturating window end: a degenerate `count == 0` window admits nothing.
    let window_end = first.checked_add(count)?;
    if bar_index >= window_end {
        return None;
    }
    let pitch = bar_width + bar_spacing;
    let offset = (bar_index - first) as f64;
    let x = chart_left + offset * pitch;
    // A NaN/inf coordinate would propagate straight into the painter; the
    // caller treats `None` as "not plottable" and skips the primitive.
    x.is_finite().then_some(x)
}

/// Map a price to y-coordinate within the visible price range.
///
/// # Arguments
/// * `price` - The price value to map.
/// * `price_low` - Bottom of the visible price range.
/// * `price_high` - Top of the visible price range.
/// * `chart_top` - Top edge of the drawable chart area in pixels.
/// * `chart_height` - Height of the drawable chart area in pixels.
///
/// # Returns
/// The y-coordinate (higher y = lower price, screen origin top-left).
/// Returns `None` if the price range is zero (degenerate) or if any input /
/// the result is non-finite — a `Some(NaN)` would be drawn as garbage and
/// would poison every downstream accumulator.
pub fn price_to_y(
    price: f64,
    price_low: f64,
    price_high: f64,
    chart_top: f64,
    chart_height: f64,
) -> Option<f64> {
    if !price.is_finite()
        || !price_low.is_finite()
        || !price_high.is_finite()
        || !chart_top.is_finite()
        || !chart_height.is_finite()
    {
        return None;
    }
    let range = price_high - price_low;
    if range <= 0.0 {
        return None;
    }
    let normalized = (price - price_low) / range;
    // Flip: price axis grows upward, screen y grows downward.
    let y = chart_top + chart_height * (1.0 - normalized);
    y.is_finite().then_some(y)
}

/// Map an x-coordinate back to a bar index.
///
/// Returns `None` when the point is left of the plot, when the bar pitch is
/// degenerate (zero/negative/non-finite — dividing by it would yield an
/// infinite or NaN slot count), or when the resulting absolute index does not
/// fit `usize`. A saturating `as usize` cast would silently answer with a
/// wrong (usually 0) bar instead of admitting it cannot map.
pub fn x_to_bar(
    x: f64,
    first: usize,
    bar_width: f64,
    bar_spacing: f64,
    chart_left: f64,
) -> Option<usize> {
    if !x.is_finite() || !chart_left.is_finite() {
        return None;
    }
    if x < chart_left {
        return None;
    }
    let pitch = bar_width + bar_spacing;
    if !pitch.is_finite() || pitch <= 0.0 {
        return None;
    }
    let offset = (x - chart_left) / pitch;
    if !offset.is_finite() || offset < 0.0 {
        return None;
    }
    // floor() then checked conversion: an offset beyond `usize::MAX` is not a
    // bar index, it is an arithmetic overflow.
    let slot = offset.floor();
    if slot > usize::MAX as f64 {
        return None;
    }
    first.checked_add(slot as usize)
}

/// Map a y-coordinate back to a price.
pub fn y_to_price(
    y: f64,
    price_low: f64,
    price_high: f64,
    chart_top: f64,
    chart_height: f64,
) -> Option<f64> {
    if !y.is_finite()
        || !price_low.is_finite()
        || !price_high.is_finite()
        || !chart_top.is_finite()
        || !chart_height.is_finite()
    {
        return None;
    }
    let range = price_high - price_low;
    if range <= 0.0 || chart_height <= 0.0 {
        return None;
    }
    let normalized = 1.0 - (y - chart_top) / chart_height;
    let price = price_low + normalized * range;
    price.is_finite().then_some(price)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bar_to_x_first_bar() {
        let x = bar_to_x(10, 10, 100, 8.0, 2.0, 50.0).unwrap();
        assert_eq!(x, 50.0);
    }

    #[test]
    fn bar_to_x_offset() {
        let x = bar_to_x(15, 10, 100, 8.0, 2.0, 50.0).unwrap();
        assert_eq!(x, 50.0 + 5.0 * 10.0);
    }

    #[test]
    fn bar_to_x_before_first_is_none() {
        assert_eq!(bar_to_x(5, 10, 100, 8.0, 2.0, 50.0), None);
    }

    #[test]
    fn bar_to_x_past_window_is_none() {
        // `first + count` is the first EMPTY slot, not a plottable bar.
        assert_eq!(bar_to_x(14, 10, 4, 8.0, 2.0, 50.0), None);
        assert_eq!(bar_to_x(13, 10, 4, 8.0, 2.0, 50.0), Some(80.0));
        // A zero-slot window admits nothing.
        assert_eq!(bar_to_x(10, 10, 0, 8.0, 2.0, 50.0), None);
    }

    #[test]
    fn bar_to_x_non_finite_geometry_is_none() {
        assert_eq!(bar_to_x(10, 10, 100, f64::NAN, 2.0, 50.0), None);
        assert_eq!(bar_to_x(10, 10, 100, 8.0, 2.0, f64::INFINITY), None);
    }

    #[test]
    fn price_to_y_at_high() {
        let y = price_to_y(150.0, 100.0, 150.0, 20.0, 400.0).unwrap();
        assert_eq!(y, 20.0);
    }

    #[test]
    fn price_to_y_at_low() {
        let y = price_to_y(100.0, 100.0, 150.0, 20.0, 400.0).unwrap();
        assert_eq!(y, 420.0);
    }

    #[test]
    fn price_to_y_midpoint() {
        let y = price_to_y(125.0, 100.0, 150.0, 20.0, 400.0).unwrap();
        assert_eq!(y, 220.0);
    }

    #[test]
    fn price_to_y_zero_range_is_none() {
        assert_eq!(price_to_y(100.0, 100.0, 100.0, 20.0, 400.0), None);
    }

    #[test]
    fn non_finite_inputs_never_produce_a_coordinate() {
        // A Some(NaN)/Some(inf) would be painted as garbage and poison every
        // downstream accumulator — the transforms must report "no mapping".
        assert_eq!(price_to_y(f64::NAN, 100.0, 150.0, 20.0, 400.0), None);
        assert_eq!(price_to_y(f64::INFINITY, 100.0, 150.0, 20.0, 400.0), None);
        assert_eq!(price_to_y(120.0, f64::NAN, 150.0, 20.0, 400.0), None);
        assert_eq!(price_to_y(120.0, 100.0, f64::NAN, 20.0, 400.0), None);
        assert_eq!(price_to_y(120.0, 100.0, 150.0, f64::NAN, 400.0), None);
        assert_eq!(price_to_y(120.0, 100.0, 150.0, 20.0, f64::NAN), None);
        assert_eq!(y_to_price(f64::NAN, 100.0, 150.0, 20.0, 400.0), None);
        assert_eq!(y_to_price(200.0, f64::NAN, 150.0, 20.0, 400.0), None);
        assert_eq!(y_to_price(200.0, 100.0, 150.0, f64::NAN, 400.0), None);
        assert_eq!(y_to_price(200.0, 100.0, 150.0, 20.0, f64::INFINITY), None);
        assert_eq!(x_to_bar(f64::NAN, 10, 8.0, 2.0, 50.0), None);
        assert_eq!(x_to_bar(f64::INFINITY, 10, 8.0, 2.0, 50.0), None);
    }

    #[test]
    fn x_to_bar_zero_or_negative_pitch_is_none() {
        // Dividing by a zero pitch yields an infinite slot count; a saturating
        // `as usize` would answer with a wrong bar instead of refusing.
        assert_eq!(x_to_bar(70.0, 10, 0.0, 0.0, 50.0), None);
        assert_eq!(x_to_bar(70.0, 10, -8.0, 2.0, 50.0), None);
        assert_eq!(x_to_bar(70.0, 10, f64::NAN, 2.0, 50.0), None);
    }

    #[test]
    fn x_to_bar_out_of_range_offset_is_none() {
        // first + slot overflows usize — refuse instead of wrapping.
        let huge = usize::MAX as f64;
        assert_eq!(x_to_bar(huge * 1e10, usize::MAX, 1.0, 0.0, 0.0), None);
    }

    #[test]
    fn x_to_bar_round_trip() {
        let original = 15;
        let x = bar_to_x(original, 10, 100, 8.0, 2.0, 50.0).unwrap();
        let recovered = x_to_bar(x, 10, 8.0, 2.0, 50.0).unwrap();
        assert_eq!(recovered, original);
    }

    #[test]
    fn x_to_bar_before_chart_is_none() {
        assert_eq!(x_to_bar(30.0, 10, 8.0, 2.0, 50.0), None);
    }

    #[test]
    fn y_to_price_round_trip() {
        let original = 125.0;
        let y = price_to_y(original, 100.0, 150.0, 20.0, 400.0).unwrap();
        let recovered = y_to_price(y, 100.0, 150.0, 20.0, 400.0).unwrap();
        assert!((recovered - original).abs() < 0.01);
    }

    #[test]
    fn y_to_price_zero_range_is_none() {
        assert_eq!(y_to_price(200.0, 100.0, 100.0, 20.0, 400.0), None);
    }

    #[test]
    fn y_to_price_zero_height_is_none() {
        assert_eq!(y_to_price(200.0, 100.0, 150.0, 20.0, 0.0), None);
    }
}
