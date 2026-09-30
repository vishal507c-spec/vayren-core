//! Volume Weighted Average Price (VWAP) — intraday benchmark price.

/// Calculate VWAP as a cumulative volume-weighted average.
///
/// VWAP formula: Σ(typical_price * volume) / Σ(volume), where
/// typical_price = (high + low + close) / 3. Returns a vector aligned with
/// inputs. Zero cumulative volume -> NaN (undefined). Typically reset daily;
/// caller handles session boundaries.
///
/// # Arguments
/// * `highs` - High prices
/// * `lows` - Low prices
/// * `closes` - Close prices
/// * `volumes` - Volume (must be non-negative)
///
/// All arrays must have the same length. Returns empty if mismatched.
///
/// # Example
/// ```
/// # use vayren_core::indicator::vwap;
/// let highs = vec![101.0, 102.0, 103.0];
/// let lows = vec![99.0, 100.0, 101.0];
/// let closes = vec![100.0, 101.0, 102.0];
/// let volumes = vec![1000.0, 1500.0, 2000.0];
/// let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
/// assert_eq!(result.len(), 3);
/// // result[0] = 100.0 (typical price, single bar)
/// // result[1] = weighted avg of first two bars
/// // result[2] = weighted avg of all three bars
/// ```
pub fn vwap(highs: &[f64], lows: &[f64], closes: &[f64], volumes: &[f64]) -> Option<Vec<f64>> {
    let n = highs.len();
    if n != lows.len() || n != closes.len() || n != volumes.len() || n == 0 {
        return None;
    }
    let mut result = Vec::with_capacity(n);
    let mut cum_pv = 0.0; // cumulative (price * volume)
    let mut cum_vol = 0.0; // cumulative volume
    for i in 0..n {
        let typical = (highs[i] + lows[i] + closes[i]) / 3.0;
        cum_pv += typical * volumes[i];
        cum_vol += volumes[i];
        let vwap_val = if cum_vol == 0.0 {
            f64::NAN // undefined when no volume
        } else {
            cum_pv / cum_vol
        };
        result.push(vwap_val);
    }
    Some(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input() {
        assert_eq!(vwap(&[], &[], &[], &[]), None);
    }

    #[test]
    fn mismatched_lengths() {
        let highs = vec![1.0, 2.0];
        let lows = vec![0.5];
        let closes = vec![0.8, 1.5];
        let volumes = vec![100.0, 200.0];
        assert_eq!(vwap(&highs, &lows, &closes, &volumes), None);
    }

    #[test]
    fn single_bar() {
        let highs = vec![102.0];
        let lows = vec![98.0];
        let closes = vec![100.0];
        let volumes = vec![1000.0];
        let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
        // Typical = (102+98+100)/3 = 100
        assert_eq!(result, vec![100.0]);
    }

    #[test]
    fn cumulative_weighted_average() {
        let highs = vec![11.0, 12.0, 13.0];
        let lows = vec![9.0, 10.0, 11.0];
        let closes = vec![10.0, 11.0, 12.0];
        let volumes = vec![100.0, 200.0, 300.0];
        let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
        // Bar 0: typical=10, vwap = 10*100/100 = 10.0
        assert!((result[0] - 10.0).abs() < 1e-10);
        // Bar 1: typical=11, cum_pv=10*100+11*200=3200, cum_vol=300, vwap=3200/300≈10.666
        assert!((result[1] - 10.666666666666666).abs() < 1e-10);
        // Bar 2: typical=12, cum_pv=3200+12*300=6800, cum_vol=600, vwap=6800/600≈11.333
        assert!((result[2] - 11.333333333333334).abs() < 1e-10);
    }

    #[test]
    fn zero_volume_produces_nan() {
        let highs = vec![10.0, 11.0];
        let lows = vec![9.0, 10.0];
        let closes = vec![9.5, 10.5];
        let volumes = vec![0.0, 0.0];
        let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
        assert!(result[0].is_nan());
        assert!(result[1].is_nan());
    }

    #[test]
    fn zero_volume_then_nonzero() {
        let highs = vec![10.0, 11.0, 12.0];
        let lows = vec![9.0, 10.0, 11.0];
        let closes = vec![9.5, 10.5, 11.5];
        let volumes = vec![0.0, 0.0, 100.0];
        let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
        assert!(result[0].is_nan());
        assert!(result[1].is_nan());
        // Bar 2: typical=(12+11+11.5)/3=11.5, cum_pv=11.5*100, cum_vol=100, vwap=11.5
        assert!((result[2] - 11.5).abs() < 1e-10);
    }

    #[test]
    fn nan_price_propagates() {
        let highs = vec![10.0, f64::NAN, 12.0];
        let lows = vec![9.0, 10.0, 11.0];
        let closes = vec![9.5, 10.5, 11.5];
        let volumes = vec![100.0, 100.0, 100.0];
        let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
        assert!((result[0] - 9.5).abs() < 1e-10); // first bar valid
        assert!(result[1].is_nan()); // NaN in typical price
        assert!(result[2].is_nan()); // cumulative NaN persists
    }

    #[test]
    fn equal_volumes_simple_average() {
        // When all volumes equal, VWAP = simple average of typical prices
        let highs = vec![11.0, 13.0, 15.0];
        let lows = vec![9.0, 11.0, 13.0];
        let closes = vec![10.0, 12.0, 14.0];
        let volumes = vec![100.0, 100.0, 100.0];
        let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
        // Typical prices: 10, 12, 14
        // VWAP[2] = (10+12+14)/3 = 12
        assert!((result[2] - 12.0).abs() < 1e-10);
    }

    #[test]
    fn large_volume_dominates() {
        let highs = vec![10.0, 20.0];
        let lows = vec![10.0, 20.0];
        let closes = vec![10.0, 20.0];
        let volumes = vec![1.0, 1000.0];
        let result = vwap(&highs, &lows, &closes, &volumes).unwrap();
        // Bar 1 typical=20, dominates due to 1000x volume
        // VWAP[1] = (10*1 + 20*1000)/(1+1000) ≈ 20
        assert!((result[1] - 19.990009990009993).abs() < 1e-10);
    }
}
