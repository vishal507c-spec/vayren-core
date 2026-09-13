//! Chart viewport zoom state — pure, headless-testable native UI state
//! (constitution §3: Rust owns UI state/interaction logic; Slint only
//! renders bound properties).
//!
//! This is the first migrated slice of the chart workspace: the zoom level
//! owned centrally in Rust. The Slint chart toolbar reports the Reset Zoom
//! action here; the canvas itself still lives in the Python application
//! (Qt shell) until its own migration slice lands.

/// Zoom level showing the full loaded window (100%).
pub const DEFAULT_LEVEL: f64 = 1.0;

/// Hard viewport bounds: 10% (overview) .. 2000% (single-candle inspection).
pub const MIN_LEVEL: f64 = 0.1;
pub const MAX_LEVEL: f64 = 20.0;

/// Immutable zoom snapshot for one chart viewport.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ChartViewportZoom {
    level: f64,
}

impl Default for ChartViewportZoom {
    fn default() -> Self {
        Self {
            level: DEFAULT_LEVEL,
        }
    }
}

impl ChartViewportZoom {
    /// Zoom level as a multiplier of the default window (1.0 = 100%).
    pub fn level(self) -> f64 {
        self.level
    }

    /// True when the viewport shows the default window.
    pub fn is_default(self) -> bool {
        self.level == DEFAULT_LEVEL
    }

    /// Set an explicit level, clamped into the hard viewport bounds so the
    /// viewport can never zoom to zero (invisible chart) or overflow.
    /// NaN falls back to the default window instead of poisoning state.
    pub fn set_level(&mut self, level: f64) {
        self.level = if level.is_nan() {
            DEFAULT_LEVEL
        } else {
            level.clamp(MIN_LEVEL, MAX_LEVEL)
        };
    }

    /// Reset Zoom action: restore the default window.
    pub fn reset(&mut self) {
        self.level = DEFAULT_LEVEL;
    }

    /// Deterministic display label for the bound Slint property.
    pub fn label(self) -> String {
        format!("{}%", (self.level * 100.0).round() as i64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_is_full_window() {
        let zoom = ChartViewportZoom::default();
        assert_eq!(zoom.level(), DEFAULT_LEVEL);
        assert!(zoom.is_default());
        assert_eq!(zoom.label(), "100%");
    }

    #[test]
    fn set_level_clamps_into_bounds() {
        let mut zoom = ChartViewportZoom::default();
        zoom.set_level(2.5);
        assert_eq!(zoom.level(), 2.5);
        assert!(!zoom.is_default());
        assert_eq!(zoom.label(), "250%");
        zoom.set_level(0.0);
        assert_eq!(zoom.level(), MIN_LEVEL);
        zoom.set_level(f64::INFINITY);
        assert_eq!(zoom.level(), MAX_LEVEL);
        zoom.set_level(f64::NAN);
        assert!(zoom.is_default());
    }

    #[test]
    fn reset_restores_default_window() {
        let mut zoom = ChartViewportZoom::default();
        zoom.set_level(4.0);
        assert!(!zoom.is_default());
        zoom.reset();
        assert!(zoom.is_default());
        assert_eq!(zoom.level(), DEFAULT_LEVEL);
        assert_eq!(zoom.label(), "100%");
    }

    #[test]
    fn reset_is_idempotent() {
        let mut zoom = ChartViewportZoom::default();
        zoom.reset();
        zoom.reset();
        assert!(zoom.is_default());
    }
}
