//! RISK decision orchestration — the `RiskEngine._evaluate` checklist.
//!
//! The 13 scalar gates live in the auto-generated, hands-off [`crate::risk`]
//! kernel, reached here in-process via `evaluate_risk_kernel` — the same math
//! the Python path reaches through FFI (`vy_risk_kernel`). No duplicate
//! calculations. What this module owns, verbatim from `07_risk/risk/engine.py`
//! and `session.py`:
//!
//! | Python | Rust (here) |
//! |---|---|
//! | `RiskPolicy` / `RiskRequest` / `RiskCheck` / `RiskDecision` | same structs, same defaults |
//! | 18 named checks in fixed order | `evaluate` pushes the same 18 in order |
//! | reason strings (`"in cooldown"`, …) | byte-identical, incl. `None` spellings |
//! | kill/session/clock/instrument/duplicate gates | ported (`session.rs` logic inline) |
//! | `_seen_intent_ids` (approved only) | caller-visible `seen` set on the engine |
//! | `evaluate` fail-closed wrapper | structural: no panics, no exceptions |
//!
//! Stays Python: `KillSwitch` persistence/orchestration (compose via the
//! `kill_halted` input — see [`crate::kill_switch`]), settings/credentials,
//! UI. RISK has no EventBus traffic (manifest: nothing consumed/produced),
//! so no bus integration is added — Phase 6 is intentionally a no-op.

use std::collections::HashSet;

use crate::risk::{
    evaluate_risk_kernel, RiskKernelInputs, BIT_BROKER_HEALTH, BIT_CAPITAL, BIT_COOLDOWN,
    BIT_DAILY_LOSS, BIT_EXPOSURE, BIT_FRESH_DATA, BIT_NOTIONAL, BIT_ORDER_QTY, BIT_ORDER_RATE,
    BIT_POSITION, BIT_SANITY, BIT_SPREAD, BIT_STRATEGY_LOSS,
};

// ── models (mirror risk/models.py) ────────────────────────────────────────

/// Hard limits enforced before ANY order. `None` disables the check it
/// guards, except structural checks (duplicate, kill switch) always on.
#[derive(Debug, Clone)]
pub struct RiskPolicy {
    pub max_position_qty: f64,
    pub max_order_qty: f64,
    pub max_notional: Option<f64>,
    pub max_exposure_pct: Option<f64>,
    pub daily_loss_limit: Option<f64>,
    pub strategy_loss_limit: Option<f64>,
    pub allowed_symbols: Vec<String>,
    pub spread_limit_pct: Option<f64>,
    pub require_fresh_data_seconds: Option<f64>,
    pub session_start: Option<String>,
    pub session_end: Option<String>,
    pub cooldown_seconds: f64,
    pub max_orders_per_day: Option<i64>,
}

impl Default for RiskPolicy {
    fn default() -> Self {
        Self {
            max_position_qty: 1000.0,
            max_order_qty: 500.0,
            max_notional: None,
            max_exposure_pct: None,
            daily_loss_limit: None,
            strategy_loss_limit: None,
            allowed_symbols: Vec::new(),
            spread_limit_pct: None,
            require_fresh_data_seconds: Some(60.0),
            session_start: None,
            session_end: None,
            cooldown_seconds: 0.0,
            max_orders_per_day: None,
        }
    }
}

/// One order intent asking for a verdict. Plain data, no behavior.
#[derive(Debug, Clone)]
pub struct RiskRequest {
    pub intent_id: String,
    pub strategy_id: String,
    pub symbol: String,
    pub side: String,
    pub quantity: f64,
    pub price: f64,
    pub timestamp: String,
    pub position_qty: f64,
    pub day_pnl: f64,
    pub strategy_day_pnl: f64,
    pub equity: f64,
    pub available_capital: f64,
    pub spread_pct: Option<f64>,
    pub data_age_seconds: Option<f64>,
    pub broker_healthy: bool,
    pub orders_today: i64,
    pub last_order_epoch: Option<f64>,
    pub now_epoch: f64,
}

/// One named gate result inside a decision (audit trail).
#[derive(Debug, Clone, PartialEq)]
pub struct RiskCheck {
    pub name: &'static str,
    pub passed: bool,
    pub detail: String,
}

/// Verdict for one request. Denied unless every check passes.
#[derive(Debug, Clone, PartialEq)]
pub struct RiskDecision {
    pub approved: bool,
    pub intent_id: String,
    pub reasons: Vec<String>,
    pub checks: Vec<RiskCheck>,
}

// ── session.py ────────────────────────────────────────────────────────────

/// Extract `HH:MM` from an ISO-like timestamp (chars 11..16), else short/empty
/// (never panics — mirrors `str[11:16]` slicing, where a short input yields a
/// short slice that simply fails the bound comparison, exactly like Python).
fn hhmm(timestamp: &str) -> String {
    timestamp.chars().skip(11).take(5).collect()
}

/// True inside the allowed window (inclusive bounds). `None`/`None` disables.
pub fn within_session(timestamp: &str, start: Option<&str>, end: Option<&str>) -> bool {
    if start.is_none() && end.is_none() {
        return true;
    }
    let hhmm = hhmm(timestamp);
    if let Some(s) = start {
        if hhmm.as_str() < s {
            return false;
        }
    }
    if let Some(e) = end {
        if hhmm.as_str() > e {
            return false;
        }
    }
    true
}

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

/// Parse an ISO-8601 timestamp to epoch seconds: `YYYY-MM-DD<T>HH:MM:SS[.frac]`
/// with `Z`/`±HH:MM`/`±HHMM`/`±HH` offset or naive (= UTC, like Python's
/// `fromisoformat` + UTC-assume). `None` on anything unparseable (fail-closed).
fn parse_epoch(text: &str) -> Option<f64> {
    let t = text
        .strip_suffix('Z')
        .map(|s| format!("{s}+00:00"))
        .unwrap_or_else(|| text.to_string());
    let b = t.as_bytes();
    if b.len() < 19 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let (ys, ms, ds) = (&t[0..4], &t[5..7], &t[8..10]);
    if !digits(ys) || !digits(ms) || !digits(ds) {
        return None;
    }
    let (y, m, d): (i64, i64, i64) = (ys.parse().ok()?, ms.parse().ok()?, ds.parse().ok()?);
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    // Any single-char date/time separator (mirrors `fromisoformat`).
    if b.len() < 19 || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let (hs, ns, ss) = (&t[11..13], &t[14..16], &t[17..19]);
    if !digits(hs) || !digits(ns) || !digits(ss) {
        return None;
    }
    let (h, n, mut s): (i64, i64, f64) = (hs.parse().ok()?, ns.parse().ok()?, ss.parse().ok()?);
    if h > 23 || n > 59 {
        return None;
    }
    let mut rest = &t[19..];
    if let Some(frac) = rest.strip_prefix('.') {
        let end = frac
            .find(|c: char| !c.is_ascii_digit())
            .unwrap_or(frac.len());
        if end == 0 {
            return None;
        }
        let scale = 10f64.powi(end as i32);
        s += frac[..end].parse::<f64>().ok()? / scale;
        rest = &frac[end..];
    }
    let mut offset_secs = 0i64;
    if !rest.is_empty() {
        let sb = rest.as_bytes();
        if sb[0] != b'+' && sb[0] != b'-' {
            return None;
        }
        let sign = if sb[0] == b'+' { 1i64 } else { -1i64 };
        let body = &rest[1..];
        let (oh, om) = if body.len() == 5 && body.as_bytes()[2] == b':' {
            (
                body[0..2].parse::<i64>().ok()?,
                body[3..5].parse::<i64>().ok()?,
            )
        } else if body.len() == 4 && digits(body) {
            (
                body[0..2].parse::<i64>().ok()?,
                body[2..4].parse::<i64>().ok()?,
            )
        } else if body.len() == 2 && digits(body) {
            (body.parse::<i64>().ok()?, 0)
        } else {
            return None;
        };
        if oh > 23 || om > 59 {
            return None;
        }
        offset_secs = sign * (oh * 3600 + om * 60);
    }
    let days = days_from_civil(y, m, d);
    Some(days as f64 * 86_400.0 + h as f64 * 3600.0 + n as f64 * 60.0 + s - offset_secs as f64)
}

/// Reject event timestamps impossibly far in the future. Unparseable input
/// fails closed. Mirrors `clock_sane` (default 300s skew).
pub fn clock_sane(timestamp: &str, now_epoch: f64, max_future_skew_seconds: f64) -> bool {
    match parse_epoch(timestamp) {
        Some(event) => event <= now_epoch + max_future_skew_seconds,
        None => false,
    }
}

// ── Python float formatting for reason strings ────────────────────────────

/// `repr(float)` spelling: `5.0` not `5`, `1e+16` not `1e16`, `nan`/`inf`.
/// Only reason-string cosmetics — decisions never depend on it.
pub fn py_float(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_string();
    }
    if value.is_infinite() {
        return if value > 0.0 {
            "inf".to_string()
        } else {
            "-inf".to_string()
        };
    }
    let text = format!("{value:?}");
    let Some(epos) = text.find('e') else {
        return text;
    };
    let (mantissa, exp) = text.split_at(epos);
    let exp: i32 = exp[1..].parse().unwrap_or(0);
    format!("{mantissa}e{:+03}", exp)
}

fn py_opt(value: Option<f64>) -> String {
    match value {
        Some(v) => py_float(v),
        None => "None".to_string(),
    }
}

// ── engine ────────────────────────────────────────────────────────────────

/// Stateless policy evaluation + duplicate-order memory (mirrors `RiskEngine`;
/// only approved intent IDs are remembered).
pub struct RiskEngine {
    policy: RiskPolicy,
    seen: HashSet<String>,
}

impl RiskEngine {
    pub fn new(policy: RiskPolicy) -> Self {
        Self {
            policy,
            seen: HashSet::new(),
        }
    }

    pub fn policy(&self) -> &RiskPolicy {
        &self.policy
    }

    /// Approve only when every applicable check passes. Never panics —
    /// the structural equivalent of the Python fail-closed wrapper.
    pub fn evaluate(&mut self, request: &RiskRequest, kill_halted: bool) -> RiskDecision {
        let policy = &self.policy;
        let mask = evaluate_risk_kernel(&RiskKernelInputs {
            request_broker_healthy: request.broker_healthy,
            policy_require_fresh_data_seconds_present: policy.require_fresh_data_seconds.is_some(),
            request_data_age_seconds_present: request.data_age_seconds.is_some(),
            request_data_age_seconds: request.data_age_seconds.unwrap_or(0.0),
            policy_require_fresh_data_seconds: policy.require_fresh_data_seconds.unwrap_or(0.0),
            policy_spread_limit_pct_present: policy.spread_limit_pct.is_some(),
            request_spread_pct_present: request.spread_pct.is_some(),
            request_spread_pct: request.spread_pct.unwrap_or(0.0),
            policy_spread_limit_pct: policy.spread_limit_pct.unwrap_or(0.0),
            policy_cooldown_seconds: policy.cooldown_seconds,
            request_last_order_epoch_present: request.last_order_epoch.is_some(),
            request_now_epoch: request.now_epoch,
            request_last_order_epoch: request.last_order_epoch.unwrap_or(0.0),
            policy_max_orders_per_day_present: policy.max_orders_per_day.is_some(),
            request_orders_today: request.orders_today,
            policy_max_orders_per_day: policy.max_orders_per_day.unwrap_or(0),
            request_quantity: request.quantity,
            request_price: request.price,
            policy_max_order_qty: policy.max_order_qty,
            policy_max_notional_present: policy.max_notional.is_some(),
            policy_max_notional: policy.max_notional.unwrap_or(0.0),
            request_position_qty: request.position_qty,
            side_is_buy: request.side == "BUY",
            policy_max_position_qty: policy.max_position_qty,
            policy_max_exposure_pct_present: policy.max_exposure_pct.is_some(),
            request_equity: request.equity,
            policy_max_exposure_pct: policy.max_exposure_pct.unwrap_or(0.0),
            policy_daily_loss_limit_present: policy.daily_loss_limit.is_some(),
            request_day_pnl: request.day_pnl,
            policy_daily_loss_limit: policy.daily_loss_limit.unwrap_or(0.0),
            policy_strategy_loss_limit_present: policy.strategy_loss_limit.is_some(),
            request_strategy_day_pnl: request.strategy_day_pnl,
            policy_strategy_loss_limit: policy.strategy_loss_limit.unwrap_or(0.0),
            request_available_capital: request.available_capital,
        });
        let bit = |b: u32| mask & b != 0;

        let mut checks: Vec<RiskCheck> = Vec::with_capacity(18);
        let mut ok = true;
        let mut push =
            |checks: &mut Vec<RiskCheck>, name: &'static str, passed: bool, detail: String| {
                checks.push(RiskCheck {
                    name,
                    passed,
                    detail,
                });
                ok &= passed;
            };

        push(
            &mut checks,
            "kill_switch",
            !kill_halted,
            if kill_halted {
                "kill switch engaged".to_string()
            } else {
                String::new()
            },
        );
        push(
            &mut checks,
            "broker_health",
            bit(BIT_BROKER_HEALTH),
            if request.broker_healthy {
                String::new()
            } else {
                "broker unhealthy".to_string()
            },
        );

        let in_session = within_session(
            &request.timestamp,
            policy.session_start.as_deref(),
            policy.session_end.as_deref(),
        );
        push(
            &mut checks,
            "session",
            in_session,
            if in_session {
                String::new()
            } else {
                "outside trading session".to_string()
            },
        );
        let sane = clock_sane(&request.timestamp, request.now_epoch, 300.0);
        push(
            &mut checks,
            "clock",
            sane,
            if sane {
                String::new()
            } else {
                "clock anomaly".to_string()
            },
        );

        if !policy.allowed_symbols.is_empty() {
            let allowed = policy.allowed_symbols.iter().any(|s| s == &request.symbol);
            push(
                &mut checks,
                "instrument",
                allowed,
                if allowed {
                    String::new()
                } else {
                    format!("symbol not allowed: {}", request.symbol)
                },
            );
        } else {
            push(
                &mut checks,
                "instrument",
                true,
                "universe unrestricted".to_string(),
            );
        }
        if self.seen.contains(&request.intent_id) {
            push(
                &mut checks,
                "duplicate",
                false,
                format!("intent already decided: {}", request.intent_id),
            );
        } else {
            push(&mut checks, "duplicate", true, String::new());
        }

        if let Some(limit) = policy.require_fresh_data_seconds {
            let fresh = request.data_age_seconds.is_some_and(|a| a <= limit);
            push(
                &mut checks,
                "fresh_data",
                bit(BIT_FRESH_DATA),
                if fresh {
                    String::new()
                } else {
                    format!(
                        "stale market data: age={}",
                        py_opt(request.data_age_seconds)
                    )
                },
            );
        } else {
            push(
                &mut checks,
                "fresh_data",
                true,
                "staleness gate disabled".to_string(),
            );
        }
        if let Some(limit) = policy.spread_limit_pct {
            let tight = request.spread_pct.is_some_and(|s| s <= limit);
            push(
                &mut checks,
                "spread",
                bit(BIT_SPREAD),
                if tight {
                    String::new()
                } else {
                    format!("spread too wide: {}", py_opt(request.spread_pct))
                },
            );
        } else {
            push(
                &mut checks,
                "spread",
                true,
                "spread gate disabled".to_string(),
            );
        }
        if policy.cooldown_seconds > 0.0 && request.last_order_epoch.is_some() {
            let last = request.last_order_epoch.unwrap_or(0.0);
            let cooled = request.now_epoch - last >= policy.cooldown_seconds;
            push(
                &mut checks,
                "cooldown",
                bit(BIT_COOLDOWN),
                if cooled {
                    String::new()
                } else {
                    "in cooldown".to_string()
                },
            );
        } else {
            push(&mut checks, "cooldown", true, String::new());
        }
        if let Some(max) = policy.max_orders_per_day {
            let under = request.orders_today < max;
            push(
                &mut checks,
                "order_rate",
                bit(BIT_ORDER_RATE),
                if under {
                    String::new()
                } else {
                    "max orders per day reached".to_string()
                },
            );
        } else {
            push(&mut checks, "order_rate", true, String::new());
        }
        push(
            &mut checks,
            "sanity",
            bit(BIT_SANITY),
            if bit(BIT_SANITY) {
                String::new()
            } else {
                "non-positive quantity or price".to_string()
            },
        );
        push(
            &mut checks,
            "order_qty",
            bit(BIT_ORDER_QTY),
            if request.quantity <= policy.max_order_qty {
                String::new()
            } else {
                "order quantity exceeds max".to_string()
            },
        );

        let notional = request.quantity * request.price;
        if policy.max_notional.is_some() {
            push(
                &mut checks,
                "notional",
                bit(BIT_NOTIONAL),
                if notional <= policy.max_notional.unwrap_or(0.0) {
                    String::new()
                } else {
                    "notional exceeds max".to_string()
                },
            );
        } else {
            push(&mut checks, "notional", true, String::new());
        }
        let direction = if request.side == "BUY" { 1.0 } else { -1.0 };
        let new_position = request.position_qty + direction * request.quantity;
        push(
            &mut checks,
            "position",
            bit(BIT_POSITION),
            if new_position.abs() <= policy.max_position_qty {
                String::new()
            } else {
                "position limit exceeded".to_string()
            },
        );
        if policy.max_exposure_pct.is_some() && request.equity > 0.0 {
            push(
                &mut checks,
                "exposure",
                bit(BIT_EXPOSURE),
                if new_position.abs() * request.price / request.equity * 100.0
                    <= policy.max_exposure_pct.unwrap_or(0.0)
                {
                    String::new()
                } else {
                    "exposure limit exceeded".to_string()
                },
            );
        } else {
            push(&mut checks, "exposure", true, String::new());
        }
        if let Some(limit) = policy.daily_loss_limit {
            push(
                &mut checks,
                "daily_loss",
                bit(BIT_DAILY_LOSS),
                if request.day_pnl >= -limit.abs() {
                    String::new()
                } else {
                    "daily loss limit breached".to_string()
                },
            );
        } else {
            push(&mut checks, "daily_loss", true, String::new());
        }
        if let Some(limit) = policy.strategy_loss_limit {
            push(
                &mut checks,
                "strategy_loss",
                bit(BIT_STRATEGY_LOSS),
                if request.strategy_day_pnl >= -limit.abs() {
                    String::new()
                } else {
                    "strategy loss limit breached".to_string()
                },
            );
        } else {
            push(&mut checks, "strategy_loss", true, String::new());
        }
        push(
            &mut checks,
            "capital",
            bit(BIT_CAPITAL),
            if bit(BIT_CAPITAL) {
                String::new()
            } else {
                "no available capital".to_string()
            },
        );

        let reasons: Vec<String> = checks
            .iter()
            .filter(|c| !c.passed && !c.detail.is_empty())
            .map(|c| c.detail.clone())
            .collect();
        if ok {
            self.seen.insert(request.intent_id.clone());
        }
        RiskDecision {
            approved: ok,
            intent_id: request.intent_id.clone(),
            reasons,
            checks,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kill_switch::{KillSwitch, KillSwitchLevel};

    const NOW: f64 = 1767700000.0;

    fn base_request() -> RiskRequest {
        RiskRequest {
            intent_id: "i".to_string(),
            strategy_id: "s".to_string(),
            symbol: "RELIANCE".to_string(),
            side: "BUY".to_string(),
            quantity: 10.0,
            price: 100.0,
            timestamp: "2026-01-06T09:30:00+00:00".to_string(),
            position_qty: 0.0,
            day_pnl: 0.0,
            strategy_day_pnl: 0.0,
            equity: 1_000_000.0,
            available_capital: 500_000.0,
            spread_pct: Some(0.02),
            data_age_seconds: Some(5.0),
            broker_healthy: true,
            orders_today: 0,
            last_order_epoch: None,
            now_epoch: NOW,
        }
    }

    fn decide(policy: RiskPolicy, mutate: impl FnOnce(&mut RiskRequest)) -> RiskDecision {
        let mut req = base_request();
        mutate(&mut req);
        RiskEngine::new(policy).evaluate(&req, false)
    }

    fn check_names(d: &RiskDecision) -> Vec<(&str, bool)> {
        d.checks.iter().map(|c| (c.name, c.passed)).collect()
    }

    // ── parity vs live probe (risk_probe.py, 2026-09-17) ────────────────

    #[test]
    fn clean_request_approved_with_18_checks() {
        let d = decide(RiskPolicy::default(), |_| {});
        assert!(d.approved);
        assert!(d.reasons.is_empty());
        assert_eq!(d.checks.len(), 18);
        assert!(d.checks.iter().all(|c| c.passed));
        assert_eq!(
            check_names(&d).iter().map(|(n, _)| *n).collect::<Vec<_>>(),
            vec![
                "kill_switch",
                "broker_health",
                "session",
                "clock",
                "instrument",
                "duplicate",
                "fresh_data",
                "spread",
                "cooldown",
                "order_rate",
                "sanity",
                "order_qty",
                "notional",
                "position",
                "exposure",
                "daily_loss",
                "strategy_loss",
                "capital",
            ]
        );
        assert_eq!(d.checks[4].detail, "universe unrestricted");
        assert_eq!(d.checks[7].detail, "spread gate disabled");
    }

    #[test]
    fn gate_denials_match_probe() {
        let cases: Vec<(RiskPolicy, Box<dyn Fn(&mut RiskRequest)>, bool, Vec<&str>)> = vec![
            (
                RiskPolicy::default(),
                Box::new(|r| r.broker_healthy = false),
                false,
                vec!["broker unhealthy"],
            ),
            (
                RiskPolicy::default(),
                Box::new(|r| r.data_age_seconds = Some(3600.0)),
                false,
                vec!["stale market data: age=3600.0"],
            ),
            (
                RiskPolicy::default(),
                Box::new(|r| r.data_age_seconds = None),
                false,
                vec!["stale market data: age=None"],
            ),
            (
                RiskPolicy {
                    spread_limit_pct: Some(0.05),
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.spread_pct = Some(0.5)),
                false,
                vec!["spread too wide: 0.5"],
            ),
            (
                RiskPolicy {
                    max_orders_per_day: Some(2),
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.orders_today = 2),
                false,
                vec!["max orders per day reached"],
            ),
            (
                RiskPolicy {
                    max_orders_per_day: Some(2),
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.orders_today = 1),
                true,
                vec![],
            ),
            (
                RiskPolicy {
                    cooldown_seconds: 60.0,
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.last_order_epoch = Some(NOW - 10.0)),
                false,
                vec!["in cooldown"],
            ),
            (
                RiskPolicy {
                    cooldown_seconds: 60.0,
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.last_order_epoch = Some(NOW - 60.0)),
                true,
                vec![],
            ),
            (
                RiskPolicy::default(),
                Box::new(|r| r.quantity = 0.0),
                false,
                vec!["non-positive quantity or price"],
            ),
            (
                RiskPolicy::default(),
                Box::new(|r| r.quantity = -5.0),
                false,
                vec!["non-positive quantity or price"],
            ),
            // NaN denies via qty+position, NOT sanity/engine-error (IEEE, like Python).
            (
                RiskPolicy::default(),
                Box::new(|r| r.quantity = f64::NAN),
                false,
                vec!["order quantity exceeds max", "position limit exceeded"],
            ),
            (
                RiskPolicy {
                    max_order_qty: 50.0,
                    max_notional: Some(10_000.0),
                    max_position_qty: 100.0,
                    ..RiskPolicy::default()
                },
                Box::new(|r| {
                    r.quantity = 50.0;
                    r.price = 1000.0;
                }),
                false,
                vec!["notional exceeds max"],
            ),
            (
                RiskPolicy {
                    max_order_qty: 50.0,
                    max_notional: Some(10_000.0),
                    max_position_qty: 100.0,
                    ..RiskPolicy::default()
                },
                Box::new(|r| {
                    r.quantity = 10.0;
                    r.position_qty = 95.0;
                }),
                false,
                vec!["position limit exceeded"],
            ),
            (
                RiskPolicy {
                    max_exposure_pct: Some(10.0),
                    ..RiskPolicy::default()
                },
                Box::new(|_| {}),
                true,
                vec![],
            ),
            (
                RiskPolicy {
                    max_exposure_pct: Some(0.0001),
                    ..RiskPolicy::default()
                },
                Box::new(|_| {}),
                false,
                vec!["exposure limit exceeded"],
            ),
            (
                RiskPolicy {
                    max_exposure_pct: Some(0.1),
                    ..RiskPolicy::default()
                },
                Box::new(|_| {}),
                true,
                vec![],
            ),
            (
                RiskPolicy {
                    daily_loss_limit: Some(1000.0),
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.day_pnl = -1000.0),
                true,
                vec![],
            ),
            (
                RiskPolicy {
                    daily_loss_limit: Some(1000.0),
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.day_pnl = -1000.01),
                false,
                vec!["daily loss limit breached"],
            ),
            (
                RiskPolicy {
                    strategy_loss_limit: Some(500.0),
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.strategy_day_pnl = -500.0),
                true,
                vec![],
            ),
            (
                RiskPolicy {
                    strategy_loss_limit: Some(500.0),
                    ..RiskPolicy::default()
                },
                Box::new(|r| r.strategy_day_pnl = -600.0),
                false,
                vec!["strategy loss limit breached"],
            ),
            (
                RiskPolicy::default(),
                Box::new(|r| r.available_capital = 0.0),
                false,
                vec!["no available capital"],
            ),
            (
                RiskPolicy::default(),
                Box::new(|r| {
                    r.available_capital = 0.0;
                    r.side = "SELL".to_string();
                }),
                true,
                vec![],
            ),
            (
                RiskPolicy {
                    allowed_symbols: vec!["TCS".to_string()],
                    ..RiskPolicy::default()
                },
                Box::new(|_| {}),
                false,
                vec!["symbol not allowed: RELIANCE"],
            ),
        ];
        for (policy, mutate, approved, reasons) in cases {
            let d = decide(policy, mutate);
            assert_eq!(d.approved, approved, "reasons: {:?}", d.reasons);
            assert_eq!(
                d.reasons,
                reasons.iter().map(|s| s.to_string()).collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn duplicate_and_kill_switch() {
        let mut engine = RiskEngine::new(RiskPolicy::default());
        let mut req = base_request();
        req.intent_id = "dup".to_string();
        assert!(engine.evaluate(&req, false).approved);
        let second = engine.evaluate(&req, false);
        assert!(!second.approved);
        assert_eq!(
            second.reasons,
            vec!["intent already decided: dup".to_string()]
        );
        assert!(
            !second
                .checks
                .iter()
                .find(|c| c.name == "duplicate")
                .unwrap()
                .passed
        );

        // Reuse of the persisted kill-switch: engaged global denies everything.
        let mut ks = KillSwitch::in_memory();
        ks.engage("halt", KillSwitchLevel::Global);
        let denied = RiskEngine::new(RiskPolicy::default())
            .evaluate(&base_request(), ks.is_halted(KillSwitchLevel::Global));
        assert!(!denied.approved);
        assert_eq!(denied.reasons, vec!["kill switch engaged".to_string()]);
    }

    #[test]
    fn session_edges_match_python() {
        assert!(within_session(
            "2026-01-06T10:00:00+00:00",
            Some("09:15"),
            Some("15:30")
        ));
        assert!(within_session(
            "2026-01-06T09:15:00+00:00",
            Some("09:15"),
            Some("15:30")
        ));
        assert!(within_session(
            "2026-01-06T15:30:00+00:00",
            Some("09:15"),
            Some("15:30")
        ));
        assert!(!within_session(
            "2026-01-06T18:00:00+00:00",
            Some("09:15"),
            Some("15:30")
        ));
        assert!(within_session("2026-01-06T10:00:00+00:00", None, None));
        assert!(!within_session("x", Some("09:15"), Some("15:30")));
        assert!(!within_session("", Some("09:15"), Some("15:30")));
        // Garbage with only an end bound passes in Python too ("" sorts below
        // every bound) — preserved, not "fixed".
        assert!(within_session("x", None, Some("15:30")));

        // Wired through the engine: outside session denies with the reason
        // (04:00 is past NOW, so only the session gate fires).
        let d = decide(
            RiskPolicy {
                session_start: Some("09:15".to_string()),
                session_end: Some("15:30".to_string()),
                ..RiskPolicy::default()
            },
            |r| r.timestamp = "2026-01-06T04:00:00+00:00".to_string(),
        );
        assert!(!d.approved);
        assert_eq!(d.reasons, vec!["outside trading session".to_string()]);
    }

    #[test]
    fn clock_edges_match_python() {
        assert!(clock_sane("2026-01-06T09:30:00+00:00", NOW, 300.0));
        assert!(!clock_sane("2999-01-01T00:00:00+00:00", NOW, 300.0));
        assert!(!clock_sane("not-a-time", NOW, 300.0));
        assert!(clock_sane("2026-01-05 09:15:00", NOW, 300.0));

        let d = decide(RiskPolicy::default(), |r| {
            r.timestamp = "not-a-time".to_string()
        });
        assert!(!d.approved);
        assert_eq!(d.reasons, vec!["clock anomaly".to_string()]);
    }

    #[test]
    fn float_formatting_matches_repr() {
        assert_eq!(py_float(5.0), "5.0");
        assert_eq!(py_float(0.02), "0.02");
        assert_eq!(py_float(3600.0), "3600.0");
        assert_eq!(py_float(1e16), "1e+16");
        assert_eq!(py_float(1.5e-7), "1.5e-07");
        assert_eq!(py_float(f64::NAN), "nan");
        assert_eq!(py_float(f64::INFINITY), "inf");
        assert_eq!(py_opt(None), "None");
    }
}
