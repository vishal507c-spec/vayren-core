//! Kill switch — latchable emergency stop with JSON persistence authority.
//!
//! Rust port of `07_risk/risk/kill_switch.py`. Engaged state gates NEW orders
//! only; it never deletes position state. The latch table, the level
//! vocabulary, the timestamp format, the reload rule (what survives a restart)
//! and the serialized file shape all live here — the Python side is a handle
//! wrapper that only issues `Path.read_text`/`write_text` for these bytes, so
//! an engaged switch cannot be silently cleared by a restart (fail-closed).
//!
//! Serialized text is byte-compatible with the historical
//! `json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=True)` output, and
//! the parser accepts documents written by the previous implementation.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::risk_engine::py_float;

/// Kill switch scope.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum KillSwitchLevel {
    Global,
    Strategy,
    Broker,
}

impl KillSwitchLevel {
    /// Every level, in the public `LEVELS` declaration order.
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

/// Which persisted field a read is asking for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KillSwitchField {
    Reason,
    EngagedAt,
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

/// Outcome of feeding persisted text back in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LoadOutcome {
    /// The document was walked; engaged latches were restored.
    Applied,
    /// Not valid JSON: every level stayed exactly as it was (historical
    /// `except Exception: return`).
    Garbage,
}

/// Latchable emergency stop. Engaged state blocks all NEW orders.
#[derive(Debug, Clone)]
pub struct KillSwitch {
    state: HashMap<KillSwitchLevel, KillSwitchState>,
}

/// Every rejection the Python wrapper must surface, with the historical text.
pub type KillSwitchError = String;

fn unknown_level(level: &str) -> KillSwitchError {
    format!("unknown kill-switch level: {level}")
}

fn not_walkable(value: &JsonValue) -> KillSwitchError {
    format!(
        "'{}' object has no attribute 'get'",
        python_type_name(value)
    )
}

impl KillSwitch {
    /// Fresh switch with every level disengaged.
    pub fn in_memory() -> Self {
        Self {
            state: Self::empty_state(),
        }
    }

    fn empty_state() -> HashMap<KillSwitchLevel, KillSwitchState> {
        KillSwitchLevel::ALL
            .iter()
            .map(|&level| (level, KillSwitchState::clear(level)))
            .collect()
    }

    /// Resolve a level name or produce the historical refusal message.
    fn level_or_error(level: &str) -> Result<KillSwitchLevel, KillSwitchError> {
        KillSwitchLevel::parse(level).ok_or_else(|| unknown_level(level))
    }

    /// UTC timestamp in the exact shape `datetime.now(UTC).isoformat()` writes:
    /// `YYYY-MM-DDTHH:MM:SS`, plus `.ffffff` only when microseconds are nonzero,
    /// plus the `+00:00` offset.
    fn now() -> String {
        let Ok(elapsed) = SystemTime::now().duration_since(UNIX_EPOCH) else {
            return String::new();
        };
        Self::format_utc(elapsed.as_secs() as i64, elapsed.subsec_micros())
    }

    fn format_utc(secs: i64, micros: u32) -> String {
        let days = secs.div_euclid(86_400);
        let seconds_of_day = secs.rem_euclid(86_400);
        let (year, month, day) = civil_from_days(days);
        let fraction = if micros == 0 {
            String::new()
        } else {
            format!(".{micros:06}")
        };
        format!(
            "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}{fraction}+00:00",
            seconds_of_day / 3600,
            (seconds_of_day % 3600) / 60,
            seconds_of_day % 60,
        )
    }

    /// Latch the switch on. The timestamp is stamped here, never by a caller.
    pub fn engage(&mut self, reason: &str, level: &str) -> Result<(), KillSwitchError> {
        let parsed = Self::level_or_error(level)?;
        self.state.insert(
            parsed,
            KillSwitchState {
                engaged: true,
                reason: reason.to_string(),
                engaged_at: Self::now(),
                level: parsed,
            },
        );
        Ok(())
    }

    /// Release the latch. Never automatic — an explicit call is required.
    pub fn disengage(&mut self, level: &str) -> Result<(), KillSwitchError> {
        let parsed = Self::level_or_error(level)?;
        self.state.insert(parsed, KillSwitchState::clear(parsed));
        Ok(())
    }

    /// True when this level — or the global switch — is engaged.
    pub fn is_halted(&self, level: &str) -> Result<bool, KillSwitchError> {
        let parsed = Self::level_or_error(level)?;
        Ok(self.state[&KillSwitchLevel::Global].engaged || self.state[&parsed].engaged)
    }

    pub fn state_engaged(&self, level: &str) -> Result<bool, KillSwitchError> {
        let parsed = Self::level_or_error(level)?;
        Ok(self.state[&parsed].engaged)
    }

    /// Read one persisted field of one level's latch.
    pub fn field(&self, level: &str, field: KillSwitchField) -> Result<String, KillSwitchError> {
        let parsed = Self::level_or_error(level)?;
        let state = &self.state[&parsed];
        Ok(match field {
            KillSwitchField::Reason => state.reason.clone(),
            KillSwitchField::EngagedAt => state.engaged_at.clone(),
        })
    }

    /// Persisted document text: the exact historical
    /// `json.dumps(..., indent=2, sort_keys=True, ensure_ascii=True)` bytes
    /// (LF line endings — platform newline translation stays with the writer).
    pub fn serialize(&self) -> String {
        let mut out = String::from("{\n");
        for (index, level) in PERSIST_ORDER.iter().enumerate() {
            let state = &self.state[level];
            out.push_str(&format!("  {}: {{\n", json_escape(level.as_str())));
            out.push_str(&format!("    \"engaged\": {},\n", state.engaged));
            out.push_str(&format!(
                "    \"engaged_at\": {},\n",
                json_escape(&state.engaged_at)
            ));
            out.push_str(&format!("    \"reason\": {}\n", json_escape(&state.reason)));
            out.push_str(if index + 1 < PERSIST_ORDER.len() {
                "  },\n"
            } else {
                "  }\n"
            });
        }
        out.push('}');
        out
    }

    /// Restore latches from persisted text. Only a truthy `engaged` produces a
    /// latch; an unparseable document changes nothing; a document that cannot be
    /// walked reports the historical `AttributeError` text after having applied
    /// the levels it reached first, exactly as the Python loop did.
    pub fn apply_saved(&mut self, text: &str) -> Result<LoadOutcome, KillSwitchError> {
        let Ok(parsed) = json_parse(text) else {
            return Ok(LoadOutcome::Garbage);
        };
        let root = match &parsed {
            JsonValue::Object(entries) => entries,
            other => return Err(not_walkable(other)),
        };
        for level in KillSwitchLevel::ALL {
            let entry = match lookup(root, level.as_str()) {
                // An absent level behaves like `{}`: no latch, no error.
                None => continue,
                Some(JsonValue::Object(fields)) => fields,
                Some(other) => return Err(not_walkable(other)),
            };
            if !lookup(entry, "engaged").is_some_and(python_truthy) {
                continue;
            }
            let text_of =
                |key: &str| -> String { lookup(entry, key).map_or_else(String::new, python_str) };
            self.state.insert(
                level,
                KillSwitchState {
                    engaged: true,
                    reason: text_of("reason"),
                    engaged_at: text_of("engaged_at"),
                    level,
                },
            );
        }
        Ok(LoadOutcome::Applied)
    }
}

/// `json.dumps(..., sort_keys=True)` walks levels alphabetically.
const PERSIST_ORDER: [KillSwitchLevel; 3] = [
    KillSwitchLevel::Broker,
    KillSwitchLevel::Global,
    KillSwitchLevel::Strategy,
];

/// Proleptic-Gregorian civil date from days since 1970-01-01 (Howard Hinnant's
/// `civil_from_days`), so UTC timestamps need no external crate.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = (shifted - era * 146_097) as u64;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era as i64 + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * month_prime + 2) / 5 + 1) as u32;
    let month = if month_prime < 10 {
        month_prime + 3
    } else {
        month_prime - 9
    } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

// ── JSON: the persisted file is the safety contract, so parse and escape it
//    properly instead of pattern-matching on substrings. ───────────────────

#[derive(Debug, Clone, PartialEq)]
enum JsonValue {
    Null,
    Bool(bool),
    /// Integer literal, kept verbatim for `str()` rendering.
    Int(String),
    Float(f64),
    Str(String),
    Array(Vec<JsonValue>),
    Object(Vec<(String, JsonValue)>),
}

fn lookup<'a>(entries: &'a [(String, JsonValue)], key: &str) -> Option<&'a JsonValue> {
    // A duplicate key resolves to the last occurrence, like `json.loads`.
    entries
        .iter()
        .rev()
        .find(|(name, _)| name == key)
        .map(|(_, value)| value)
}

fn python_type_name(value: &JsonValue) -> &'static str {
    match value {
        JsonValue::Null => "NoneType",
        JsonValue::Bool(_) => "bool",
        JsonValue::Int(_) => "int",
        JsonValue::Float(_) => "float",
        JsonValue::Str(_) => "str",
        JsonValue::Array(_) => "list",
        JsonValue::Object(_) => "dict",
    }
}

/// `bool(value)` for a decoded JSON value.
fn python_truthy(value: &JsonValue) -> bool {
    match value {
        JsonValue::Null => false,
        JsonValue::Bool(flag) => *flag,
        JsonValue::Int(raw) => raw != "0" && raw != "-0",
        JsonValue::Float(value) => *value != 0.0,
        JsonValue::Str(text) => !text.is_empty(),
        JsonValue::Array(items) => !items.is_empty(),
        JsonValue::Object(entries) => !entries.is_empty(),
    }
}

/// `str(value)` for a decoded JSON value. Containers report their element count
/// rather than Python's `repr()` rendering (which needs Unicode printability
/// tables); a container can only reach `reason`/`engaged_at` through a
/// hand-edited file, and such an entry is still latched exactly the same.
fn python_str(value: &JsonValue) -> String {
    match value {
        JsonValue::Null => "None".to_string(),
        JsonValue::Bool(flag) => {
            if *flag {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        JsonValue::Int(raw) => {
            if raw == "-0" {
                "0".to_string()
            } else {
                raw.clone()
            }
        }
        JsonValue::Float(value) => py_float(*value),
        JsonValue::Str(text) => text.clone(),
        JsonValue::Array(items) => format!("[{}]", items.len()),
        JsonValue::Object(entries) => format!("{{{}}}", entries.len()),
    }
}

pub(crate) fn json_escape(text: &str) -> String {
    let mut out = String::from("\"");
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if (c as u32) > 0x7e => {
                let code = c as u32;
                if code > 0xFFFF {
                    let shifted = code - 0x1_0000;
                    out.push_str(&format!(
                        "\\u{:04x}\\u{:04x}",
                        0xD800 + (shifted >> 10),
                        0xDC00 + (shifted & 0x3FF)
                    ));
                } else {
                    out.push_str(&format!("\\u{code:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

struct JsonParser<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> JsonParser<'a> {
    fn new(text: &'a str) -> Self {
        Self {
            bytes: text.as_bytes(),
            pos: 0,
        }
    }

    fn parse_document(&mut self) -> Result<JsonValue, ()> {
        self.skip_ws();
        let value = self.parse_value()?;
        self.skip_ws();
        if self.pos != self.bytes.len() {
            return Err(());
        }
        Ok(value)
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.pos).copied()
    }

    fn take(&mut self) -> Option<u8> {
        let byte = self.peek()?;
        self.pos += 1;
        Some(byte)
    }

    fn skip_ws(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.pos += 1;
        }
    }

    fn expect(&mut self, byte: u8) -> Result<(), ()> {
        if self.take() == Some(byte) {
            Ok(())
        } else {
            Err(())
        }
    }

    fn literal(&mut self, word: &str) -> Result<(), ()> {
        if self.bytes[self.pos..].starts_with(word.as_bytes()) {
            self.pos += word.len();
            Ok(())
        } else {
            Err(())
        }
    }

    fn parse_value(&mut self) -> Result<JsonValue, ()> {
        match self.peek() {
            Some(b'{') => self.parse_object(),
            Some(b'[') => self.parse_array(),
            Some(b'"') => self.parse_string().map(JsonValue::Str),
            Some(b't') => {
                self.literal("true")?;
                Ok(JsonValue::Bool(true))
            }
            Some(b'f') => {
                self.literal("false")?;
                Ok(JsonValue::Bool(false))
            }
            Some(b'n') => {
                self.literal("null")?;
                Ok(JsonValue::Null)
            }
            Some(byte) if byte == b'-' || byte.is_ascii_digit() => self.parse_number(),
            _ => Err(()),
        }
    }

    fn parse_number(&mut self) -> Result<JsonValue, ()> {
        let start = self.pos;
        self.pos += 1;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.pos += 1;
        }
        if !matches!(self.peek(), Some(b'.' | b'e' | b'E')) {
            let raw = std::str::from_utf8(&self.bytes[start..self.pos]).map_err(|_| ())?;
            return Ok(JsonValue::Int(raw.to_string()));
        }
        while matches!(
            self.peek(),
            Some(b'.' | b'e' | b'E' | b'+' | b'-' | b'0'..=b'9')
        ) {
            self.pos += 1;
        }
        let raw = std::str::from_utf8(&self.bytes[start..self.pos]).map_err(|_| ())?;
        raw.parse::<f64>().map(JsonValue::Float).map_err(|_| ())
    }

    fn parse_array(&mut self) -> Result<JsonValue, ()> {
        self.expect(b'[')?;
        let mut items = Vec::new();
        self.skip_ws();
        if self.peek() == Some(b']') {
            self.pos += 1;
            return Ok(JsonValue::Array(items));
        }
        loop {
            self.skip_ws();
            items.push(self.parse_value()?);
            self.skip_ws();
            match self.take() {
                Some(b',') => continue,
                Some(b']') => return Ok(JsonValue::Array(items)),
                _ => return Err(()),
            }
        }
    }

    fn parse_object(&mut self) -> Result<JsonValue, ()> {
        self.expect(b'{')?;
        let mut entries = Vec::new();
        self.skip_ws();
        if self.peek() == Some(b'}') {
            self.pos += 1;
            return Ok(JsonValue::Object(entries));
        }
        loop {
            self.skip_ws();
            let key = self.parse_string()?;
            self.skip_ws();
            self.expect(b':')?;
            self.skip_ws();
            let value = self.parse_value()?;
            entries.push((key, value));
            self.skip_ws();
            match self.take() {
                Some(b',') => continue,
                Some(b'}') => return Ok(JsonValue::Object(entries)),
                _ => return Err(()),
            }
        }
    }

    fn parse_string(&mut self) -> Result<String, ()> {
        self.expect(b'"')?;
        let mut out = String::new();
        loop {
            match self.take() {
                None => return Err(()),
                Some(b'"') => return Ok(out),
                Some(b'\\') => match self.take() {
                    Some(b'"') => out.push('"'),
                    Some(b'\\') => out.push('\\'),
                    Some(b'/') => out.push('/'),
                    Some(b'n') => out.push('\n'),
                    Some(b'r') => out.push('\r'),
                    Some(b't') => out.push('\t'),
                    Some(b'b') => out.push('\u{8}'),
                    Some(b'f') => out.push('\u{c}'),
                    Some(b'u') => {
                        let code = self.hex4()?;
                        let scalar = if (0xD800..0xDC00).contains(&code) {
                            // Surrogate pair, as `ensure_ascii` writes astral code points.
                            if self.take() != Some(b'\\') || self.take() != Some(b'u') {
                                return Err(());
                            }
                            let low = self.hex4()?;
                            if !(0xDC00..0xE000).contains(&low) {
                                return Err(());
                            }
                            0x1_0000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                        } else {
                            code
                        };
                        out.push(char::from_u32(scalar).ok_or(())?);
                    }
                    _ => return Err(()),
                },
                Some(byte) if byte < 0x80 => out.push(byte as char),
                Some(first) => {
                    let width = match first {
                        b if b >= 0xF0 => 4,
                        b if b >= 0xE0 => 3,
                        b if b >= 0xC0 => 2,
                        _ => return Err(()),
                    };
                    if self.pos + width - 1 > self.bytes.len() {
                        return Err(());
                    }
                    let slice = &self.bytes[self.pos - 1..self.pos + width - 1];
                    out.push_str(std::str::from_utf8(slice).map_err(|_| ())?);
                    self.pos += width - 1;
                }
            }
        }
    }

    fn hex4(&mut self) -> Result<u32, ()> {
        let mut value = 0u32;
        for _ in 0..4 {
            let digit = match self.take() {
                Some(b) if (b'0'..=b'9').contains(&b) => (b - b'0') as u32,
                Some(b) if (b'a'..=b'f').contains(&b) => (b - b'a' + 10) as u32,
                Some(b) if (b'A'..=b'F').contains(&b) => (b - b'A' + 10) as u32,
                _ => return Err(()),
            };
            value = value * 16 + digit;
        }
        Ok(value)
    }
}

fn json_parse(text: &str) -> Result<JsonValue, ()> {
    JsonParser::new(text).parse_document()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Round-trip a switch through its own persisted text. The Python wrapper
    /// does the file read/write; here the text stands in for the file.
    fn reload(switch: &KillSwitch) -> KillSwitch {
        let mut reopened = KillSwitch::in_memory();
        assert_eq!(
            reopened.apply_saved(&switch.serialize()),
            Ok(LoadOutcome::Applied)
        );
        reopened
    }

    #[test]
    fn starts_disengaged() {
        let switch = KillSwitch::in_memory();
        for level in KillSwitchLevel::ALL {
            assert_eq!(switch.is_halted(level.as_str()), Ok(false));
        }
    }

    #[test]
    fn engage_and_disengage() {
        let mut switch = KillSwitch::in_memory();
        assert!(switch.engage("manual stop", "global").is_ok());
        assert_eq!(switch.is_halted("global"), Ok(true));
        assert_eq!(switch.state_engaged("global"), Ok(true));
        assert_eq!(
            switch.field("global", KillSwitchField::Reason).unwrap(),
            "manual stop"
        );
        assert!(!switch
            .field("global", KillSwitchField::EngagedAt)
            .unwrap()
            .is_empty());

        assert!(switch.disengage("global").is_ok());
        assert_eq!(switch.is_halted("global"), Ok(false));
        assert_eq!(switch.state_engaged("global"), Ok(false));
        assert_eq!(
            switch.field("global", KillSwitchField::Reason).unwrap(),
            String::new()
        );
    }

    #[test]
    fn global_halts_every_level() {
        let mut switch = KillSwitch::in_memory();
        assert!(switch.engage("strategy stop", "strategy").is_ok());
        assert_eq!(switch.is_halted("strategy"), Ok(true));
        assert_eq!(switch.is_halted("broker"), Ok(false));

        assert!(switch.engage("global stop", "global").is_ok());
        assert_eq!(switch.is_halted("broker"), Ok(true));
        assert_eq!(switch.is_halted("strategy"), Ok(true));
    }

    #[test]
    fn unknown_level_is_refused_with_the_historical_message() {
        let mut switch = KillSwitch::in_memory();
        assert_eq!(
            switch.engage("x", "nope").unwrap_err(),
            "unknown kill-switch level: nope"
        );
        for refused in [
            switch.disengage("nope"),
            switch.is_halted("nope").map(|_| ()),
            switch.state_engaged("nope").map(|_| ()),
            switch.field("nope", KillSwitchField::Reason).map(|_| ()),
        ] {
            assert_eq!(refused.unwrap_err(), "unknown kill-switch level: nope");
        }
        // Nothing was latched by a refused call.
        assert_eq!(switch.is_halted("global"), Ok(false));
    }

    #[test]
    fn refused_engage_leaves_the_previous_latch_alone() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("keep", "strategy").unwrap();
        assert!(switch.engage("dropped", "nope").is_err());
        assert_eq!(
            switch.field("strategy", KillSwitchField::Reason).unwrap(),
            "keep"
        );
    }

    #[test]
    fn level_names_are_authoritative() {
        assert_eq!(
            KillSwitchLevel::ALL.map(|level| level.as_str()),
            ["global", "strategy", "broker"]
        );
        for level in KillSwitchLevel::ALL {
            assert_eq!(KillSwitchLevel::parse(level.as_str()), Some(level));
        }
        assert_eq!(KillSwitchLevel::parse("nonsense"), None);
        assert_eq!(KillSwitchLevel::parse(""), None);
    }

    #[test]
    fn engaged_latch_survives_reload() {
        let mut switch = KillSwitch::in_memory();
        assert!(switch.engage("persisted stop", "global").is_ok());
        let reloaded = reload(&switch);
        assert_eq!(reloaded.is_halted("global"), Ok(true));
        assert_eq!(
            reloaded.field("global", KillSwitchField::Reason).unwrap(),
            "persisted stop"
        );
    }

    #[test]
    fn disengage_persists_too() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("stop", "broker").unwrap();
        switch.disengage("broker").unwrap();
        assert_eq!(reload(&switch).is_halted("broker"), Ok(false));
    }

    #[test]
    fn garbage_leaves_switch_clear() {
        let mut switch = KillSwitch::in_memory();
        assert_eq!(
            switch.apply_saved("not json at all"),
            Ok(LoadOutcome::Garbage)
        );
        assert_eq!(switch.is_halted("global"), Ok(false));
    }

    #[test]
    fn garbage_does_not_clear_an_engaged_latch() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("keep me", "global").unwrap();
        assert_eq!(switch.apply_saved("{oops"), Ok(LoadOutcome::Garbage));
        assert_eq!(switch.is_halted("global"), Ok(true));
    }

    #[test]
    fn unwalkable_documents_report_attribute_error() {
        let mut switch = KillSwitch::in_memory();
        for (document, name) in [
            ("[1, 2]", "list"),
            ("5", "int"),
            ("null", "NoneType"),
            ("true", "bool"),
            (r#""halt""#, "str"),
            ("1.5", "float"),
        ] {
            assert_eq!(
                switch.apply_saved(document),
                Err(format!("'{name}' object has no attribute 'get'")),
                "{document}"
            );
        }
        // A dict root whose entry is not a dict fails on that entry instead.
        assert_eq!(
            switch.apply_saved(r#"{"global": 5, "strategy": {}, "broker": {}}"#),
            Err("'int' object has no attribute 'get'".to_string())
        );
        assert_eq!(switch.is_halted("global"), Ok(false));
    }

    #[test]
    fn unwalkable_document_still_applies_the_levels_it_reached() {
        // The historical loop mutated `self._state` per level before raising.
        let mut switch = KillSwitch::in_memory();
        assert_eq!(
            switch.apply_saved(
                r#"{"global": {"engaged": true, "reason": "applied"}, "strategy": 7}"#
            ),
            Err("'int' object has no attribute 'get'".to_string())
        );
        assert_eq!(switch.is_halted("global"), Ok(true));
        assert_eq!(
            switch.field("global", KillSwitchField::Reason).unwrap(),
            "applied"
        );
    }

    #[test]
    fn engaged_uses_python_truthiness() {
        for (raw, halted) in [
            (r#"{"global": {"engaged": true}}"#, true),
            (r#"{"global": {"engaged": 1}}"#, true),
            (r#"{"global": {"engaged": "false"}}"#, true),
            (r#"{"global": {"engaged": [0]}}"#, true),
            (r#"{"global": {"engaged": {"a": 1}}}"#, true),
            (r#"{"global": {"engaged": 0.001}}"#, true),
            (r#"{"global": {"engaged": false}}"#, false),
            (r#"{"global": {"engaged": 0}}"#, false),
            (r#"{"global": {"engaged": -0}}"#, false),
            (r#"{"global": {"engaged": 0.0}}"#, false),
            (r#"{"global": {"engaged": ""}}"#, false),
            (r#"{"global": {"engaged": []}}"#, false),
            (r#"{"global": {"engaged": {}}}"#, false),
            (r#"{"global": {"engaged": null}}"#, false),
            (r#"{"global": {}}"#, false),
            ("{}", false),
        ] {
            let mut switch = KillSwitch::in_memory();
            assert_eq!(switch.apply_saved(raw), Ok(LoadOutcome::Applied), "{raw}");
            assert_eq!(switch.is_halted("global"), Ok(halted), "{raw}");
        }
    }

    #[test]
    fn persisted_scalars_coerce_like_python_str() {
        let mut switch = KillSwitch::in_memory();
        switch
            .apply_saved(
                r#"{"global": {"engaged": true, "reason": 5, "engaged_at": 1e16},
                "strategy": {"engaged": true, "reason": null, "engaged_at": true},
                "broker": {"engaged": true}}"#,
            )
            .unwrap();
        assert_eq!(
            switch.field("global", KillSwitchField::Reason).unwrap(),
            "5"
        );
        assert_eq!(
            switch.field("global", KillSwitchField::EngagedAt).unwrap(),
            "1e+16"
        );
        assert_eq!(
            switch.field("strategy", KillSwitchField::Reason).unwrap(),
            "None"
        );
        assert_eq!(
            switch
                .field("strategy", KillSwitchField::EngagedAt)
                .unwrap(),
            "True"
        );
        // A missing field is `str("")` — the `.get(key, "")` default.
        assert_eq!(
            switch.field("broker", KillSwitchField::Reason).unwrap(),
            String::new()
        );
    }

    #[test]
    fn float_coercion_matches_python_repr() {
        for (raw, expected) in [
            ("5.0", "5.0"),
            ("1e16", "1e+16"),
            ("1.5", "1.5"),
            ("-0.0", "-0.0"),
            ("1e-5", "1e-05"),
        ] {
            let mut switch = KillSwitch::in_memory();
            switch
                .apply_saved(&format!(
                    r#"{{"global": {{"engaged": true, "reason": {raw}}}}}"#
                ))
                .unwrap();
            assert_eq!(
                switch.field("global", KillSwitchField::Reason).unwrap(),
                expected,
                "{raw}"
            );
        }
    }

    #[test]
    fn only_engaged_levels_are_restored() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("s", "strategy").unwrap();
        switch.engage("g", "global").unwrap();
        let mut reopened = reload(&switch);
        assert_eq!(reopened.state_engaged("strategy"), Ok(true));
        assert_eq!(reopened.state_engaged("broker"), Ok(false));
        assert!(reopened.disengage("strategy").is_ok());
        assert_eq!(reopened.is_halted("global"), Ok(true));
        // Global still dominates the level that was never engaged.
        assert_eq!(reopened.is_halted("broker"), Ok(true));
        assert!(reopened.disengage("global").is_ok());
        assert_eq!(reopened.is_halted("broker"), Ok(false));
    }

    #[test]
    fn serialized_text_is_stdlib_json_canonical() {
        let mut switch = KillSwitch::in_memory();
        switch
            .engage("halt \"q\" — अ\nx{y}\t\u{8}\u{c}", "strategy")
            .unwrap();
        let text = switch.serialize();
        let levels: Vec<&str> = text
            .lines()
            .filter(|line| line.starts_with("  \""))
            .map(|line| line.split('"').nth(1).unwrap())
            .collect();
        assert_eq!(levels, vec!["broker", "global", "strategy"]);
        let fields: Vec<&str> = text
            .lines()
            .filter(|line| line.starts_with("    \""))
            .map(|line| line.split('"').nth(1).unwrap())
            .collect();
        assert_eq!(
            fields,
            ["engaged", "engaged_at", "reason"]
                .into_iter()
                .cycle()
                .take(9)
                .collect::<Vec<_>>()
        );
        assert!(text.starts_with("{\n"));
        assert!(text.ends_with("\n}"));
        assert!(!text.ends_with('\n'), "json.dumps adds no trailing newline");
        assert!(text.contains(r#"\u2014"#) && text.contains(r#"\u0905"#));
        assert!(
            text.contains(r#"\b"#) && text.contains(r#"\t"#),
            "named escapes win"
        );
        assert!(
            !text.contains('—'),
            "ensure_ascii never emits raw non-ASCII"
        );
        assert!(text.contains(r#"halt \"q\""#));
        assert!(text.contains(r"\nx{y}\t"));
    }

    #[test]
    fn parses_documents_written_by_the_previous_implementation() {
        // Byte-for-byte what `json.dumps(indent=2, sort_keys=True,
        // ensure_ascii=True)` + Windows newline translation produced.
        let historical = "{\r\n  \"broker\": {\r\n    \"engaged\": false,\r\n    \"engaged_at\": \"\",\r\n    \"reason\": \"\"\r\n  },\r\n  \"global\": {\r\n    \"engaged\": true,\r\n    \"engaged_at\": \"2026-09-20T10:49:21.289897+00:00\",\r\n    \"reason\": \"halt \\\"q\\\" \\u2014 \\u0905\\nx{y}\"\r\n  },\r\n  \"strategy\": {\r\n    \"engaged\": true,\r\n    \"engaged_at\": \"2026-09-20T10:49:21.288863+00:00\",\r\n    \"reason\": \"global stop\"\r\n  }\r\n}";
        let mut switch = KillSwitch::in_memory();
        assert_eq!(switch.apply_saved(historical), Ok(LoadOutcome::Applied));
        assert_eq!(switch.is_halted("global"), Ok(true));
        assert_eq!(switch.is_halted("strategy"), Ok(true));
        assert_eq!(
            switch.is_halted("broker"),
            Ok(true),
            "global dominates every level"
        );
        assert_eq!(
            switch.field("global", KillSwitchField::Reason).unwrap(),
            "halt \"q\" — अ\nx{y}"
        );
        assert_eq!(
            switch.field("global", KillSwitchField::EngagedAt).unwrap(),
            "2026-09-20T10:49:21.289897+00:00"
        );
    }

    #[test]
    fn strings_cannot_confuse_the_parser() {
        // Substring scanning used to read keys and braces inside string values.
        let tricky = r#"{"global": {"reason": "\"strategy\": {\"engaged\": true}", "engaged": true},
                         "strategy": {"engaged": false}, "broker": {"engaged": false}}"#;
        let mut switch = KillSwitch::in_memory();
        assert_eq!(switch.apply_saved(tricky), Ok(LoadOutcome::Applied));
        assert_eq!(switch.is_halted("global"), Ok(true));
        assert_eq!(switch.state_engaged("strategy"), Ok(false));
        assert_eq!(switch.state_engaged("broker"), Ok(false));
        assert_eq!(
            switch.field("global", KillSwitchField::Reason).unwrap(),
            r#""strategy": {"engaged": true}"#
        );
    }

    #[test]
    fn duplicate_keys_resolve_to_last_like_json_loads() {
        let mut switch = KillSwitch::in_memory();
        switch
            .apply_saved(
                r#"{"global": {"engaged": true, "engaged": false}, "global": {"engaged": true}}"#,
            )
            .unwrap();
        assert_eq!(switch.is_halted("global"), Ok(true));
    }

    #[test]
    fn whitespace_and_nesting_tolerate_hand_editing() {
        let mut switch = KillSwitch::in_memory();
        assert_eq!(
            switch.apply_saved(
                "  {  \"global\"  :  {  \"engaged\"  :  true  ,  \"reason\"  :  \"spaced\"  }  }  "
            ),
            Ok(LoadOutcome::Applied)
        );
        assert_eq!(switch.is_halted("global"), Ok(true));
        assert_eq!(
            switch.field("global", KillSwitchField::Reason).unwrap(),
            "spaced"
        );
    }

    #[test]
    fn unterminated_documents_count_as_garbage() {
        for raw in [
            r#"{"global": {"engaged": true"#,
            r#"{"global": }"#,
            r#"{"global": {"engaged": tru}"#,
            r#"{"global" "engaged": true}"#,
            r#"{\"reason\": 5}"#,
            "[]",
            "{\"a\": /*c*/ 1}",
        ] {
            let mut switch = KillSwitch::in_memory();
            switch.engage("keep", "global").unwrap();
            let outcome = switch.apply_saved(raw);
            assert!(
                outcome.is_err() || outcome == Ok(LoadOutcome::Garbage),
                "{raw} parsed as {outcome:?}"
            );
        }
    }

    #[test]
    fn surrogate_pairs_round_trip() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("halt \u{1F300}", "global").unwrap();
        let text = switch.serialize();
        assert!(text.contains(r#"\ud83c\udf00"#), "{text}");
        assert_eq!(
            reload(&switch)
                .field("global", KillSwitchField::Reason)
                .unwrap(),
            "halt \u{1F300}"
        );
    }

    #[test]
    fn utc_timestamps_match_python_isoformat() {
        assert_eq!(
            KillSwitch::format_utc(1_789_901_361, 289_897),
            "2026-09-20T10:49:21.289897+00:00"
        );
        // Zero microseconds drop the fractional part entirely, as isoformat does.
        assert_eq!(
            KillSwitch::format_utc(1_789_901_361, 0),
            "2026-09-20T10:49:21+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(0, 1),
            "1970-01-01T00:00:00.000001+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(-1, 999_999),
            "1969-12-31T23:59:59.999999+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(1_435_708_800, 0),
            "2015-07-01T00:00:00+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(1_709_337_600, 0),
            "2024-03-02T00:00:00+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(1_709_251_200, 0),
            "2024-03-01T00:00:00+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(1_709_164_800, 0),
            "2024-02-29T00:00:00+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(1_583_020_800, 0),
            "2020-03-01T00:00:00+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(1_262_304_000, 0),
            "2010-01-01T00:00:00+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(2_147_483_648, 0),
            "2038-01-19T03:14:08+00:00"
        );
        assert_eq!(
            KillSwitch::format_utc(198_612_259_200, 0),
            "8263-10-10T00:00:00+00:00"
        );
    }

    #[test]
    fn engaged_at_is_a_python_iso_timestamp() {
        let mut switch = KillSwitch::in_memory();
        switch.engage("now", "global").unwrap();
        let stamp = switch.field("global", KillSwitchField::EngagedAt).unwrap();
        let expected = if stamp.contains('.') { 32 } else { 25 };
        assert_eq!(stamp.len(), expected, "{stamp}");
        assert_eq!(&stamp[4..5], "-");
        assert_eq!(&stamp[7..8], "-");
        assert_eq!(&stamp[10..11], "T");
        assert_eq!(&stamp[13..14], ":");
        assert_eq!(&stamp[16..17], ":");
        assert!(stamp.ends_with("+00:00"), "{stamp}");
    }

    #[test]
    fn text_moves_bytes_kernel_stays_out_of_fs() {
        // A scratch file proves the kernel text is the whole contract: writing
        // it and reading it back restores the latch without any Rust file IO.
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let path = std::env::temp_dir().join(format!("vayren_ks_text_{nanos}.json"));
        let mut switch = KillSwitch::in_memory();
        switch.engage("disk stop", "broker").unwrap();
        std::fs::write(&path, switch.serialize()).unwrap();
        let read = std::fs::read_to_string(&path).unwrap();
        let mut reopened = KillSwitch::in_memory();
        reopened.apply_saved(&read).unwrap();
        assert_eq!(reopened.is_halted("broker"), Ok(true));
        let _ = std::fs::remove_file(&path);
    }
}
