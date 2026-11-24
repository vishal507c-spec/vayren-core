//! Desktop UI settings for the VAYREN client (vayren-shell).
//!
//! Storage: `%USERPROFILE%\.vayren\ui_settings.json` (on other platforms the
//! home directory is used). Atomic write: the JSON is serialized to a sibling
//! temp file and `fs::rename`d over the target, so a crash mid-save can
//! never corrupt the previous settings file.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// MARKET chart timeframes the desktop client persists and restores. Same
/// set the backend `available_timeframes` list offers; an unsupported saved
/// value falls back to the backend default (see [`UiSettings::load`]).
pub const SUPPORTED_MARKET_TIMEFRAMES: [&str; 7] = ["15m", "30m", "45m", "1h", "2h", "4h", "1D"];

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct UiSettings {
    /// Saved MARKET chart timeframe (timeframe chip). `None` = never picked,
    /// backend default applies.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chart_timeframe: Option<String>,
}

impl UiSettings {
    /// Validate a timeframe against the supported set. Returns a normalized
    /// copy or `None` for unsupported values.
    pub fn validate_chart_timeframe(timeframe: &str) -> Option<String> {
        let tf = timeframe.trim();
        if tf.is_empty() {
            return None;
        }
        if SUPPORTED_MARKET_TIMEFRAMES.contains(&tf) {
            Some(tf.to_string())
        } else {
            None
        }
    }

    /// Load settings from `path`. Unreadable or corrupt files fall back to
    /// the default, and an unsupported saved timeframe is dropped to
    /// `None` — both cases keep the backend default.
    pub fn load(path: &Path) -> UiSettings {
        let Ok(text) = fs::read_to_string(path) else {
            return UiSettings::default();
        };
        let Ok(mut settings) = serde_json::from_str::<UiSettings>(&text) else {
            return UiSettings::default();
        };
        settings.chart_timeframe = settings
            .chart_timeframe
            .as_deref()
            .and_then(Self::validate_chart_timeframe);
        settings
    }

    /// Atomically write settings: serialize to `<target>.tmp` in the target
    /// directory, then `fs::rename` over the target. The temp name is
    /// derived from the target so concurrent writers to different files
    /// cannot race on a shared name.
    pub fn save_atomic(&self, path: &Path) -> io::Result<()> {
        let dir = path.parent().unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(dir)?;
        let body = serde_json::to_string_pretty(self).unwrap_or_else(|_| "{}".to_string());
        let tmp = dir.join(format!(
            "{}.tmp",
            path.file_name()
                .unwrap_or_else(|| path.as_os_str())
                .to_string_lossy()
        ));
        fs::write(&tmp, body)?;
        // Older-Windows `fs::rename` refuses when the target exists; remove
        // and retry so the save still lands.
        if fs::rename(&tmp, path).is_err() {
            let _ = fs::remove_file(path);
            fs::rename(&tmp, path)?;
        }
        Ok(())
    }
}

/// The default settings path: `%USERPROFILE%\.vayren\ui_settings.json`.
pub fn default_settings_path() -> PathBuf {
    home_dir().join(".vayren").join("ui_settings.json")
}

fn home_dir() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "vayren-ui-settings-{name}-{}.json",
            std::process::id()
        ))
    }

    #[test]
    fn supported_timeframes_are_the_market_set() {
        assert_eq!(
            SUPPORTED_MARKET_TIMEFRAMES,
            ["15m", "30m", "45m", "1h", "2h", "4h", "1D"]
        );
    }

    #[test]
    fn load_returns_default_when_file_is_missing() {
        let path = test_path("missing");
        let _ = fs::remove_file(&path);
        assert_eq!(UiSettings::load(&path), UiSettings::default());
    }

    #[test]
    fn save_then_load_roundtrips_chart_timeframe() {
        let path = test_path("roundtrip");
        let _ = fs::remove_file(&path);
        let mut settings = UiSettings::default();
        settings.chart_timeframe = UiSettings::validate_chart_timeframe("1D");
        settings.save_atomic(&path).unwrap();
        assert_eq!(
            UiSettings::load(&path).chart_timeframe.as_deref(),
            Some("1D")
        );
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn save_replaces_a_previous_value() {
        let path = test_path("replace");
        let _ = fs::remove_file(&path);
        let mut first = UiSettings::default();
        first.chart_timeframe = UiSettings::validate_chart_timeframe("45m");
        first.save_atomic(&path).unwrap();
        let mut second = UiSettings::default();
        second.chart_timeframe = UiSettings::validate_chart_timeframe("1D");
        second.save_atomic(&path).unwrap();
        assert_eq!(
            UiSettings::load(&path).chart_timeframe.as_deref(),
            Some("1D")
        );
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn invalid_or_garbage_files_fall_back_to_default() {
        let path = test_path("invalid");
        let _ = fs::remove_file(&path);
        fs::write(&path, "{ not json").unwrap();
        assert_eq!(UiSettings::load(&path), UiSettings::default());
        fs::write(&path, r#"{"chart_timeframe": "5m"}"#).unwrap();
        assert_eq!(
            UiSettings::load(&path).chart_timeframe,
            None,
            "unsupported saved timeframe falls back"
        );
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn save_survives_a_pre_existing_file() {
        let path = test_path("preexisting");
        let _ = fs::remove_file(&path);
        fs::write(&path, r#"{"chart_timeframe": "2h"}"#).unwrap();
        let mut settings = UiSettings::default();
        settings.chart_timeframe = UiSettings::validate_chart_timeframe("4h");
        settings.save_atomic(&path).unwrap();
        assert_eq!(
            UiSettings::load(&path).chart_timeframe.as_deref(),
            Some("4h")
        );
        let _ = fs::remove_file(&path);
    }
}
