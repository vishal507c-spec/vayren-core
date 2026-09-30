//! LIVE readiness policy — the Rust-owned authority for going live.
//!
//! One responsibility: decide whether the system may act against a real
//! venue, and say why not in the words the operator sees.
//!
//! | Python (`08_execution/execution/broker/`) | Rust (here) |
//!|---|---|
//! | `credentials.py::validate_credentials` | `credential_reasons` |
//! | `gates.py::confirm_account` | `account_reasons` |
//! | `gates.py::risk_configuration_valid` + `RISK_CONFIG_MESSAGES` | `risk_reasons` |
//! | `gates.py::funds_valid_for_live` | `funds_reasons` |
//! | `gates.py::evaluate_live_gates` | `evaluate_gates` |
//! | `activation.py::evaluate_activation` | `evaluate_ceremony` |
//!
//! Python keeps only what a kernel cannot do: query an adapter, resolve a
//! secret name through a store, read the kill switch, and materialise the
//! frozen dataclasses the answers arrive in. Every verdict, reason string,
//! gate order and blocker composition is decided here.
//!
//! Fail-closed throughout: a missing fact is a failure, never a default pass.

use crate::execution_engine::{
    self, PRESENT_DAILY_LOSS_LIMIT, PRESENT_MAX_EXPOSURE_PCT, PRESENT_MAX_NOTIONAL,
    PRESENT_MAX_ORDERS_PER_DAY, PRESENT_REQUIRE_FRESH_DATA_SECONDS, PRESENT_STRATEGY_LOSS_LIMIT,
};
use crate::pytext::py_repr;
use crate::risk_engine::py_float;

/// Gate vocabulary (mission §4). Python re-exports the same five names.
pub const BROKER_ADAPTER_READY: &str = "BROKER_ADAPTER_READY";
pub const CREDENTIALS_READY: &str = "CREDENTIALS_READY";
pub const ACCOUNT_CONFIRMED: &str = "ACCOUNT_CONFIRMED";
pub const RISK_CONFIGURATION_VALID: &str = "RISK_CONFIGURATION_VALID";
pub const EXECUTION_SAFETY_ENABLED: &str = "EXECUTION_SAFETY_ENABLED";

/// How a list of reasons reads inside one gate's detail.
const REASON_JOIN: &str = "; ";

/// Kernel verdict bit i ↔ message i, in `risk_reasons` check order. The mask
/// itself is `execution_engine::risk_configuration_mask`; naming the failures
/// belongs with the gates that publish them.
const RISK_CONFIG_MESSAGES: &[&str] = &[
    "max_position_qty must be positive",
    "max_order_qty must be positive",
    "max_order_qty exceeds max_position_qty",
    "max_notional must be positive when set",
    "max_exposure_pct must be positive when set",
    "daily_loss_limit must be positive when set",
    "strategy_loss_limit must be positive when set",
    "cooldown_seconds must not be negative",
    "max_orders_per_day must be positive when set",
    "require_fresh_data_seconds must be positive when set",
];

fn join(reasons: &[&str]) -> String {
    reasons.join(REASON_JOIN)
}

/// Detail for a failed check: its reasons, or the caller's own fallback
/// wording when there are none (Python's `join(...) or "fallback"`).
fn failed_detail(reasons: &[&str], fallback: &str) -> String {
    let text = join(reasons);
    if text.is_empty() {
        fallback.to_string()
    } else {
        text
    }
}

/// Identity (+ secrets when required) validation. Reasons name missing
/// fields, never values; `resolvable[i]` is the store's own answer for
/// `key_refs[i]` (a short list fails closed).
pub fn credential_reasons(
    account_id: &str,
    environment: &str,
    expected_environment: &str,
    key_refs: &[&str],
    resolvable: &[bool],
    require_secrets: bool,
    store_present: bool,
) -> Vec<String> {
    let mut reasons: Vec<String> = Vec::new();
    if account_id.is_empty() {
        reasons.push("missing account_id".to_string());
    }
    if environment.is_empty() {
        reasons.push("missing environment".to_string());
    } else if !expected_environment.is_empty() && environment != expected_environment {
        reasons.push(format!(
            "environment mismatch: credentials say {}, expected {}",
            py_repr(environment),
            py_repr(expected_environment)
        ));
    }
    if require_secrets {
        if !store_present {
            reasons.push("no credential store configured".to_string());
        } else {
            for (index, reference) in key_refs.iter().enumerate() {
                if !resolvable.get(index).copied().unwrap_or(false) {
                    reasons.push(format!("secret not resolvable: {reference}"));
                }
            }
        }
        if key_refs.is_empty() {
            reasons.push("no secret key_refs declared".to_string());
        }
    }
    reasons
}

/// Broker/account identity as the adapter reported it. A sandbox account
/// never confirms as LIVE, even with a matching id — environment is identity.
pub fn account_reasons(
    account_id: &str,
    environment: &str,
    expected_account_id: &str,
    expected_environment: &str,
) -> Vec<String> {
    if account_id.is_empty() {
        return vec!["adapter reports no account_id".to_string()];
    }
    let mut reasons: Vec<String> = Vec::new();
    if !expected_account_id.is_empty() && account_id != expected_account_id {
        reasons.push(format!(
            "account mismatch: adapter={} expected={}",
            py_repr(account_id),
            py_repr(expected_account_id)
        ));
    }
    if !expected_environment.is_empty() {
        if environment != expected_environment {
            reasons.push(format!(
                "environment mismatch: adapter={} expected={}",
                py_repr(environment),
                py_repr(expected_environment)
            ));
        }
        if expected_environment == "live" && environment != "live" {
            reasons.push("sandbox account must never be treated as LIVE".to_string());
        }
    }
    reasons
}

/// Active risk policy sanity check (no market data needed): the messages for
/// each bit of ``execution_engine::risk_configuration_mask``.
#[allow(clippy::too_many_arguments)]
pub fn risk_reasons(
    present: u32,
    max_position_qty: f64,
    max_order_qty: f64,
    max_notional: f64,
    max_exposure_pct: f64,
    daily_loss_limit: f64,
    strategy_loss_limit: f64,
    cooldown_seconds: f64,
    max_orders_per_day: i64,
    require_fresh_data_seconds: f64,
) -> Vec<String> {
    let mask = execution_engine::risk_configuration_mask(
        present,
        max_position_qty,
        max_order_qty,
        max_notional,
        max_exposure_pct,
        daily_loss_limit,
        strategy_loss_limit,
        cooldown_seconds,
        max_orders_per_day,
        require_fresh_data_seconds,
    );
    RISK_CONFIG_MESSAGES
        .iter()
        .enumerate()
        .filter(|(bit, _)| mask & (1 << bit) != 0)
        .map(|(_, message)| (*message).to_string())
        .collect()
}

/// Funds preconditions for LIVE (FINAL §H/§S): non-positive availability or
/// equity, or an absent currency, denies. Zero can never authorize.
pub fn funds_reasons(available: f64, equity: f64, currency: &str) -> Vec<String> {
    let mut reasons = Vec::new();
    if available <= 0.0 {
        reasons.push(format!(
            "available capital not positive: {}",
            py_float(available)
        ));
    }
    if equity <= 0.0 {
        reasons.push(format!("equity not positive: {}", py_float(equity)));
    }
    if currency.trim().is_empty() {
        reasons.push("funds currency missing".to_string());
    }
    reasons
}

/// One live gate's verdict.
pub struct GateResult {
    pub name: &'static str,
    pub passed: bool,
    pub detail: String,
}

/// The five live gates, in order, fail-closed. Each fact arrives already
/// gathered: the adapter's health, and the reasons from the credential,
/// account and risk-policy checks above (an unevaluated check is itself a
/// failure — the caller cannot opt out of one).
#[allow(clippy::too_many_arguments)]
pub fn evaluate_gates(
    adapter_present: bool,
    adapter_error: &str,
    health_ok: bool,
    health_detail: &str,
    credentials_ok: bool,
    credential_reasons: &[&str],
    account_ok: bool,
    account_reasons: &[&str],
    account_evaluated: bool,
    risk_present: bool,
    risk_ok: bool,
    risk_reasons: &[&str],
    kill_halted: bool,
) -> (bool, Vec<GateResult>) {
    let gates = vec![
        GateResult {
            name: BROKER_ADAPTER_READY,
            passed: adapter_present && health_ok,
            detail: if adapter_present {
                if health_ok {
                    String::new()
                } else {
                    health_detail.to_string()
                }
            } else {
                fallback(adapter_error, "no adapter")
            },
        },
        GateResult {
            name: CREDENTIALS_READY,
            passed: credentials_ok,
            detail: if credentials_ok {
                String::new()
            } else {
                failed_detail(credential_reasons, "")
            },
        },
        GateResult {
            name: ACCOUNT_CONFIRMED,
            passed: account_evaluated && account_ok,
            detail: if !account_evaluated {
                "no adapter to confirm against".to_string()
            } else if account_ok {
                String::new()
            } else {
                failed_detail(account_reasons, "")
            },
        },
        GateResult {
            name: RISK_CONFIGURATION_VALID,
            passed: risk_present && risk_ok,
            detail: if !risk_present {
                "no risk policy active".to_string()
            } else if risk_ok {
                String::new()
            } else {
                failed_detail(risk_reasons, "")
            },
        },
        GateResult {
            name: EXECUTION_SAFETY_ENABLED,
            passed: !kill_halted,
            detail: if kill_halted {
                "kill switch engaged".to_string()
            } else {
                String::new()
            },
        },
    ];
    let ready = gates.iter().all(|gate| gate.passed);
    (ready, gates)
}

fn fallback(text: &str, when_absent: &str) -> String {
    if text.is_empty() {
        when_absent.to_string()
    } else {
        text.to_string()
    }
}

/// One ordered ceremony step.
pub struct Step {
    pub index: i64,
    pub name: &'static str,
    pub ok: bool,
    pub detail: String,
}

/// The LIVE activation ceremony (FINAL §T): twelve explicit steps, eleven of
/// them verifiable. Step 12 is never auto-ok — a green ceremony still leaves
/// going live to an explicit operator act.
#[allow(clippy::too_many_arguments)]
pub fn evaluate_ceremony(
    broker_name: &str,
    environment: &str,
    credentials_ok: bool,
    credential_reasons: &[&str],
    account_ok: bool,
    account_reasons: &[&str],
    market_healthy: bool,
    market_reason: &str,
    funds_ok: bool,
    funds_reasons: &[&str],
    risk_ok: bool,
    risk_reasons: &[&str],
    verdict_present: bool,
    verdict_blocks: bool,
    verdict_reasons: &[&str],
    kill_halted: bool,
    gates_present: bool,
    gates_ready: bool,
    failed_gate_names: &[&str],
    failed_gate_details: &[&str],
    armed_value: &str,
) -> (bool, Vec<Step>, Vec<String>) {
    let broker_selected = !broker_name.is_empty();
    let live_environment = environment == "live";
    let reconciled = verdict_present && !verdict_blocks;
    let gates_passed = gates_present && gates_ready;
    let armed = armed_value == "ARMED";
    let gate_lines: Vec<String> = failed_gate_names
        .iter()
        .zip(failed_gate_details)
        .map(|(name, detail)| format!("{name}: {detail}"))
        .collect();
    let gate_text = gate_lines.join(REASON_JOIN);
    let steps: Vec<Step> = vec![
        Step {
            index: 1,
            name: "SELECT_BROKER",
            ok: broker_selected,
            detail: step_detail(broker_selected, "", "no broker selected"),
        },
        Step {
            index: 2,
            name: "LIVE_ENVIRONMENT",
            ok: live_environment,
            detail: step_detail(
                live_environment,
                "",
                &format!("environment is {}, not 'live'", py_repr(environment)),
            ),
        },
        Step {
            index: 3,
            name: "VALIDATE_CREDENTIALS",
            ok: credentials_ok,
            detail: step_detail(
                credentials_ok,
                "",
                &failed_detail(credential_reasons, "credentials invalid"),
            ),
        },
        Step {
            index: 4,
            name: "CONFIRM_ACCOUNT",
            ok: account_ok,
            detail: step_detail(
                account_ok,
                "",
                &failed_detail(account_reasons, "account unconfirmed"),
            ),
        },
        Step {
            index: 5,
            name: "VERIFY_MARKET_DATA",
            ok: market_healthy,
            detail: step_detail(
                market_healthy,
                "",
                &fallback(market_reason, "market data unhealthy"),
            ),
        },
        Step {
            index: 6,
            name: "VERIFY_FUNDS",
            ok: funds_ok,
            detail: step_detail(funds_ok, "", &failed_detail(funds_reasons, "funds invalid")),
        },
        Step {
            index: 7,
            name: "VALIDATE_RISK",
            ok: risk_ok,
            detail: step_detail(risk_ok, "", &failed_detail(risk_reasons, "risk invalid")),
        },
        Step {
            index: 8,
            name: "RECONCILE",
            ok: reconciled,
            detail: if !verdict_present {
                "reconciliation not evaluated".to_string()
            } else {
                step_detail(
                    reconciled,
                    "",
                    &failed_detail(verdict_reasons, "reconciliation blocks live"),
                )
            },
        },
        Step {
            index: 9,
            name: "EVALUATE_GATES",
            ok: gates_passed,
            detail: if !gates_present {
                "gates not evaluated".to_string()
            } else {
                step_detail(
                    gates_passed,
                    "",
                    if gate_text.is_empty() {
                        "gates failing"
                    } else {
                        &gate_text
                    },
                )
            },
        },
        Step {
            index: 10,
            name: "VERIFY_KILL_SWITCH",
            ok: !kill_halted,
            detail: step_detail(!kill_halted, "", "kill switch engaged"),
        },
        Step {
            index: 11,
            name: "EXPLICIT_ARM",
            ok: armed,
            detail: step_detail(armed, "", &format!("arming is {armed_value}, not ARMED")),
        },
    ];
    let verifiable_ready = steps.iter().all(|step| step.ok);
    let blockers = steps
        .iter()
        .filter(|step| !step.ok && !step.detail.is_empty())
        .map(|step| format!("{}: {}", step.name, step.detail))
        .collect();
    let mut steps = steps;
    steps.push(Step {
        index: 12,
        name: "START_LIVE",
        ok: false,
        detail: if verifiable_ready {
            "ceremony green — start remains an explicit operator act".to_string()
        } else {
            "blocked: ceremony not fully green".to_string()
        },
    });
    (verifiable_ready, steps, blockers)
}

fn step_detail(ok: bool, when_ok: &str, when_failed: &str) -> String {
    if ok {
        when_ok.to_string()
    } else {
        when_failed.to_string()
    }
}

/// Presence bit for one optional policy limit (a clear bit = Python `None`).
pub const fn presence(defined: bool, bit: u32) -> u32 {
    if defined {
        bit
    } else {
        0
    }
}

/// Assemble the presence bitmask from the six optional limits.
#[allow(clippy::too_many_arguments)]
pub fn presence_mask(
    max_notional: bool,
    max_exposure_pct: bool,
    daily_loss_limit: bool,
    strategy_loss_limit: bool,
    max_orders_per_day: bool,
    require_fresh_data_seconds: bool,
) -> u32 {
    presence(max_notional, PRESENT_MAX_NOTIONAL)
        | presence(max_exposure_pct, PRESENT_MAX_EXPOSURE_PCT)
        | presence(daily_loss_limit, PRESENT_DAILY_LOSS_LIMIT)
        | presence(strategy_loss_limit, PRESENT_STRATEGY_LOSS_LIMIT)
        | presence(max_orders_per_day, PRESENT_MAX_ORDERS_PER_DAY)
        | presence(
            require_fresh_data_seconds,
            PRESENT_REQUIRE_FRESH_DATA_SECONDS,
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn refs(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    #[test]
    fn credentials_report_missing_identity_before_secrets() {
        assert_eq!(
            credential_reasons("", "", "", &[], &[], false, false),
            refs(&["missing account_id", "missing environment"])
        );
        assert!(credential_reasons("a", "sandbox", "", &[], &[], false, false).is_empty());
        assert_eq!(
            credential_reasons("a", "sandbox", "live", &[], &[], false, false),
            refs(&["environment mismatch: credentials say 'sandbox', expected 'live'"])
        );
        // An empty expected environment never contradicts what is there.
        assert!(credential_reasons("a", "sandbox", "", &[], &[], false, false).is_empty());
    }

    #[test]
    fn secrets_are_resolved_by_the_caller_and_denied_here() {
        assert_eq!(
            credential_reasons("a", "live", "live", &[], &[], true, false),
            refs(&[
                "no credential store configured",
                "no secret key_refs declared"
            ])
        );
        assert_eq!(
            credential_reasons(
                "a",
                "live",
                "",
                &["API_KEY", "API_SECRET"],
                &[true, false],
                true,
                true
            ),
            refs(&["secret not resolvable: API_SECRET"])
        );
        assert!(credential_reasons("a", "live", "", &["K"], &[true], true, true).is_empty());
        // A short resolvability list fails closed rather than passing by accident.
        assert_eq!(
            credential_reasons("a", "live", "", &["K"], &[], true, true),
            refs(&["secret not resolvable: K"])
        );
    }

    #[test]
    fn a_sandbox_account_can_never_confirm_as_live() {
        assert_eq!(
            account_reasons("sbx-1", "sandbox", "live", ""),
            refs(&["account mismatch: adapter='sbx-1' expected='live'"])
        );
        assert_eq!(
            account_reasons("sbx-1", "sandbox", "sbx-1", "live"),
            refs(&[
                "environment mismatch: adapter='sandbox' expected='live'",
                "sandbox account must never be treated as LIVE"
            ])
        );
        assert!(account_reasons("sbx-1", "sandbox", "sbx-1", "sandbox").is_empty());
        assert!(account_reasons("a", "", "", "").is_empty());
        assert_eq!(
            account_reasons("", "", "", ""),
            refs(&["adapter reports no account_id"])
        );
    }

    #[test]
    fn risk_reasons_name_every_set_bit_in_check_order() {
        // Defaults: max_position_qty/max_order_qty positive, cooldown sane.
        let clean = risk_reasons(
            presence_mask(false, false, false, false, false, false),
            1.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0,
            0.0,
        );
        assert!(clean.is_empty(), "{clean:?}");
        assert_eq!(
            risk_reasons(0, 0.0, 9_999.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0, 0.0),
            refs(&[
                "max_position_qty must be positive",
                "max_order_qty exceeds max_position_qty",
                "cooldown_seconds must not be negative",
            ])
        );
        assert_eq!(
            risk_reasons(
                presence_mask(true, false, false, false, false, false),
                10.0,
                5.0,
                -5.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0,
                0.0
            ),
            refs(&["max_notional must be positive when set"])
        );
    }

    #[test]
    fn funds_denies_zero_capital_and_an_absent_currency() {
        assert!(funds_reasons(100.0, 100.0, "INR").is_empty());
        assert_eq!(
            funds_reasons(0.0, -5.0, "  "),
            refs(&[
                "available capital not positive: 0.0",
                "equity not positive: -5.0",
                "funds currency missing"
            ])
        );
    }

    #[test]
    fn the_five_gates_fail_closed_in_order() {
        let (ready, gates) = evaluate_gates(
            false,
            "",
            false,
            "",
            false,
            &["missing account_id"],
            false,
            &[],
            false,
            false,
            false,
            &[],
            true,
        );
        assert!(!ready);
        let names: Vec<&str> = gates.iter().map(|gate| gate.name).collect();
        assert_eq!(
            names,
            vec![
                BROKER_ADAPTER_READY,
                CREDENTIALS_READY,
                ACCOUNT_CONFIRMED,
                RISK_CONFIGURATION_VALID,
                EXECUTION_SAFETY_ENABLED,
            ]
        );
        let details: Vec<&str> = gates.iter().map(|gate| gate.detail.as_str()).collect();
        assert_eq!(
            details,
            vec![
                "no adapter",
                "missing account_id",
                "no adapter to confirm against",
                "no risk policy active",
                "kill switch engaged"
            ]
        );
    }

    #[test]
    fn every_gate_passes_only_on_real_evidence() {
        let (ready, gates) = evaluate_gates(
            true,
            "",
            true,
            "sandbox ready",
            true,
            &[],
            true,
            &[],
            true,
            true,
            true,
            &[],
            false,
        );
        assert!(ready);
        assert!(gates.iter().all(|gate| gate.detail.is_empty()));
        // An adapter present but unhealthy carries the adapter's own reason.
        let (ready, gates) = evaluate_gates(
            true,
            "ignored",
            false,
            "simulated transport drop",
            true,
            &[],
            true,
            &[],
            true,
            true,
            true,
            &[],
            false,
        );
        assert!(!ready);
        assert_eq!(gates[0].detail, "simulated transport drop");
        let (ready, gates) = evaluate_gates(
            false,
            "adapter boom",
            false,
            "",
            true,
            &[],
            true,
            &[],
            true,
            true,
            true,
            &[],
            false,
        );
        assert!(!ready);
        assert_eq!(gates[0].detail, "adapter boom");
    }

    #[test]
    fn a_green_ceremony_still_refuses_to_start_live() {
        let (ready, steps, blockers) = evaluate_ceremony(
            "ubl",
            "live",
            true,
            &[],
            true,
            &[],
            true,
            "",
            true,
            &[],
            true,
            &[],
            true,
            false,
            &[],
            false,
            true,
            true,
            &[],
            &[],
            "ARMED",
        );
        assert!(ready);
        assert_eq!(steps.len(), 12);
        assert!(blockers.is_empty());
        assert!(!steps[11].ok);
        assert_eq!(
            steps[11].detail,
            "ceremony green — start remains an explicit operator act"
        );
        assert_eq!(steps[11].index, 12);
    }

    #[test]
    fn a_disarmed_ceremony_blocks_live_with_named_reasons() {
        let (ready, steps, blockers) = evaluate_ceremony(
            "",
            "sandbox",
            false,
            &["missing account_id"],
            false,
            &["no adapter"],
            false,
            "",
            false,
            &[],
            false,
            &[],
            false,
            false,
            &[],
            true,
            false,
            false,
            &[],
            &[],
            "DISARMED",
        );
        assert!(!ready);
        assert_eq!(steps[0].detail, "no broker selected");
        assert_eq!(steps[1].detail, "environment is 'sandbox', not 'live'");
        assert_eq!(steps[2].detail, "missing account_id");
        assert_eq!(steps[4].detail, "market data unhealthy");
        assert_eq!(steps[7].detail, "reconciliation not evaluated");
        assert_eq!(steps[8].detail, "gates not evaluated");
        assert_eq!(steps[9].detail, "kill switch engaged");
        assert_eq!(steps[10].detail, "arming is DISARMED, not ARMED");
        assert_eq!(steps[11].detail, "blocked: ceremony not fully green");
        assert_eq!(blockers.len(), 11);
        assert_eq!(blockers[0], "SELECT_BROKER: no broker selected");
        assert_eq!(blockers[2], "VALIDATE_CREDENTIALS: missing account_id");
    }

    #[test]
    fn failed_gates_and_verdicts_read_as_one_detail_line() {
        let (ready, steps, _) = evaluate_ceremony(
            "ubl",
            "live",
            true,
            &[],
            true,
            &[],
            true,
            "",
            true,
            &[],
            true,
            &[],
            true,
            true,
            &["position mismatch", "cash mismatch"],
            true,
            true,
            false,
            &["ACCOUNT_CONFIRMED", "CREDENTIALS_READY"],
            &["adapter lag", ""],
            "ARMED",
        );
        assert!(!ready);
        assert_eq!(steps[7].detail, "position mismatch; cash mismatch");
        assert_eq!(
            steps[8].detail,
            "ACCOUNT_CONFIRMED: adapter lag; CREDENTIALS_READY: "
        );
    }
}
