//! Exponential Moving Average (EMA) — exponentially weighted moving average.

/// Calculate EMA for the entire price series using standard smoothing.
///
/// EMA formula: EMA(t) = price(t) * α + EMA(t-1) * (1 - α), where
/// α = 2 / (period + 1). First value is initialized to the first price
/// (industry standard). Returns `None` if input is empty or period is zero.
/// NaN/Inf in prices produces NaN output from that point forward.
///
/// # Example
/// ```
/// # use vayren_core::indicator::ema;
/// let prices = vec![10.0, 11.0, 12.0, 11.0, 10.0];
/// let result = ema(&prices, 3).unwrap();
/// assert_eq!(result.len(), 5);
/// assert_eq!(result[0], 10.0); // seed
/// // result[1] = 11 * 0.5 + 10 * 0.5 = 10.5
/// // result[2] = 12 * 0.5 + 10.5 * 0.5 = 11.25
/// ```
pub fn ema(prices: &[f64], period: usize) -> Option<Vec<f64>> {
    if prices.is_empty() || period == 0 {
        return None;
    }
    let alpha = 2.0 / (period as f64 + 1.0);
    let mut result = Vec::with_capacity(prices.len());
    let mut ema_val = prices[0];
    result.push(ema_val);
    for &price in &prices[1..] {
        ema_val = price * alpha + ema_val * (1.0 - alpha);
        result.push(ema_val);
    }
    Some(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input() {
        assert_eq!(ema(&[], 5), None);
    }

    #[test]
    fn period_zero() {
        assert_eq!(ema(&[1.0, 2.0], 0), None);
    }

    #[test]
    fn single_value() {
        let result = ema(&[42.0], 10).unwrap();
        assert_eq!(result, vec![42.0]);
    }

    #[test]
    fn seed_is_first_price() {
        let result = ema(&[100.0, 110.0, 120.0], 5).unwrap();
        assert_eq!(result[0], 100.0);
    }

    #[test]
    fn converges_toward_new_prices() {
        let prices = vec![10.0, 20.0, 20.0, 20.0, 20.0];
        let result = ema(&prices, 3).unwrap();
        // α = 2/(3+1) = 0.5
        // EMA[0] = 10
        // EMA[1] = 20*0.5 + 10*0.5 = 15
        // EMA[2] = 20*0.5 + 15*0.5 = 17.5
        // EMA[3] = 20*0.5 + 17.5*0.5 = 18.75
        // EMA[4] = 20*0.5 + 18.75*0.5 = 19.375
        assert!((result[0] - 10.0).abs() < 1e-10);
        assert!((result[1] - 15.0).abs() < 1e-10);
        assert!((result[2] - 17.5).abs() < 1e-10);
        assert!((result[3] - 18.75).abs() < 1e-10);
        assert!((result[4] - 19.375).abs() < 1e-10);
    }

    #[test]
    fn nan_propagates() {
        let prices = vec![10.0, f64::NAN, 30.0];
        let result = ema(&prices, 2).unwrap();
        assert_eq!(result[0], 10.0);
        assert!(result[1].is_nan());
        assert!(result[2].is_nan()); // once NaN, stays NaN
    }

    #[test]
    fn period_one_tracks_exactly() {
        // α = 2/(1+1) = 1.0 → EMA = price (no smoothing)
        let prices = vec![5.0, 10.0, 15.0, 20.0];
        let result = ema(&prices, 1).unwrap();
        assert_eq!(result, prices);
    }
}
