//! Kill switch — latchable emergency stop with JSON persistence.
//!
//! Rust port of 07_risk/risk/kill_switch.py. Engaged state gates NEW orders
//! only; it never deletes position state. State persists to a JSON file so a
//! restart cannot silently clear an engaged switch (fail-closed on reload).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// Kill switch scope.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum KillSwitchLevel {
    Global,
    Strategy,
    Broker,
}

impl KillSwitchLevel {
    /// Every level, in persistence order.
    pub const ALL: [KillSwitchLevel; 3] = [Self::Global, Self::Strategy, Self::Broker];

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Global => "global",
            Self::Strategy => "strategy",
            Self::Broker => "broker",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "global" => Some(Self::Global),
            "strategy" => Some(Self::Strategy),
            "broker" => Some(Self::Broker),
            _ => None,
        }
    }
}

/// One latched emergency stop.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KillSwitchState {
    pub engaged: bool,
    pub reason: String,
    pub engaged_at: String,
    pub level: KillSwitchLevel,
}

impl KillSwitchState {
    fn clear(level: KillSwitchLevel) -> Self {
        Self {
            engaged: false,
            reason: String::new(),
            engaged_at: String::new(),
            level,
        }
    }
}

/// Latchable emergency stop. Engaged state blocks all NEW orders.
#[derive(Debug, Clone)]
pub struct KillSwitch {
    path: Option<PathBuf>,
    state: HashMap<KillSwitchLevel, KillSwitchState>,
}

impl KillSwitch {
    /// In-memory switch with no persistence.
    pub fn in_memory() -> Self {
        Self {
            path: None,
            state: Self::empty_state(),
        }
    }

    /// Persisted switch. An existing file is loaded so engaged latches survive
    /// a restart; unreadable files leave every level disengaged.
    pub fn with_path(path: impl AsRef<Path>) -> Self {
        let mut switch = Self {
            path: Some(path.as_ref().to_path_buf()),
            state: Self::empty_state(),
        };
        switch.load();
        switch
    }

    fn empty_state() -> HashMap<KillSwitchLevel, KillSwitchState> {
        KillSwitchLevel::ALL
            .iter()
            .map(|&level| (level, KillSwitchState::clear(level)))
            .collect()
    }

    /// Seconds since the Unix epoch, as a string (no external time crate).
    fn now() -> String {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs().to_string())
            .unwrap_or_default()
    }

    fn load(&mut self) {
        let Some(path) = self.path.as_ref() else {
            return;
        };
        let Ok(text) = std::fs::read_to_string(path) else {
            return;
        };
        for level in KillSwitchLevel::ALL {
            let key = format!("\"{}\"", level.as_str());
            let Some(pos) = text.find(&key) else {
                continue;
            };
            let after = &text[pos + key.len()..];
            let Some(brace_open) = after.find('{') else {
                continue;
            };
            let Some(brace_close) = after[brace_open..].find('}') else {
                continue;
            };
            let section = &after[brace_open..brace_open + brace_close + 1];

            // check engaged
            let is_engaged = if let Some(e_pos) = section.find("\"engaged\"") {
                let rest = section[e_pos + 9..].trim_start();
                if let Some(c_pos) = rest.find(':') {
                    rest[c_pos + 1..].trim_start().starts_with("true")
                } else {
                    false
                }
            } else {
                false
            };

            if !is_engaged {
                continue;
            }

            let extract_str = |field: &str| -> String {
                let fkey = format!("\"{}\"", field);
                if let Some(fpos) = section.find(&fkey) {
                    let rest = section[fpos + fkey.len()..].trim_start();
                    if let Some(cpos) = rest.find(':') {
                        let val_part = rest[cpos + 1..].trim_start();
                        if val_part.starts_with('"') {
                            let inner = &val_part[1..];
                            if let Some(endq) = inner.find('"') {
                                return inner[..endq].replace("\\\"", "\"").replace("\\\\", "\\");
                            }
                        }
                    }
                }
                String::new()
            };

            let reason = extract_str("reason");
            let engaged_at = extract_str("engaged_at");
            self.state.insert(
                level,
                KillSwitchState {
                    engaged: true,
                    reason,
                    engaged_at,
                    level,
                },
            );
        }
    }

    fn save(&self) {
        let Some(path) = self.path.as_ref() else {
            return;
        };
        let mut buf = String::from("{\n");
        let all = KillSwitchLevel::ALL;
        for (i, level) in all.iter().enumerate() {
            let state = &self.state[level];
            let comma = if i + 1 < all.len() { "," } else { "" };
            let r_esc = state.reason.replace('\\', "\\\\").replace('"', "\\\"");
            let e_esc = state.engaged_at.replace('\\', "\\\\").replace('"', "\\\"");
            buf.push_str(&format!(
                "  \"{}\": {{\n    \"engaged\": {},\n    \"reason\": \"{}\",\n    \"engaged_at\": \"{}\"\n  }}{}\n",
                level.as_str(),
                state.engaged,
                r_esc,
                e_esc,
                comma
            ));
        }
        buf.push_str("}\n");
        if let Some(parent) = path.parent() {
            if std::fs::create_dir_all(parent).is_err() {
                return;
            }
        }
        let _ = std::fs::write(path, buf);
    }

    /// Latch the switch on.
    pub fn engage(&mut self, reason: impl Into<String>, level: KillSwitchLevel) {
        self.state.insert(
            level,
            KillSwitchState {
                engaged: true,
                reason: reason.into(),
                engaged_at: Self::now(),
                level,
            },
        );
        self.save();
    }

    /// Release the latch. Never automatic — an explicit call is required.
    pub fn disengage(&mut self, level: KillSwitchLevel) {
        self.state.insert(level, KillSwitchState::clear(level));
        self.save();
    }

    /// True when this level — or the global switch — is engaged.
    pub fn is_halted(&self, level: KillSwitchLevel) -> bool {
        self.state[&KillSwitchLevel::Global].engaged || self.state[&level].engaged
    }

    pub fn state(&self, level: KillSwitchLevel) -> &KillSwitchState {
        &self.state[&level]
    }
}

impl Default for KillSwitch {
    fn default() -> Self {
        Self::in_memory()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Unique scratch path without pulling in a temp-dir crate.
    fn scratch_path(name: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        std::env::temp_dir().join(format!("vayren_ks_{name}_{nanos}.json"))
    }

    #[test]
    fn starts_disengaged() {
        let switch = KillSwitch::in_memory();
        for level in KillSwitchLevel::ALL {
            assert!(!switch.is_halted(level));
        }
    }

    #[test]
    fn engage_and_disengage() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("manual stop", KillSwitchLevel::Global);
        assert!(switch.is_halted(KillSwitchLevel::Global));
        let state = switch.state(KillSwitchLevel::Global);
        assert_eq!(state.reason, "manual stop");
        assert!(!state.engaged_at.is_empty());

        switch.disengage(KillSwitchLevel::Global);
        assert!(!switch.is_halted(KillSwitchLevel::Global));
        assert!(switch.state(KillSwitchLevel::Global).reason.is_empty());
    }

    #[test]
    fn global_halts_every_level() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("strategy stop", KillSwitchLevel::Strategy);
        assert!(switch.is_halted(KillSwitchLevel::Strategy));
        assert!(!switch.is_halted(KillSwitchLevel::Broker));

        switch.engage("global stop", KillSwitchLevel::Global);
        assert!(switch.is_halted(KillSwitchLevel::Broker));
        assert!(switch.is_halted(KillSwitchLevel::Strategy));
    }

    #[test]
    fn level_names_round_trip() {
        for level in KillSwitchLevel::ALL {
            assert_eq!(KillSwitchLevel::parse(level.as_str()), Some(level));
        }
        assert_eq!(KillSwitchLevel::parse("nonsense"), None);
    }

    #[test]
    fn engaged_latch_survives_reload() {
        let path = scratch_path("reload");
        {
            let mut switch = KillSwitch::with_path(&path);
            switch.engage("persisted stop", KillSwitchLevel::Global);
        }
        assert!(path.is_file());

        let reloaded = KillSwitch::with_path(&path);
        assert!(reloaded.is_halted(KillSwitchLevel::Global));
        assert_eq!(
            reloaded.state(KillSwitchLevel::Global).reason,
            "persisted stop"
        );

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn disengage_persists_too() {
        let path = scratch_path("disengage");
        {
            let mut switch = KillSwitch::with_path(&path);
            switch.engage("stop", KillSwitchLevel::Broker);
            switch.disengage(KillSwitchLevel::Broker);
        }
        let reloaded = KillSwitch::with_path(&path);
        assert!(!reloaded.is_halted(KillSwitchLevel::Broker));

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn unreadable_file_leaves_switch_clear() {
        let path = scratch_path("garbage");
        std::fs::write(&path, "not json at all").unwrap();
        let switch = KillSwitch::with_path(&path);
        assert!(!switch.is_halted(KillSwitchLevel::Global));

        let _ = std::fs::remove_file(&path);
    }
}
