//! Operator-readable one-line summaries of gateway snapshots.
//!
//! Pure projection over the remote `snapshot` sections (never the trading
//! facts themselves): connection, strategy, risk, reconciliation, and
//! capital lines an operator can read at a glance. Missing facts render as
//! `NOT REPORTED` — nothing is ever invented to fill a line.

use serde_json::Value;

fn text_at(value: &Value, section: &str, key: &str) -> String {
    value
        .get(section)
        .and_then(|s| s.get(key))
        .and_then(Value::as_str)
        .unwrap_or("NOT REPORTED")
        .to_string()
}

/// Summarize a `snapshot`/`state_update` section map for status surfaces.
pub fn summarize_snapshot(sections: &Value) -> Vec<String> {
    let broker = text_at(sections, "broker", "status");
    let broker_name = text_at(sections, "broker", "name");
    let strategy = text_at(sections, "strategy", "status");
    let mode = text_at(sections, "strategy", "mode");
    let risk = text_at(sections, "risk", "status");
    let recon = text_at(sections, "reconciliation", "status");
    let capital_source = text_at(sections, "capital", "capital_source");
    let blockers = sections
        .get("blockers")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join("; ")
        })
        .unwrap_or_default();
    let mut lines = vec![
        format!("broker: {broker_name} [{broker}]"),
        format!("strategy: {strategy} (mode {mode})"),
        format!("risk: {risk} (capital: {capital_source})"),
        format!("reconciliation: {recon}"),
    ];
    lines.push(if blockers.is_empty() {
        "blockers: none reported".to_string()
    } else {
        format!("blockers: {blockers}")
    });
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn summary_reads_sections_and_names_gaps() {
        let sections = json!({
            "broker": {"status": "CONNECTED", "name": "FYERS"},
            "strategy": {"status": "BLOCKED", "mode": "PAPER"},
            "blockers": ["no strategy selected"],
        });
        let lines = summarize_snapshot(&sections);
        assert!(lines
            .iter()
            .any(|l| l.contains("FYERS") && l.contains("CONNECTED")));
        assert!(lines.iter().any(|l| l.contains("BLOCKED")));
        assert!(lines.iter().any(|l| l.contains("NOT REPORTED")));
        assert!(lines.iter().any(|l| l.contains("no strategy selected")));
    }

    #[test]
    fn empty_snapshot_stays_honest() {
        let lines = summarize_snapshot(&Value::Null);
        assert!(lines
            .iter()
            .all(|l| l.contains("NOT REPORTED") || l.contains("none reported")));
    }
}
