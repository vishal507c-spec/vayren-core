//! Relative Strength Index (RSI) — momentum oscillator (0-100).

/// Calculate RSI using Wilder's smoothing (industry standard).
///
/// RSI formula: 100 - (100 / (1 + RS)), where RS = avg_gain / avg_loss over
/// the period. First period uses simple average, subsequent use Wilder's EMA:
/// new_avg = (prev_avg * (period-1) + current_change) / period.
///
/// Returns a vector aligned with `prices`, where first `period` values are
/// `None` (not enough data), then RSI values. Empty/short input -> empty.
/// All-zero changes (flat prices) -> RSI 50.0 by convention (neutral).
///
/// # Example
/// ```
/// # use vayren_core::indicator::rsi;
/// let prices = vec![44.0, 44.25, 44.5, 43.75, 44.0, 44.5, 45.0, 45.5];
/// let result = rsi(&prices, 5);
/// assert_eq!(result.len(), 8);
/// // First 5 are None, then computed RSI values
/// ```
pub fn rsi(prices: &[f64], period: usize) -> Vec<Option<f64>> {
    if prices.is_empty() || period == 0 {
        return vec![None; prices.len()];
    }
    if prices.len() <= period {
        return vec![None; prices.len()];
    }
    let mut result = vec![None; prices.len()];
    // Calculate price changes
    let mut gains = Vec::with_capacity(prices.len() - 1);
    let mut losses = Vec::with_capacity(prices.len() - 1);
    for i in 1..prices.len() {
        let change = prices[i] - prices[i - 1];
        // NaN propagates: if change is NaN, both gain and loss are NaN
        if change.is_nan() {
            gains.push(f64::NAN);
            losses.push(f64::NAN);
        } else if change > 0.0 {
            gains.push(change);
            losses.push(0.0);
        } else {
            gains.push(0.0);
            losses.push(-change);
        }
    }
    // First RSI: simple average of first `period` changes
    let first_avg_gain: f64 = gains[..period].iter().sum::<f64>() / period as f64;
    let first_avg_loss: f64 = losses[..period].iter().sum::<f64>() / period as f64;
    let mut avg_gain = first_avg_gain;
    let mut avg_loss = first_avg_loss;
    result[period] = Some(rsi_value(avg_gain, avg_loss));
    // Subsequent RSI: Wilder's smoothing
    for i in (period + 1)..prices.len() {
        let idx = i - 1; // index into gains/losses arrays
        avg_gain = (avg_gain * (period - 1) as f64 + gains[idx]) / period as f64;
        avg_loss = (avg_loss * (period - 1) as f64 + losses[idx]) / period as f64;
        result[i] = Some(rsi_value(avg_gain, avg_loss));
    }
    result
}

fn rsi_value(avg_gain: f64, avg_loss: f64) -> f64 {
    if avg_loss == 0.0 {
        if avg_gain == 0.0 {
            50.0 // flat prices: neutral
        } else {
            100.0 // all gains, no losses
        }
    } else {
        let rs = avg_gain / avg_loss;
        100.0 - (100.0 / (1.0 + rs))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input() {
        assert_eq!(rsi(&[], 5), Vec::<Option<f64>>::new());
    }

    #[test]
    fn too_short() {
        let result = rsi(&[1.0, 2.0, 3.0], 5);
        assert_eq!(result, vec![None, None, None]);
    }

    #[test]
    fn flat_prices_neutral() {
        let prices = vec![10.0; 20];
        let result = rsi(&prices, 5);
        // First 5 are None, rest should be 50.0 (neutral)
        for (i, val) in result.iter().enumerate() {
            if i < 5 {
                assert_eq!(*val, None);
            } else {
                assert_eq!(val.unwrap(), 50.0);
            }
        }
    }

    #[test]
    fn all_gains() {
        let prices = vec![10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0];
        let result = rsi(&prices, 5);
        // First period+1 values are None (need period changes, which need period+1 prices)
        // Index 5 = first RSI (after 5 price changes from indices 1-5)
        assert_eq!(result[0..5], vec![None; 5]);
        assert_eq!(result[5].unwrap(), 100.0);
        assert_eq!(result[6].unwrap(), 100.0);
    }

    #[test]
    fn all_losses() {
        let prices = vec![16.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0];
        let result = rsi(&prices, 5);
        // First 5 None, index 5 = first RSI (all losses)
        assert_eq!(result[0..5], vec![None; 5]);
        assert_eq!(result[5].unwrap(), 0.0);
        assert_eq!(result[6].unwrap(), 0.0);
    }

    #[test]
    fn mixed_changes() {
        // Simple case: alternating +1/-1
        let prices = vec![10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 10.0, 11.0];
        let result = rsi(&prices, 3);
        // First 3 None (indices 0,1,2), index 3 = first RSI
        assert_eq!(result[0..3], vec![None; 3]);
        // RSI at index 3: gains=[1,0,1], losses=[0,1,0] → avg_gain=2/3, avg_loss=1/3 → RS=2 → RSI≈66.67
        let rsi3 = result[3].unwrap();
        assert!((rsi3 - 66.666666).abs() < 0.01);
    }

    #[test]
    fn nan_input_produces_nan() {
        let prices = vec![10.0, 11.0, f64::NAN, 13.0, 14.0, 15.0, 16.0];
        let result = rsi(&prices, 3);
        // Changes: [+1, NaN, NaN, +1, +1, +1]
        // First RSI at index 3 uses changes[0..3] = [+1, NaN, NaN]
        // NaN in changes causes NaN in avg_gain/avg_loss, propagates to RSI
        assert!(result[3].is_some() && result[3].unwrap().is_nan());
        assert!(result[4].is_some() && result[4].unwrap().is_nan());
    }

    #[test]
    fn period_one_edge_case() {
        // Period 1 means first RSI at index 1
        let prices = vec![10.0, 12.0, 11.0, 13.0];
        let result = rsi(&prices, 1);
        assert_eq!(result[0], None);
        // Index 1: gain=2, loss=0 → RSI=100
        assert_eq!(result[1].unwrap(), 100.0);
        // Index 2: Wilder avg_gain=(2*0+0)/1=0, avg_loss=(0*0+1)/1=1 → RSI=0
        assert_eq!(result[2].unwrap(), 0.0);
        // Index 3: avg_gain=(0*0+2)/1=2, avg_loss=(1*0+0)/1=0 → RSI=100
        assert_eq!(result[3].unwrap(), 100.0);
    }
}
