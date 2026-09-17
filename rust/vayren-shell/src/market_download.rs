//! Historical-Download console view-model — the native Market's faithful port
//! of `02_data`'s `HistoricalDownloadPanel` (DownloadPanel + StatusView +
//! LogPanel) + `ProviderCredentialsDialog`. It owns ONLY presentation + the
//! calendar/credential modal interaction state. Every user action maps to an
//! existing public method/signal on the retained Qt panel or the provider
//! manager (see `slint_market_host.py`), so no download logic, validation,
//! engine, progress math, plan estimate or credential handling is duplicated
//! in Rust — this mirrors the Strategy Lab projection contract exactly.
//!
//! All financial/provider facts arrive from the backend bridge; missing
//! pieces degrade to honest absence (never invented). Formatting that the Qt
//! panel already performs (plan text, coverage text, status labels, clock,
//! byte sizes) is passed through verbatim so the native console reads the same.

/// One provider credential field (`CredentialField` projected for the modal).
#[derive(Debug, Clone, PartialEq)]
pub struct CredField {
    pub key: String,
    pub label: String,
    pub secret: bool,
    pub value: String,
    pub revealed: bool,
}

/// Download-plan estimate rows (verbatim from `DownloadPanel._update_plan`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PlanView {
    pub stocks: String,
    pub interval: String,
    pub range: String,
    pub days: String,
    pub rows: String,
    pub error: String,
}

/// Live run/complete/error/coverage/provider facts — label strings come from
/// the retained `StatusView` (never recomputed here).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct StatusViewFacts {
    pub mode: String, // idle|running|complete|failed|coverage
    pub status: String,
    pub symbol: String,
    pub interval: String,
    pub range: String,
    pub chunk: String,
    pub rows: String,
    pub coverage: String,
    pub progress_pct: f32,
    pub progress_note: String,
    pub perf_rows: String,
    pub perf_elapsed: String,
    pub perf_eta: String,
    pub perf_size: String,
    // complete card
    pub complete_status: String,
    pub complete_rows: String,
    pub complete_coverage: String,
    pub complete_duration: String,
    pub complete_size: String,
    // error card
    pub error_line: String,
    pub error_detail: String,
    pub error_details_shown: bool,
    // coverage card
    pub cov_symbol: String,
    pub cov_interval: String,
    pub cov_range: String,
    pub cov_coverage: String,
    pub cov_rows: String,
    // provider card
    pub provider_name: String,
    pub provider_status: String,
    pub provider_label: String,
    pub provider_detail: String,
    pub configure_visible: bool,
    pub advanced_visible: bool,
    pub advanced_open: bool,
    pub env_visible: bool,
    pub broker_caps: String,
    pub broker_error: String,
}

/// A broker selector entry (registry facts).
#[derive(Debug, Clone, PartialEq)]
pub struct BrokerChoice {
    pub name: String,
    pub display: String,
}

/// Full download-console state. `pending` wires are drained by the Qt host to
/// the retained panel/manager (never applied here beyond optimistic UI fields).
#[derive(Debug, Clone, PartialEq)]
pub struct DownloadState {
    pub open: bool,
    pub busy: bool,
    pub interval_index: usize,
    pub interval_items: Vec<String>, // display labels (INTERVAL_LABEL order)
    pub symbols: Vec<String>,
    pub selected: Vec<String>,
    pub filter: String,
    pub from_display: String,
    pub to_display: String,
    pub from_ymd: (i32, i32, i32),
    pub to_ymd: (i32, i32, i32),
    pub plan: PlanView,
    pub status: StatusViewFacts,
    pub brokers: Vec<BrokerChoice>,
    pub broker_index: usize,
    pub log: Vec<String>,
    pub log_expanded: bool,
    // native calendar (view-local): which field is open + shown month
    pub cal_field: String, // "" | "from" | "to"
    pub cal_year: i32,
    pub cal_month: i32, // 1..=12
    // credentials modal
    pub cred_open: bool,
    pub cred_fields: Vec<CredField>,
    pub cred_has_stored: bool,
    pub cred_status: String,
    pub test_running: bool,
    pub confirm_clear: bool,
}

impl Default for DownloadState {
    fn default() -> Self {
        Self {
            open: false,
            busy: false,
            interval_index: 0,
            interval_items: Vec::new(),
            symbols: Vec::new(),
            selected: Vec::new(),
            filter: String::new(),
            from_display: String::new(),
            to_display: String::new(),
            from_ymd: (2017, 1, 1),
            to_ymd: (2017, 1, 1),
            plan: PlanView::default(),
            status: StatusViewFacts::default(),
            brokers: Vec::new(),
            broker_index: 0,
            log: Vec::new(),
            log_expanded: false,
            cal_field: String::new(),
            cal_year: 2017,
            cal_month: 1,
            cred_open: false,
            cred_fields: Vec::new(),
            cred_has_stored: false,
            cred_status: String::new(),
            test_running: false,
            confirm_clear: false,
        }
    }
}

/// Console actions. Every variant maps 1:1 to a retained-widget method/signal
/// or a manager call in `apply_market_action` (never a native reimplementation).
#[derive(Debug, Clone, PartialEq)]
pub enum DownloadAction {
    Toggle,
    Interval(String), // selected display label (resolved to index here)
    ToggleSymbol(String),
    SelectAll,
    ClearAll,
    SetFilter(String),
    OpenCal(String), // "from" | "to"
    CloseCal,
    CalPrevMonth,
    CalNextMonth,
    PickDay(i32), // day-of-month of the shown month
    QuickRange(String),
    Download,
    CheckCoverage,
    Cancel,
    Retry,
    ViewCoverage,
    ToggleErrorDetails,
    ToggleAdvanced,
    ToggleLog,
    ClearLog,
    Broker(String),
    OpenCreds,
    CloseCreds,
    CredField(String, String),
    CredReveal(String),
    CredTest,
    CredSave,
    CredClear,
    CredConfirmClear(bool),
}

/// Local-only calendar month arithmetic (pure presentation).
fn shift_month(state: &mut DownloadState, delta: i32) {
    let mut m = state.cal_month + delta;
    let mut y = state.cal_year;
    while m < 1 {
        m += 12;
        y -= 1;
    }
    while m > 12 {
        m -= 12;
        y += 1;
    }
    state.cal_month = m;
    state.cal_year = y;
}

impl DownloadState {
    /// Apply a console action locally (optimistic UI: filter, calendar, modal
    /// open/close, chips) and report the wire string when the action must
    /// reach the retained backend. Returns `None` for pure view-local moves.
    /// Compact JSON map of the current credential field values, embedded in
    /// the test/save wire so the host can call the SAME manager methods the
    /// Qt dialog used (no engine, no duplicate validation).
    fn cred_payload(&self) -> String {
        let map: serde_json::Map<String, serde_json::Value> = self
            .cred_fields
            .iter()
            .map(|f| (f.key.clone(), serde_json::Value::String(f.value.clone())))
            .collect();
        serde_json::to_string(&serde_json::Value::Object(map)).unwrap_or_else(|_| "{}".to_string())
    }

    pub fn apply(&mut self, action: DownloadAction) -> Option<String> {
        match action {
            DownloadAction::Toggle => {
                self.open = !self.open;
                None
            }
            DownloadAction::Interval(label) => {
                if let Some(i) = self.interval_items.iter().position(|x| *x == label) {
                    self.interval_index = i;
                    Some(format!("dl:interval:{i}"))
                } else {
                    None
                }
            }
            DownloadAction::ToggleSymbol(sym) => {
                if let Some(pos) = self.selected.iter().position(|s| s == &sym) {
                    self.selected.remove(pos);
                } else {
                    self.selected.push(sym.clone());
                    self.selected.sort();
                }
                Some(format!("dl:select:{sym}"))
            }
            DownloadAction::SelectAll => {
                self.selected = self.symbols.clone();
                Some("dl:select-all".to_string())
            }
            DownloadAction::ClearAll => {
                self.selected.clear();
                Some("dl:clear-all".to_string())
            }
            DownloadAction::SetFilter(text) => {
                self.filter = text;
                None
            }
            DownloadAction::OpenCal(which) => {
                let (y, m, _) = if which == "to" {
                    self.to_ymd
                } else {
                    self.from_ymd
                };
                self.cal_year = y;
                self.cal_month = m;
                self.cal_field = which;
                None
            }
            DownloadAction::CloseCal => {
                self.cal_field.clear();
                None
            }
            DownloadAction::CalPrevMonth => {
                shift_month(self, -1);
                None
            }
            DownloadAction::CalNextMonth => {
                shift_month(self, 1);
                None
            }
            DownloadAction::PickDay(day) => {
                // The host turns (field, year, month, day) into a setDate on
                // the retained QDateEdit (which refreshes the plan); close the
                // calendar optimistically like a popup commit.
                let field = std::mem::take(&mut self.cal_field);
                Some(format!(
                    "dl:day:{field}:{}:{}:{}",
                    self.cal_year, self.cal_month, day
                ))
            }
            DownloadAction::QuickRange(caption) => {
                // Dates refresh from the snapshot; clear the calendar optimistically.
                self.cal_field.clear();
                Some(format!("dl:quick:{caption}"))
            }
            DownloadAction::Download => Some("dl:download".to_string()),
            DownloadAction::CheckCoverage => Some("dl:coverage".to_string()),
            DownloadAction::Cancel => Some("dl:cancel".to_string()),
            DownloadAction::Retry => Some("dl:retry".to_string()),
            DownloadAction::ViewCoverage => Some("dl:view-coverage".to_string()),
            DownloadAction::ToggleErrorDetails => {
                self.status.error_details_shown = !self.status.error_details_shown;
                Some("dl:error-details".to_string())
            }
            DownloadAction::ToggleAdvanced => {
                self.status.advanced_open = !self.status.advanced_open;
                Some("dl:advanced".to_string())
            }
            DownloadAction::ToggleLog => {
                self.log_expanded = !self.log_expanded;
                Some("dl:log".to_string())
            }
            DownloadAction::ClearLog => {
                self.log.clear();
                Some("dl:log-clear".to_string())
            }
            DownloadAction::Broker(display) => {
                if let Some(i) = self.brokers.iter().position(|b| b.display == display) {
                    self.broker_index = i;
                    Some(format!("dl:broker:{}", self.brokers[i].name))
                } else {
                    None
                }
            }
            DownloadAction::OpenCreds => {
                self.cred_open = true;
                Some("dl:creds-open".to_string())
            }
            DownloadAction::CloseCreds => {
                self.cred_open = false;
                self.confirm_clear = false;
                None
            }
            DownloadAction::CredField(key, value) => {
                if let Some(f) = self.cred_fields.iter_mut().find(|f| f.key == key) {
                    f.value = value;
                }
                None
            }
            DownloadAction::CredReveal(key) => {
                if let Some(f) = self.cred_fields.iter_mut().find(|f| f.key == key) {
                    f.revealed = !f.revealed;
                }
                None
            }
            DownloadAction::CredTest => {
                self.test_running = true;
                self.cred_status = "Testing connection…".to_string();
                Some(format!("dl:creds-test:{}", self.cred_payload()))
            }
            DownloadAction::CredSave => Some(format!("dl:creds-save:{}", self.cred_payload())),
            DownloadAction::CredClear => {
                self.confirm_clear = true;
                None
            }
            DownloadAction::CredConfirmClear(yes) => {
                self.confirm_clear = false;
                if yes {
                    Some("dl:creds-clear".to_string())
                } else {
                    None
                }
            }
        }
    }
}

// ── projection (flat render view bound by market.slint) ─────────────────────

#[derive(Debug, Clone, PartialEq)]
pub struct CalDay {
    pub day: i32, // 0 = leading/trailing blank
    pub label: String,
    pub x: f32, // column 0..6
    pub y: f32, // row 0..5
}

#[derive(Debug, Clone, PartialEq)]
pub struct DownloadView {
    pub open: bool,
    pub busy: bool,
    pub interval_index: usize,
    pub interval_label: String,
    pub interval_items: Vec<String>, // display labels (INTERVAL_LABEL order)
    pub selected_count: usize,
    pub selected_text: String, // "Selected: N" (Rust-formatted; no .slint interp)
    pub progress_text: String, // "NN%" run-card progress (Rust-formatted)
    pub stock_rows: Vec<DlStock>, // filtered checklist rows (symbol + checked)
    pub chips: Vec<String>,
    pub chips_note: String,
    pub filter: String,
    pub from_display: String,
    pub to_display: String,
    pub plan: PlanView,
    pub status: StatusViewFacts,
    pub brokers: Vec<String>,
    pub broker_index: usize,
    pub log: Vec<String>,
    pub log_expanded: bool,
    pub cal_open: bool,
    pub cal_title: String,
    pub cal_days: Vec<CalDay>,
    pub weekday_labels: Vec<String>,
    pub cred_open: bool,
    pub cred_fields: Vec<CredField>,
    pub cred_has_stored: bool,
    pub cred_status: String,
    pub confirm_clear: bool,
}

/// One checklist row (filtered universe + its selection flag).
#[derive(Debug, Clone, PartialEq)]
pub struct DlStock {
    pub symbol: String,
    pub selected: bool,
}

const WEEKDAYS: [&str; 7] = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

fn days_in_month(year: i32, month: i32) -> i32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 {
                29
            } else {
                28
            }
        }
        _ => 30,
    }
}

/// Leading blank cells before the 1st, in a Monday-first grid (0..6).
fn first_weekday_offset(year: i32, month: i32) -> i32 {
    // Tomohiko Sakamoto: day-of-week of the 1st (0 = Sunday .. 6 = Saturday).
    let t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4];
    let mut y = year;
    let m = month;
    if m < 3 {
        y -= 1;
    }
    let dow = (y + y / 4 - y / 100 + y / 400 + i32::from(t[(m - 1) as usize]) + 1) % 7;
    // Monday-first grid: Sunday(0) sits in the last column -> offset (dow+6)%7.
    (dow + 6).rem_euclid(7)
}

pub fn project_download(state: &DownloadState) -> DownloadView {
    let needle = state.filter.trim().to_uppercase();
    let stock_rows: Vec<DlStock> = state
        .symbols
        .iter()
        .filter(|s| needle.is_empty() || s.to_uppercase().contains(&needle))
        .map(|s| DlStock {
            symbol: s.clone(),
            selected: state.selected.iter().any(|x| x == s),
        })
        .collect();
    let selected_sorted = state.selected.clone();
    let (chips, chips_note) = if selected_sorted.is_empty() {
        (Vec::new(), String::new())
    } else if selected_sorted.len() <= 6 {
        (selected_sorted.clone(), String::new())
    } else {
        (
            Vec::new(),
            format!("{} stocks selected", selected_sorted.len()),
        )
    };
    let interval_label = state
        .interval_items
        .get(state.interval_index)
        .cloned()
        .unwrap_or_default();

    // Calendar grid (6 rows x 7 cols).
    let mut cal_days: Vec<CalDay> = Vec::new();
    if !state.cal_field.is_empty() {
        let total = days_in_month(state.cal_year, state.cal_month);
        let lead = first_weekday_offset(state.cal_year, state.cal_month);
        let mut cells = lead + total;
        while cells % 7 != 0 {
            cells += 1;
        }
        let cols = 7.0f32;
        for i in 0..cells {
            let day_num = i - lead;
            let real = day_num >= 1 && day_num <= total;
            let col = (i as f32) % cols;
            let row = (i as f32 / cols).floor();
            cal_days.push(CalDay {
                day: if real { day_num } else { 0 },
                label: if real {
                    day_num.to_string()
                } else {
                    String::new()
                },
                x: col,
                y: row,
            });
        }
    }
    let cal_title = format!("{} {}", month_name(state.cal_month), state.cal_year);

    DownloadView {
        open: state.open,
        busy: state.busy,
        interval_index: state.interval_index,
        interval_label,
        interval_items: state.interval_items.clone(),
        selected_count: state.selected.len(),
        selected_text: format!("Selected: {}", state.selected.len()),
        progress_text: format!("{:.0}%", state.status.progress_pct),
        stock_rows,
        chips,
        chips_note,
        filter: state.filter.clone(),
        from_display: state.from_display.clone(),
        to_display: state.to_display.clone(),
        plan: state.plan.clone(),
        status: state.status.clone(),
        brokers: state.brokers.iter().map(|b| b.display.clone()).collect(),
        broker_index: state.broker_index,
        log: state.log.clone(),
        log_expanded: state.log_expanded,
        cal_open: !state.cal_field.is_empty(),
        cal_title,
        cal_days,
        weekday_labels: WEEKDAYS.iter().map(|s| s.to_string()).collect(),
        cred_open: state.cred_open,
        cred_fields: state.cred_fields.clone(),
        cred_has_stored: state.cred_has_stored,
        cred_status: state.cred_status.clone(),
        confirm_clear: state.confirm_clear,
    }
}

fn month_name(m: i32) -> &'static str {
    [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ][(m.clamp(1, 12) - 1) as usize]
}

// ── snapshot parse (retained-panel facts -> DownloadState) ──────────────────

fn dl_str(v: &serde_json::Value, key: &str) -> String {
    v.get(key)
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string()
}

fn dl_f32(v: &serde_json::Value, key: &str) -> f32 {
    v.get(key).and_then(|x| x.as_f64()).unwrap_or(0.0) as f32
}

fn dl_bool(v: &serde_json::Value, key: &str) -> bool {
    v.get(key).and_then(|x| x.as_bool()).unwrap_or(false)
}

fn dl_list(v: &serde_json::Value, key: &str) -> Vec<String> {
    v.get(key)
        .and_then(|x| x.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|t| t.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// Merge the `download` object from the bridge snapshot into `state`,
/// PRESERVING all view-local fields (open toggle, calendar, modal, filter,
/// optimistic selection) — the backend facts overwrite only what Qt owns.
pub fn apply_download_snapshot(state: &mut DownloadState, value: &serde_json::Value) {
    state.busy = dl_bool(value, "busy");
    state.interval_items = dl_list(value, "interval_items");
    if let Some(i) = value.get("interval_index").and_then(|x| x.as_u64()) {
        state.interval_index = i as usize;
    }
    state.symbols = dl_list(value, "universe");
    // Backend selection wins over optimistic ONLY when it differs (the host
    // round-trip); otherwise keep optimistic so the checkbox feels instant.
    if let Some(sel) = value.get("selected").and_then(|x| x.as_array()) {
        state.selected = sel
            .iter()
            .filter_map(|t| t.as_str().map(str::to_string))
            .collect();
    }
    state.from_display = dl_str(value, "from_display");
    state.to_display = dl_str(value, "to_display");
    if let Some(cal) = value.get("calendar").and_then(|x| x.as_object()) {
        for (key, slot) in [("from", &mut state.from_ymd), ("to", &mut state.to_ymd)] {
            if let Some(f) = cal.get(key) {
                let y = f.get("year").and_then(|x| x.as_i64()).unwrap_or(2017) as i32;
                let m = f.get("month").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                let d = f.get("day").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                *slot = (y, m, d);
            }
        }
    }
    if let Some(plan) = value.get("plan") {
        state.plan = PlanView {
            stocks: dl_str(plan, "stocks"),
            interval: dl_str(plan, "interval"),
            range: dl_str(plan, "range"),
            days: dl_str(plan, "days"),
            rows: dl_str(plan, "rows"),
            error: dl_str(plan, "error"),
        };
    }
    if let Some(st) = value.get("status") {
        let s = &mut state.status;
        s.mode = dl_str(st, "mode");
        s.status = dl_str(st, "run_status");
        s.symbol = dl_str(st, "run_symbol");
        s.interval = dl_str(st, "run_interval");
        s.range = dl_str(st, "run_range");
        s.chunk = dl_str(st, "run_chunk");
        s.rows = dl_str(st, "run_rows");
        s.coverage = dl_str(st, "run_coverage");
        s.progress_pct = dl_f32(st, "progress_pct");
        s.progress_note = dl_str(st, "progress_note");
        s.perf_rows = dl_str(st, "perf_rows");
        s.perf_elapsed = dl_str(st, "perf_elapsed");
        s.perf_eta = dl_str(st, "perf_eta");
        s.perf_size = dl_str(st, "perf_size");
        s.complete_status = dl_str(st, "complete_status");
        s.complete_rows = dl_str(st, "complete_rows");
        s.complete_coverage = dl_str(st, "complete_coverage");
        s.complete_duration = dl_str(st, "complete_duration");
        s.complete_size = dl_str(st, "complete_size");
        s.error_line = dl_str(st, "error_line");
        s.error_detail = dl_str(st, "error_detail");
        s.cov_symbol = dl_str(st, "cov_symbol");
        s.cov_interval = dl_str(st, "cov_interval");
        s.cov_range = dl_str(st, "cov_range");
        s.cov_coverage = dl_str(st, "cov_coverage");
        s.cov_rows = dl_str(st, "cov_rows");
        s.provider_name = dl_str(st, "provider_name");
        s.provider_status = dl_str(st, "provider_status");
        s.provider_label = dl_str(st, "provider_label");
        s.provider_detail = dl_str(st, "provider_detail");
        s.configure_visible = dl_bool(st, "configure_visible");
        s.advanced_visible = dl_bool(st, "advanced_visible");
        s.advanced_open = dl_bool(st, "advanced_open");
        s.env_visible = dl_bool(st, "env_visible");
        s.broker_caps = dl_str(st, "broker_caps");
        s.broker_error = dl_str(st, "broker_error");
    }
    if let Some(brokers) = value.get("brokers").and_then(|x| x.as_array()) {
        state.brokers = brokers
            .iter()
            .map(|b| BrokerChoice {
                name: dl_str(b, "name"),
                display: dl_str(b, "display"),
            })
            .collect();
        if let Some(i) = value.get("broker_index").and_then(|x| x.as_u64()) {
            state.broker_index = i as usize;
        }
    }
    state.log = dl_list(value, "log");
    if let Some(creds) = value.get("credentials") {
        if let Some(fields) = creds.get("fields").and_then(|x| x.as_array()) {
            // Merge provider field schema onto any typed-in local values.
            let local = state.cred_fields.clone();
            state.cred_fields = fields
                .iter()
                .map(|f| {
                    let key = dl_str(f, "key");
                    let previous = local.iter().find(|l| l.key == key);
                    CredField {
                        secret: dl_bool(f, "secret"),
                        label: dl_str(f, "label"),
                        value: previous
                            .map(|p| p.value.clone())
                            .unwrap_or_else(|| dl_str(f, "value")),
                        revealed: previous.map(|p| p.revealed).unwrap_or(false),
                        key,
                    }
                })
                .collect();
        }
        state.cred_has_stored = dl_bool(creds, "has_stored");
        if !state.cred_open {
            state.cred_status = dl_str(creds, "status");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn monday_first_grid_offsets() {
        // 2024-09-01 was a Sunday -> Monday-first grid leads with 6 blanks.
        assert_eq!(first_weekday_offset(2024, 9), 6);
        // 2017-01-01 was a Sunday -> 6 blanks.
        assert_eq!(first_weekday_offset(2017, 1), 6);
        // 2024-09-02 Monday -> offset for the 1st is 6; verify a Monday-first
        // month: 2023-10-01 was a Sunday (6), 2023-11-01 Wednesday (2).
        assert_eq!(first_weekday_offset(2023, 11), 2);
    }

    #[test]
    fn leap_and_month_lengths() {
        assert_eq!(days_in_month(2024, 2), 29);
        assert_eq!(days_in_month(2023, 2), 28);
        assert_eq!(days_in_month(2000, 2), 29);
        assert_eq!(days_in_month(1900, 2), 28);
        assert_eq!(days_in_month(2024, 4), 30);
        assert_eq!(days_in_month(2024, 12), 31);
    }

    #[test]
    fn toggle_and_calendar_are_view_local() {
        let mut st = DownloadState::default();
        assert_eq!(st.apply(DownloadAction::Toggle), None);
        assert!(st.open);
        assert_eq!(st.apply(DownloadAction::OpenCal("from".to_string())), None);
        assert_eq!(st.cal_field, "from");
        st.cal_year = 2024;
        st.cal_month = 1;
        assert_eq!(st.apply(DownloadAction::CalPrevMonth), None);
        assert_eq!((st.cal_year, st.cal_month), (2023, 12));
        assert_eq!(
            st.apply(DownloadAction::PickDay(15)),
            Some("dl:day:from:2023:12:15".to_string())
        );
        assert!(st.cal_field.is_empty());
    }

    #[test]
    fn selection_optimistic_and_queued() {
        let mut st = DownloadState {
            symbols: vec!["A".to_string(), "B".to_string()],
            ..Default::default()
        };
        assert_eq!(
            st.apply(DownloadAction::ToggleSymbol("A".to_string())),
            Some("dl:select:A".to_string())
        );
        assert_eq!(st.selected, vec!["A".to_string()]);
        st.apply(DownloadAction::ToggleSymbol("B".to_string()));
        assert_eq!(st.selected, vec!["A".to_string(), "B".to_string()]);
        assert_eq!(
            st.apply(DownloadAction::ClearAll),
            Some("dl:clear-all".to_string())
        );
        assert!(st.selected.is_empty());
        assert_eq!(
            st.apply(DownloadAction::SelectAll),
            Some("dl:select-all".to_string())
        );
        assert_eq!(st.selected.len(), 2);
    }

    #[test]
    fn credentials_modal_serializes_values() {
        let mut st = DownloadState {
            cred_fields: vec![CredField {
                key: "api_key".to_string(),
                label: "API key".to_string(),
                secret: false,
                value: "K".to_string(),
                revealed: false,
            }],
            ..Default::default()
        };
        let wire = st.apply(DownloadAction::CredTest).unwrap();
        assert!(wire.starts_with("dl:creds-test:"));
        assert!(wire.contains("\"api_key\":\"K\""));
        // reveal + edit are local (no wire).
        assert_eq!(
            st.apply(DownloadAction::CredReveal("api_key".to_string())),
            None
        );
        assert!(st.cred_fields[0].revealed);
        assert_eq!(
            st.apply(DownloadAction::CredField(
                "api_key".to_string(),
                "K2".to_string()
            )),
            None
        );
        assert_eq!(st.cred_fields[0].value, "K2");
        // clear asks for confirmation first (local), then emits on yes.
        assert_eq!(st.apply(DownloadAction::CredClear), None);
        assert!(st.confirm_clear);
        assert_eq!(
            st.apply(DownloadAction::CredConfirmClear(true)),
            Some("dl:creds-clear".to_string())
        );
    }

    #[test]
    fn snapshot_merges_backend_and_keeps_local() {
        let mut st = DownloadState::default();
        st.open = true; // user opened via rail
        let value: serde_json::Value = serde_json::from_str(
            r#"{"busy":false,"interval_items":["1m","5m","15m","30m","1h"],"interval_index":2,
               "universe":["A","B"],"selected":["A"],"from_display":"01 Jan 2017",
               "to_display":"15 Sep 2026","plan":{"stocks":"1","interval":"15m","range":"x",
               "days":"2400","rows":"~576K","error":""},
               "status":{"mode":"idle","provider_status":"● Not Configured"},
               "brokers":[{"name":"zerodha","display":"Zerodha"}],"broker_index":0,
               "log":["→ ready"],"credentials":{"fields":[{"key":"api_key","label":"API key",
               "secret":true,"value":"stored"}],"has_stored":true,"status":"● Configured"}}"#,
        )
        .unwrap();
        apply_download_snapshot(&mut st, &value);
        assert!(st.open); // view-local preserved
        assert_eq!(st.interval_index, 2);
        assert_eq!(st.selected, vec!["A".to_string()]);
        assert_eq!(st.plan.rows, "~576K");
        assert_eq!(st.status.provider_status, "● Not Configured");
        assert_eq!(st.brokers.len(), 1);
        assert_eq!(st.log, vec!["→ ready".to_string()]);
        assert!(st.cred_fields[0].secret);
        assert!(st.cred_has_stored);
        let view = project_download(&st);
        assert_eq!(view.interval_label, "15m");
        assert_eq!(view.chips, vec!["A".to_string()]);
        assert_eq!(view.selected_count, 1);
    }
}
