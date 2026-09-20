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
/// * `bar_width` - Width allocated to each bar in pixels.
/// * `bar_spacing` - Gap between bars in pixels.
/// * `chart_left` - Left edge of the drawable chart area in pixels.
///
/// # Returns
/// The x-coordinate (left edge of the bar body) if the bar is within the
/// visible window, otherwise `None`.
pub fn bar_to_x(
    bar_index: usize,
    first: usize,
    bar_width: f64,
    bar_spacing: f64,
    chart_left: f64,
) -> Option<f64> {
    if bar_index < first {
        return None;
    }
    let offset = (bar_index - first) as f64;
    Some(chart_left + offset * (bar_width + bar_spacing))
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
/// Returns `None` if the price range is zero (degenerate).
pub fn price_to_y(
    price: f64,
    price_low: f64,
    price_high: f64,
    chart_top: f64,
    chart_height: f64,
) -> Option<f64> {
    let range = price_high - price_low;
    if range <= 0.0 {
        return None;
    }
    let normalized = (price - price_low) / range;
    // Flip: price axis grows upward, screen y grows downward.
    Some(chart_top + chart_height * (1.0 - normalized))
}

/// Map an x-coordinate back to a bar index.
pub fn x_to_bar(
    x: f64,
    first: usize,
    bar_width: f64,
    bar_spacing: f64,
    chart_left: f64,
) -> Option<usize> {
    if x < chart_left {
        return None;
    }
    let offset = (x - chart_left) / (bar_width + bar_spacing);
    if offset < 0.0 {
        return None;
    }
    Some(first + offset.floor() as usize)
}

/// Map a y-coordinate back to a price.
pub fn y_to_price(
    y: f64,
    price_low: f64,
    price_high: f64,
    chart_top: f64,
    chart_height: f64,
) -> Option<f64> {
    let range = price_high - price_low;
    if range <= 0.0 || chart_height <= 0.0 {
        return None;
    }
    let normalized = 1.0 - (y - chart_top) / chart_height;
    Some(price_low + normalized * range)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bar_to_x_first_bar() {
        let x = bar_to_x(10, 10, 8.0, 2.0, 50.0).unwrap();
        assert_eq!(x, 50.0);
    }

    #[test]
    fn bar_to_x_offset() {
        let x = bar_to_x(15, 10, 8.0, 2.0, 50.0).unwrap();
        assert_eq!(x, 50.0 + 5.0 * 10.0);
    }

    #[test]
    fn bar_to_x_before_first_is_none() {
        assert_eq!(bar_to_x(5, 10, 8.0, 2.0, 50.0), None);
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
    fn x_to_bar_round_trip() {
        let original = 15;
        let x = bar_to_x(original, 10, 8.0, 2.0, 50.0).unwrap();
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
