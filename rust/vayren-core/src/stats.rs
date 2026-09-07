//! Small numeric statistics kernels — Rust-owned (constitution §1:
//! Numerical calculations). Used by the market timeframe detectors.

/// Most-frequent value, breaking ties by first-seen order — exactly the
/// semantics of Python `collections.Counter(...).most_common(1)`. Returns
/// `None` for an empty input.
pub fn mode(values: &[i64]) -> Option<i64> {
    let mut order: Vec<i64> = Vec::new();
    let mut counts: Vec<i64> = Vec::new();
    for &value in values {
        if let Some(pos) = order.iter().position(|&v| v == value) {
            counts[pos] += 1;
        } else {
            order.push(value);
            counts.push(1);
        }
    }
    if order.is_empty() {
        return None;
    }
    let mut best = 0usize;
    for i in 1..order.len() {
        // strictly greater keeps the first-seen element on ties (stable).
        if counts[i] > counts[best] {
            best = i;
        }
    }
    Some(order[best])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_is_none() {
        assert_eq!(mode(&[]), None);
    }

    #[test]
    fn picks_most_frequent() {
        assert_eq!(mode(&[5, 5, 3, 5, 3]), Some(5));
    }

    #[test]
    fn ties_break_by_first_seen() {
        // 7 and 9 both appear twice; 7 is seen first.
        assert_eq!(mode(&[7, 9, 7, 9]), Some(7));
    }

    #[test]
    fn single_value() {
        assert_eq!(mode(&[42]), Some(42));
    }
}
