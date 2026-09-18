//! Simple Moving Average (SMA) — arithmetic mean over a sliding window.

/// Calculate SMA for each valid window position in the price series.
///
/// Returns a vector where `out[i]` is the average of `prices[i-period+1..=i]`.
/// Positions before `period-1` are skipped (not enough data). Empty or
/// too-short input -> empty output. NaN/Inf inputs produce NaN output at that
/// window (fail-safe: never panic, but signal invalid data).
///
/// # Example
/// ```
/// # use vayren_core::indicator::sma;
/// let prices = vec![10.0, 20.0, 30.0, 40.0, 50.0];
/// let result = sma(&prices, 3);
/// // First valid SMA at index 2: (10+20+30)/3 = 20.0
/// assert_eq!(result, vec![20.0, 30.0, 40.0]);
/// ```
pub fn sma(prices: &[f64], period: usize) -> Vec<f64> {
    if period == 0 || prices.len() < period {
        return Vec::new();
    }
    let mut result = Vec::with_capacity(prices.len() - period + 1);
    for i in (period - 1)..prices.len() {
        let window = &prices[(i + 1 - period)..=i];
        let sum: f64 = window.iter().sum();
        let avg = sum / period as f64;
        result.push(avg);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input() {
        assert_eq!(sma(&[], 5), Vec::<f64>::new());
    }

    #[test]
    fn period_zero() {
        assert_eq!(sma(&[1.0, 2.0, 3.0], 0), Vec::<f64>::new());
    }

    #[test]
    fn too_short() {
        assert_eq!(sma(&[1.0, 2.0], 3), Vec::<f64>::new());
    }

    #[test]
    fn exact_period() {
        let result = sma(&[10.0, 20.0, 30.0], 3);
        assert_eq!(result.len(), 1);
        assert!((result[0] - 20.0).abs() < 1e-10);
    }

    #[test]
    fn sliding_window() {
        let prices = vec![10.0, 20.0, 30.0, 40.0, 50.0];
        let result = sma(&prices, 3);
        assert_eq!(result.len(), 3);
        assert!((result[0] - 20.0).abs() < 1e-10); // (10+20+30)/3
        assert!((result[1] - 30.0).abs() < 1e-10); // (20+30+40)/3
        assert!((result[2] - 40.0).abs() < 1e-10); // (30+40+50)/3
    }

    #[test]
    fn nan_input_propagates() {
        let prices = vec![10.0, f64::NAN, 30.0, 40.0];
        let result = sma(&prices, 2);
        assert!(result[0].is_nan()); // (10 + NaN)/2
        assert!(result[1].is_nan()); // (NaN + 30)/2
        assert!((result[2] - 35.0).abs() < 1e-10); // (30+40)/2 valid
    }

    #[test]
    fn period_one() {
        let prices = vec![5.0, 10.0, 15.0];
        let result = sma(&prices, 1);
        assert_eq!(result, prices); // SMA(1) = prices themselves
    }
}
