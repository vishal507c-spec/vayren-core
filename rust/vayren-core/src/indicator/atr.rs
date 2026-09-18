//! Average True Range (ATR) — volatility indicator.

/// Calculate ATR using Wilder's smoothing.
///
/// True Range (TR) is the greatest of:
/// - high - low
/// - |high - prev_close|
/// - |low - prev_close|
///
/// ATR is a smoothed moving average of TR (first period uses simple average,
/// then Wilder's EMA). Returns a vector aligned with input, where first value
/// is `None` (no previous close), next `period-1` are `None` (warming up),
/// then ATR values.
///
/// # Arguments
/// * `highs` - High prices
/// * `lows` - Low prices
/// * `closes` - Close prices (used for prev_close)
/// * `period` - ATR smoothing period
///
/// All arrays must have the same length. Returns empty if mismatched or too short.
///
/// # Example
/// ```
/// # use vayren_core::indicator::atr;
/// let highs = vec![50.0, 52.0, 51.0, 53.0, 54.0, 55.0];
/// let lows = vec![48.0, 49.0, 48.5, 50.0, 51.0, 52.0];
/// let closes = vec![49.0, 51.0, 50.0, 52.0, 53.0, 54.0];
/// let result = atr(&highs, &lows, &closes, 3);
/// assert_eq!(result.len(), 6);
/// // First value None (no prev_close), next 2 None (warming), then ATR
/// ```
pub fn atr(highs: &[f64], lows: &[f64], closes: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = highs.len();
    if n != lows.len() || n != closes.len() || n <= period || period == 0 {
        return vec![None; n.max(1)];
    }
    let mut result = vec![None; n];
    // Calculate True Ranges
    let mut trs = Vec::with_capacity(n - 1);
    for i in 1..n {
        // Check for NaN in any input
        if highs[i].is_nan() || lows[i].is_nan() || closes[i - 1].is_nan() {
            trs.push(f64::NAN);
        } else {
            let h_l = highs[i] - lows[i];
            let h_pc = (highs[i] - closes[i - 1]).abs();
            let l_pc = (lows[i] - closes[i - 1]).abs();
            let tr = h_l.max(h_pc).max(l_pc);
            trs.push(tr);
        }
    }
    // First ATR at index `period`: simple average of first `period` TRs
    // TRs array is offset by 1 (starts at index 1 in prices), so trs[0..period] covers prices[1..=period]
    let first_atr: f64 = trs[..period].iter().sum::<f64>() / period as f64;
    result[period] = Some(first_atr);
    // Subsequent ATR: Wilder's smoothing
    let mut atr_val = first_atr;
    for i in (period + 1)..n {
        let tr_idx = i - 1; // trs[tr_idx] is the TR for prices[i]
        atr_val = (atr_val * (period - 1) as f64 + trs[tr_idx]) / period as f64;
        result[i] = Some(atr_val);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input() {
        assert_eq!(atr(&[], &[], &[], 5), vec![None]);
    }

    #[test]
    fn mismatched_lengths() {
        let result = atr(&[1.0, 2.0], &[0.5], &[1.5, 1.8], 2);
        assert_eq!(result, vec![None, None]);
    }

    #[test]
    fn too_short_for_period() {
        let result = atr(&[1.0, 2.0], &[0.5, 1.0], &[0.8, 1.5], 5);
        assert_eq!(result, vec![None, None]);
    }

    #[test]
    fn first_value_always_none() {
        // No prev_close for index 0
        let highs = vec![10.0, 11.0, 12.0, 13.0, 14.0];
        let lows = vec![9.0, 10.0, 11.0, 12.0, 13.0];
        let closes = vec![9.5, 10.5, 11.5, 12.5, 13.5];
        let result = atr(&highs, &lows, &closes, 2);
        assert_eq!(result[0], None);
    }

    #[test]
    fn simple_case() {
        // Bars with consistent gaps: H-L=2, but gaps to prev_close add 1 more
        let highs = vec![12.0, 14.0, 16.0, 18.0, 20.0, 22.0];
        let lows = vec![10.0, 12.0, 14.0, 16.0, 18.0, 20.0];
        let closes = vec![11.0, 13.0, 15.0, 17.0, 19.0, 21.0];
        let result = atr(&highs, &lows, &closes, 3);
        // Bar1: TR=max(2, |14-11|, |12-11|)=max(2,3,1)=3
        // Bar2: TR=max(2, |16-13|, |14-13|)=max(2,3,1)=3
        // Bar3: TR=max(2, |18-15|, |16-15|)=max(2,3,1)=3
        // ATR[3] = (3+3+3)/3 = 3.0
        assert_eq!(result[0..=3], vec![None, None, None, Some(3.0)]);
        // Bar4: TR=max(2,|20-17|,|18-17|)=3, Wilder: (3*2 + 3)/3 = 3.0
        assert!((result[4].unwrap() - 3.0).abs() < 1e-10);
        // Bar5: TR=max(2,|22-19|,|20-19|)=3, Wilder: (3*2 + 3)/3 = 3.0
        assert!((result[5].unwrap() - 3.0).abs() < 1e-10);
    }

    #[test]
    fn gap_up_increases_tr() {
        let highs = vec![10.0, 11.0, 15.0]; // gap up at index 2
        let lows = vec![9.0, 10.0, 14.0];
        let closes = vec![9.5, 10.5, 14.5];
        let result = atr(&highs, &lows, &closes, 1);
        // TR[1] = max(1, |11-9.5|, |10-9.5|) = max(1, 1.5, 0.5) = 1.5
        assert_eq!(result[0], None);
        assert!((result[1].unwrap() - 1.5).abs() < 1e-10);
        // TR[2] = max(1, |15-10.5|, |14-10.5|) = max(1, 4.5, 3.5) = 4.5
        // Wilder: (1.5*0 + 4.5)/1 = 4.5
        assert!((result[2].unwrap() - 4.5).abs() < 1e-10);
    }

    #[test]
    fn gap_down_increases_tr() {
        let highs = vec![20.0, 19.0, 12.0]; // gap down at index 2
        let lows = vec![18.0, 17.0, 10.0];
        let closes = vec![19.0, 18.0, 11.0];
        let result = atr(&highs, &lows, &closes, 1);
        // TR[1] = max(2, |19-19|, |17-19|) = max(2, 0, 2) = 2
        assert!((result[1].unwrap() - 2.0).abs() < 1e-10);
        // TR[2] = max(2, |12-18|, |10-18|) = max(2, 6, 8) = 8
        assert!((result[2].unwrap() - 8.0).abs() < 1e-10);
    }

    #[test]
    fn nan_input_propagates() {
        let highs = vec![10.0, 11.0, f64::NAN, 13.0];
        let lows = vec![9.0, 10.0, 11.0, 12.0];
        let closes = vec![9.5, 10.5, 11.5, 12.5];
        let result = atr(&highs, &lows, &closes, 1);
        // Index 0 = None (no prev_close)
        // Index 1 = first ATR (period=1, so ATR starts at 1)
        // Index 2 = NaN high propagates into TR, then ATR
        assert!(result[2].is_some() && result[2].unwrap().is_nan());
        assert!(result[3].is_some() && result[3].unwrap().is_nan()); // smoothing carries NaN forward
    }

    #[test]
    fn zero_ranges_produce_zero_atr() {
        // Flat bars: H=L=C
        let prices = vec![10.0, 10.0, 10.0, 10.0, 10.0];
        let result = atr(&prices, &prices, &prices, 2);
        assert_eq!(result[0..=2], vec![None, None, Some(0.0)]);
        assert_eq!(result[3].unwrap(), 0.0);
        assert_eq!(result[4].unwrap(), 0.0);
    }
}
