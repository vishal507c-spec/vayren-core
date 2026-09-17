//! Portfolio native view-model — pure, headless-testable UI state
//! (constitution §3 / UI_DESIGN_SYSTEM.md: Rust owns view-model +
//! interaction state; Slint renders bound properties only).
//!
//! First-principles responsive presentation of the existing portfolio backend
//! facts (broker/account, funds, positions, orders/fills, performance,
//! risk). This module never computes financial truth: every number arrives in
//! [`PortfolioSnapshot`] from the backend bridge (the same workspace-state
//! shape the retained Qt `PortfolioWorkspace` consumes). Formatting here is
//! display-only and mirrors the Qt `ui_kit` conventions exactly:
//! signed 2dp money, `N/A` / `Unavailable` for missing data — never
//! zero-filled, never invented.
//!
//! Layout-relevant invariants (the screenshot bug classes this surface must
//! never regress):
//! - no fixed page geometry: the Slint screen owns composition from available
//!   logical width (`narrow` folds the KPI 4→2 grid and stacks secondary
//!   sections instead of squeezing them);
//! - state-driven sections: empty/missing data projects to compact truthful
//!   empty states (`has_*` flags), never to giant blank containers or
//!   clipped-overlap stacks;
//! - information hierarchy: account → value → performance → positions →
//!   risk/allocation → orders; secondary content never claims primary space.

use crate::lab::Tone;

/// Backend-fed funds facts. `None` = field absent in backend state.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct FundsFacts {
    pub equity: Option<f64>,
    pub available: Option<f64>,
    pub used: Option<f64>,
}

/// Backend-fed P&L facts. `None` = not reported by the backend.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PnlFacts {
    pub total: Option<f64>,
    pub unrealized: Option<f64>,
    pub realized: Option<f64>,
    pub today: Option<f64>,
    pub wins: Option<u64>,
    pub losses: Option<u64>,
}

/// Backend-fed broker identity facts.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct BrokerFacts {
    pub name: String,
    pub environment: String,
    pub connected: Option<bool>,
    pub status: String,
}

/// Backend-fed position facts (single `position` record or one `positions`
/// list entry — same shape the Qt workspace consumes).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PositionFacts {
    pub symbol: String,
    pub side: String,
    pub quantity: f64,
    pub avg_price: Option<f64>,
    pub current_price: Option<f64>,
    pub invested: Option<f64>,
    pub value: Option<f64>,
    pub exposure: Option<f64>,
    pub pnl: Option<f64>,
}

impl PositionFacts {
    fn exposure_value(&self) -> f64 {
        self.exposure.or(self.value).unwrap_or(0.0)
    }
}

/// Backend-fed order facts.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct OrderFacts {
    pub order_id: String,
    pub time: String,
    pub symbol: String,
    pub side: String,
    pub quantity: f64,
    pub status: String,
}

/// Backend-fed fill facts.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct FillFacts {
    pub time: String,
    pub symbol: String,
    pub side: String,
    pub quantity: f64,
    pub price: f64,
}

/// Immutable backend snapshot feeding one portfolio projection. Mirrors the
/// retained Qt workspace state-dict keys (`broker`, `funds`, `position` /
/// `positions`, `orders`, `fills`, `pnl`, `risk`, `reconciliation`, `kill`,
/// `lifecycle`, `mode`, `risk_metrics`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PortfolioSnapshot {
    pub broker: BrokerFacts,
    pub funds: Option<FundsFacts>,
    /// Single-position record (`None` or `position_flat` = flat book).
    pub position: Option<PositionFacts>,
    pub position_flat: bool,
    pub positions: Vec<PositionFacts>,
    pub orders: Vec<OrderFacts>,
    pub fills: Vec<FillFacts>,
    pub pnl: PnlFacts,
    pub risk_status: String,
    pub recon_status: String,
    pub kill_halted: bool,
    pub lifecycle: String,
    pub mode: String,
    pub risk_drawdown: Option<f64>,
    pub risk_volatility: Option<f64>,
}

/// The single source of portfolio presentation state: backend snapshot plus
/// the only two UI-owned interaction states (orders tab, selected position).
/// Slint reports actions; this state mutates centrally and re-projects.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PortfolioState {
    pub snapshot: PortfolioSnapshot,
    /// 0 = ORDERS, 1 = FILLS.
    pub orders_tab: usize,
    pub selected_position: Option<usize>,
    /// Last-refresh stamp (`Updated HH:MM:SS`), set by the bridge.
    pub updated_label: String,
}

impl PortfolioState {
    pub fn set_orders_tab(&mut self, tab: usize) {
        if tab < 2 {
            self.orders_tab = tab;
        }
    }

    pub fn select_position(&mut self, index: usize) -> bool {
        if index >= positions_of(&self.snapshot).len() {
            return false;
        }
        self.selected_position = Some(index);
        true
    }
}

// ── display formatting (mirrors Qt ui_kit / portfolio_workspace) ────────────

fn with_commas(int_part: &str, negative: bool) -> String {
    let digits: Vec<char> = int_part.chars().collect();
    let mut out = String::new();
    for (i, ch) in digits.iter().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(*ch);
    }
    if negative {
        format!("-{out}")
    } else {
        out
    }
}

fn comma_float(value: f64, decimals: usize) -> String {
    let negative = value < 0.0;
    let abs = value.abs();
    let rounded = format!("{abs:.decimals$}");
    let (int_part, frac_part) = match rounded.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (rounded.as_str(), None),
    };
    let grouped = with_commas(int_part, negative);
    match frac_part {
        Some(frac) => format!("{grouped}.{frac}"),
        None => grouped,
    }
}

/// Equity formatting: ≥ 1,00,000 → 0dp with separators, else 2dp.
pub fn format_equity(value: f64) -> String {
    if value.abs() >= 100_000.0 {
        comma_float(value, 0)
    } else {
        comma_float(value, 2)
    }
}

/// Signed 2dp money (`+1.00` / `-1.00`), mirroring `ui_kit.money`.
pub fn format_money(value: f64) -> String {
    format!("{value:+.2}")
}

/// Signed 2dp percent (`+1.00%`), mirroring the Qt `_fmt_pct`.
pub fn format_pct(value: f64) -> String {
    format!("{value:+.2}%")
}

/// Plain 2dp scalar with separators, mirroring `ui_kit.text` for floats.
pub fn format_scalar(value: f64) -> String {
    comma_float(value, 2)
}

fn tone_of(value: Option<f64>) -> Tone {
    match value {
        None => Tone::Muted,
        Some(v) if v > 0.0 => Tone::Positive,
        Some(v) if v < 0.0 => Tone::Negative,
        _ => Tone::Neutral,
    }
}

// ── backend-shape helpers (mirror the Qt workspace derivations) ─────────────

/// Mirrors `PortfolioWorkspace._is_configured`.
pub fn is_configured(snapshot: &PortfolioSnapshot) -> bool {
    let name = snapshot.broker.name.to_uppercase();
    if !["NOT CONFIGURED", "", "NONE"]
        .iter()
        .any(|blocked| *blocked == name.as_str())
    {
        if snapshot.broker.connected == Some(true)
            || ["CONNECTED", "LIVE_READY"]
                .iter()
                .any(|ok| *ok == snapshot.broker.status.as_str())
        {
            return true;
        }
    }
    snapshot.funds.is_some()
}

fn equity_of(snapshot: &PortfolioSnapshot) -> Option<f64> {
    let funds = snapshot.funds.as_ref()?;
    funds.equity.or(funds.available)
}

/// Mirrors `PortfolioWorkspace._positions_from_state`.
pub fn positions_of(snapshot: &PortfolioSnapshot) -> Vec<PositionFacts> {
    if let Some(single) = &snapshot.position {
        if !snapshot.position_flat {
            return vec![single.clone()];
        }
    }
    snapshot.positions.clone()
}

/// Defensive JSON accessors for the bridge format (owned by the Python
/// backend; see `SlintPortfolioHost.portfolio_snapshot_dict`). Missing or
/// mistyped fields degrade to `None`/default — never invented, never
/// zero-filled. JSON `true` is never a number; numeric strings are never
/// parsed (both degrade to absent, exactly like the legacy workspace which
/// only ever consumed native numbers and bools).
fn jstr(value: Option<&serde_json::Value>) -> String {
    value.and_then(|v| v.as_str()).unwrap_or("").to_string()
}

fn jnum(value: Option<&serde_json::Value>) -> Option<f64> {
    value.and_then(|v| v.as_f64())
}

fn jbool(value: Option<&serde_json::Value>) -> Option<bool> {
    value.and_then(|v| v.as_bool())
}

fn juint(value: Option<&serde_json::Value>) -> Option<u64> {
    value.and_then(|v| v.as_u64())
}

fn jobject(
    value: Option<&serde_json::Value>,
) -> Option<&serde_json::Map<String, serde_json::Value>> {
    value.and_then(|v| v.as_object())
}

fn jarray(value: Option<&serde_json::Value>) -> Vec<&serde_json::Value> {
    value
        .and_then(|v| v.as_array())
        .map(|a| a.iter().collect())
        .unwrap_or_default()
}

fn funds_of(value: Option<&serde_json::Value>) -> Option<FundsFacts> {
    // Mirrors `_is_configured`: an empty funds mapping counts as absent.
    let obj = jobject(value)?;
    if obj.is_empty() {
        return None;
    }
    Some(FundsFacts {
        equity: jnum(obj.get("equity")),
        available: jnum(obj.get("available")),
        used: jnum(obj.get("used")),
    })
}

fn position_of(value: Option<&serde_json::Value>) -> Option<PositionFacts> {
    // Mirrors `_positions_from_state`: any mapping (even sparse) is one row.
    let obj = jobject(value)?;
    let g = |key: &str| obj.get(key);
    Some(PositionFacts {
        symbol: jstr(g("symbol").or_else(|| g("instrument"))),
        side: jstr(g("side")),
        quantity: jnum(g("quantity")).unwrap_or(0.0),
        avg_price: jnum(g("avg_price")),
        current_price: jnum(g("current_price").or_else(|| g("ltp"))),
        invested: jnum(g("invested").or_else(|| g("exposure"))),
        value: jnum(g("value").or_else(|| g("exposure"))),
        exposure: jnum(g("exposure")),
        pnl: jnum(g("pnl").or_else(|| g("unrealized"))),
    })
}

fn status_of(block: Option<&serde_json::Value>) -> String {
    // Mirrors `str(risk.get("status", _NA))`: unusable block renders "N/A".
    match jobject(block).and_then(|o| o.get("status")) {
        Some(v) if v.is_string() => jstr(Some(v)),
        Some(_) => "N/A".to_string(),
        None => "N/A".to_string(),
    }
}

impl PortfolioSnapshot {
    /// Parse the backend bridge snapshot. Unknown keys are ignored
    /// (forward-compatible); every derivation downstream (`is_configured`,
    /// `positions_of`, `project`) behaves exactly as if the equivalent
    /// legacy state dict had been consumed.
    pub fn from_json(value: &serde_json::Value) -> Self {
        let obj = value.as_object();
        let g = |key: &str| obj.and_then(|o| o.get(key));
        let funds = funds_of(g("funds"));
        let position = position_of(g("position"));
        let positions: Vec<PositionFacts> = jarray(g("positions"))
            .into_iter()
            .filter_map(|v| position_of(Some(v)))
            .collect();
        let orders: Vec<OrderFacts> = jarray(g("orders"))
            .into_iter()
            .filter_map(|v| jobject(Some(v)))
            .map(|o| OrderFacts {
                order_id: jstr(o.get("order_id")),
                time: jstr(o.get("time").or_else(|| o.get("timestamp"))),
                symbol: jstr(o.get("symbol")),
                side: jstr(o.get("side")),
                quantity: jnum(o.get("quantity")).unwrap_or(0.0),
                status: jstr(o.get("status")),
            })
            .collect();
        let fills: Vec<FillFacts> = jarray(g("fills"))
            .into_iter()
            .filter_map(|v| jobject(Some(v)))
            .map(|o| FillFacts {
                time: jstr(o.get("time")),
                symbol: jstr(o.get("symbol")),
                side: jstr(o.get("side")),
                quantity: jnum(o.get("quantity")).unwrap_or(0.0),
                price: jnum(o.get("price")).unwrap_or(0.0),
            })
            .collect();
        let pnl = jobject(g("pnl"));
        let metrics = jobject(g("risk_metrics"));
        let kill_halted = jobject(g("kill"))
            .and_then(|o| o.get("halted"))
            .and_then(|v| v.as_bool());
        PortfolioSnapshot {
            broker: {
                let b = jobject(g("broker"));
                BrokerFacts {
                    name: b.map(|o| jstr(o.get("name"))).unwrap_or_default(),
                    environment: b.map(|o| jstr(o.get("environment"))).unwrap_or_default(),
                    connected: b.and_then(|o| jbool(o.get("connected"))),
                    status: b.map(|o| jstr(o.get("status"))).unwrap_or_default(),
                }
            },
            funds,
            position_flat: jobject(g("position"))
                .and_then(|o| jbool(o.get("flat")))
                .unwrap_or(false),
            position,
            positions,
            orders,
            fills,
            pnl: PnlFacts {
                total: pnl.and_then(|o| jnum(o.get("total"))),
                unrealized: pnl.and_then(|o| jnum(o.get("unrealized"))),
                realized: pnl.and_then(|o| jnum(o.get("realized"))),
                today: pnl.and_then(|o| jnum(o.get("today"))),
                wins: pnl.and_then(|o| juint(o.get("wins"))),
                losses: pnl.and_then(|o| juint(o.get("losses"))),
            },
            risk_status: status_of(g("risk")),
            recon_status: status_of(g("reconciliation")),
            kill_halted: kill_halted.unwrap_or(false),
            lifecycle: jstr(g("lifecycle")),
            mode: jstr(g("mode")),
            risk_drawdown: metrics.and_then(|o| jnum(o.get("drawdown"))),
            risk_volatility: metrics.and_then(|o| jnum(o.get("volatility"))),
        }
    }
}

/// Allocation entries `(symbol, pct)` sorted descending — display math
/// (`exposure / equity`), mirroring `_allocation_entries`.
pub fn allocation_of(snapshot: &PortfolioSnapshot) -> Vec<(String, f64)> {
    let Some(equity) = equity_of(snapshot) else {
        return Vec::new();
    };
    if equity == 0.0 {
        return Vec::new();
    }
    let mut entries: Vec<(String, f64)> = positions_of(snapshot)
        .iter()
        .filter(|p| !p.symbol.is_empty())
        .map(|p| (p.symbol.clone(), 100.0 * p.exposure_value() / equity))
        .collect();
    entries.sort_by(|a, b| b.1.total_cmp(&a.1));
    entries
}

// ── projection: PortfolioState -> flat render view ──────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub struct KpiView {
    pub label: String,
    pub value: String,
    pub tone: Tone,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GateView {
    pub label: String,
    pub status: String,
    pub tone: Tone,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PositionView {
    pub symbol: String,
    pub side: String,
    pub qty: String,
    pub avg: String,
    pub ltp: String,
    pub value: String,
    pub pnl: String,
    pub pnl_tone: Tone,
    pub alloc: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AllocView {
    pub symbol: String,
    pub pct_label: String,
    pub pct: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderView {
    pub id: String,
    pub time: String,
    pub symbol: String,
    pub side: String,
    pub qty: String,
    pub status: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FillView {
    pub time: String,
    pub symbol: String,
    pub side: String,
    pub qty: String,
    pub price: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RiskView {
    pub label: String,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DetailView {
    pub symbol: String,
    pub side: String,
    pub side_tone: Tone,
    pub qty: String,
    pub avg: String,
    pub value: String,
    pub pnl: String,
    pub pnl_tone: Tone,
    pub alloc: String,
}

/// Allocation rows rendered before the presentation cap (the label always
/// carries the true percentage — the cap is a viewport policy, same as the
/// Lab ranking precedent and the Qt top-8 rule).
pub const ALLOCATION_VIEW_CAP: usize = 8;

#[derive(Debug, Clone, PartialEq)]
pub struct PortfolioView {
    pub account_title: String,
    pub account_tone: Tone,
    pub account_info: String,
    pub account_detail: String,
    pub kpis_primary: Vec<KpiView>,
    pub kpis_secondary: Vec<KpiView>,
    pub perf_has_data: bool,
    pub perf_trades: String,
    pub perf_wins: String,
    pub perf_losses: String,
    pub perf_winrate: String,
    pub gates: Vec<GateView>,
    pub positions_count: String,
    pub positions: Vec<PositionView>,
    pub positions_empty_title: String,
    pub positions_empty_detail: String,
    pub allocation: Vec<AllocView>,
    pub alloc_has_data: bool,
    pub detail: DetailView,
    pub orders_tab: usize,
    pub orders: Vec<OrderView>,
    pub fills: Vec<FillView>,
    /// Empty state for the ACTIVE tab (orders-tab aware, configured aware).
    pub active_empty_title: String,
    pub active_empty_detail: String,
    pub active_has_rows: bool,
    pub health_label: String,
    pub health_tone: Tone,
    pub health_note: String,
    pub risk_rows: Vec<RiskView>,
    pub risk_has_data: bool,
    pub risk_empty_detail: String,
}

fn unavailable_kpis() -> (Vec<KpiView>, Vec<KpiView>) {
    let primary = ["TOTAL EQUITY", "AVAILABLE", "INVESTED", "TOTAL P&L"];
    let secondary = ["TODAY P&L", "UNREALIZED", "REALIZED", "RETURN"];
    let mk = |label: &str| KpiView {
        label: label.to_string(),
        value: "Unavailable".to_string(),
        tone: Tone::Muted,
    };
    (
        primary.iter().map(|l| mk(l)).collect(),
        secondary.iter().map(|l| mk(l)).collect(),
    )
}

pub fn project(state: &PortfolioState) -> PortfolioView {
    let snapshot = &state.snapshot;
    let configured = is_configured(snapshot);

    // ── account status (compact; never claims primary space) ──
    let (account_title, account_tone, account_info, account_detail) = if configured {
        let name = snapshot.broker.name.clone();
        let masked = if name.len() > 4 {
            name[name.len() - 4..].to_string()
        } else {
            name.clone()
        };
        let mut info_parts = Vec::new();
        if !masked.is_empty() {
            info_parts.push(format!("Account  XXXX{masked}"));
        }
        if !snapshot.broker.environment.is_empty() {
            info_parts.push(format!("Environment  {}", snapshot.broker.environment));
        }
        let mut detail_parts = Vec::new();
        if !snapshot.mode.is_empty() {
            detail_parts.push(format!("Mode {}", snapshot.mode));
        }
        if !snapshot.lifecycle.is_empty() {
            detail_parts.push(format!("Lifecycle {}", snapshot.lifecycle));
        }
        (
            "ACCOUNT CONNECTED".to_string(),
            Tone::Positive,
            info_parts.join("     "),
            detail_parts.join("     "),
        )
    } else {
        (
            "ACCOUNT NOT CONFIGURED".to_string(),
            Tone::Warning,
            "Connect/configure a broker account to view live portfolio information.".to_string(),
            String::new(),
        )
    };

    // ── KPI bands (primary value first, hierarchy preserved) ──
    let (kpis_primary, kpis_secondary) = if !configured {
        unavailable_kpis()
    } else {
        let funds = snapshot.funds.clone().unwrap_or_default();
        let pnl = &snapshot.pnl;
        let equity = |v: Option<f64>| v.map(format_equity).unwrap_or_else(|| "N/A".to_string());
        let money = |v: Option<f64>| KpiView {
            label: String::new(),
            value: v.map(format_money).unwrap_or_else(|| "N/A".to_string()),
            tone: tone_of(v),
        };
        let mut total = money(pnl.total);
        total.label = "TOTAL P&L".to_string();
        let mut today = money(pnl.today);
        today.label = "TODAY P&L".to_string();
        let mut unrealized = money(pnl.unrealized);
        unrealized.label = "UNREALIZED".to_string();
        let mut realized = money(pnl.realized);
        realized.label = "REALIZED".to_string();
        let (return_value, return_tone) = match (equity_of(snapshot), pnl.total) {
            (Some(eq), Some(total_pnl)) if eq != 0.0 => {
                let pct = 100.0 * total_pnl / eq;
                (format_pct(pct), tone_of(Some(pct)))
            }
            _ => ("N/A".to_string(), Tone::Muted),
        };
        (
            vec![
                KpiView {
                    label: "TOTAL EQUITY".to_string(),
                    value: equity(funds.equity),
                    tone: Tone::Neutral,
                },
                KpiView {
                    label: "AVAILABLE".to_string(),
                    value: equity(funds.available),
                    tone: Tone::Neutral,
                },
                KpiView {
                    label: "INVESTED".to_string(),
                    value: equity(funds.used),
                    tone: Tone::Neutral,
                },
                total,
            ],
            vec![
                today,
                unrealized,
                realized,
                KpiView {
                    label: "RETURN".to_string(),
                    value: return_value,
                    tone: return_tone,
                },
            ],
        )
    };

    // ── performance summary (honest compact state, never a giant chart) ──
    let pnl = &snapshot.pnl;
    let trades = match (pnl.wins, pnl.losses) {
        (Some(w), Some(l)) => Some(w.saturating_add(l)),
        _ => None,
    };
    let winrate = match (pnl.wins, trades) {
        (Some(w), Some(t)) if t > 0 => format!("{:.1}%", 100.0 * w as f64 / t as f64),
        _ => "N/A".to_string(),
    };
    let perf_has_data = trades.is_some() || pnl.total.is_some();
    let opt_u64 = |v: Option<u64>| {
        v.map(|n| n.to_string())
            .unwrap_or_else(|| "N/A".to_string())
    };

    // ── status gates (always rendered; NOT READY when unconfigured) ──
    let gates = if !configured {
        ["Trading", "Market Data", "Account Sync", "Risk Engine"]
            .iter()
            .map(|label| GateView {
                label: (*label).to_string(),
                status: "NOT READY".to_string(),
                tone: Tone::Muted,
                detail: "Account not configured".to_string(),
            })
            .collect()
    } else {
        let halted = snapshot.kill_halted;
        let blocked = ["BLOCKED", "HALTED"]
            .iter()
            .any(|s| *s == snapshot.risk_status.as_str());
        let trading_ok = !halted && !blocked;
        let b_connected = snapshot.broker.connected == Some(true)
            || ["CONNECTED", "LIVE_READY"]
                .iter()
                .any(|s| *s == snapshot.broker.status.as_str());
        let sync_ok = snapshot.recon_status == "CLEAN";
        let risk_ok = snapshot.risk_status == "READY";
        vec![
            GateView {
                label: "Trading".to_string(),
                status: if trading_ok { "READY" } else { "BLOCKED" }.to_string(),
                tone: if trading_ok {
                    Tone::Positive
                } else {
                    Tone::Negative
                },
                detail: if halted {
                    "Halted".to_string()
                } else if trading_ok {
                    "Active".to_string()
                } else {
                    snapshot.risk_status.clone()
                },
            },
            GateView {
                label: "Market Data".to_string(),
                status: if b_connected { "READY" } else { "NOT READY" }.to_string(),
                tone: if b_connected {
                    Tone::Positive
                } else {
                    Tone::Muted
                },
                detail: if b_connected {
                    "Connected".to_string()
                } else {
                    "Unavailable".to_string()
                },
            },
            GateView {
                label: "Account Sync".to_string(),
                status: if sync_ok { "READY" } else { "NOT READY" }.to_string(),
                tone: if sync_ok { Tone::Positive } else { Tone::Muted },
                detail: if sync_ok {
                    "Healthy".to_string()
                } else {
                    snapshot.recon_status.clone()
                },
            },
            GateView {
                label: "Risk Engine".to_string(),
                status: if risk_ok { "READY" } else { "NOT READY" }.to_string(),
                tone: if risk_ok { Tone::Positive } else { Tone::Muted },
                detail: if risk_ok {
                    "Active".to_string()
                } else {
                    snapshot.risk_status.clone()
                },
            },
        ]
    };

    // ── positions (primary feature; readable rows, never squeezed) ──
    let rows = positions_of(snapshot);
    let equity = equity_of(snapshot);
    let positions: Vec<PositionView> = rows
        .iter()
        .map(|entry| {
            let alloc = match equity {
                Some(eq) if eq != 0.0 => format!("{:.1}%", 100.0 * entry.exposure_value() / eq),
                _ => "N/A".to_string(),
            };
            PositionView {
                symbol: entry.symbol.clone(),
                side: entry.side.clone(),
                qty: format_scalar(entry.quantity),
                avg: entry
                    .avg_price
                    .map(format_scalar)
                    .unwrap_or_else(|| "N/A".to_string()),
                ltp: entry
                    .current_price
                    .map(format_scalar)
                    .unwrap_or_else(|| "N/A".to_string()),
                value: Some(entry.exposure_value())
                    .map(format_scalar)
                    .unwrap_or_else(|| "N/A".to_string()),
                pnl: entry
                    .pnl
                    .map(format_money)
                    .unwrap_or_else(|| "N/A".to_string()),
                pnl_tone: tone_of(entry.pnl),
                alloc,
            }
        })
        .collect();
    let positions_count = if positions.is_empty() {
        String::new()
    } else if positions.len() == 1 {
        "1 open position".to_string()
    } else {
        format!("{} open positions", positions.len())
    };
    let (positions_empty_title, positions_empty_detail) = if !positions.is_empty() {
        (String::new(), String::new())
    } else if configured {
        (
            "NO OPEN POSITIONS".to_string(),
            "Your portfolio is currently flat.".to_string(),
        )
    } else {
        (
            "ACCOUNT NOT CONFIGURED".to_string(),
            "Connect/configure an account to view positions.".to_string(),
        )
    };

    // ── allocation (meaningful analysis; compact when basis missing) ──
    let entries = allocation_of(snapshot);
    let alloc_has_data = !entries.is_empty();
    let allocation: Vec<AllocView> = entries
        .iter()
        .take(ALLOCATION_VIEW_CAP)
        .map(|(symbol, pct)| AllocView {
            symbol: symbol.clone(),
            pct_label: format!("{pct:.1}%"),
            pct: (*pct as f32).clamp(0.0, 100.0),
        })
        .collect();

    // ── position detail (row selection; honest placeholder otherwise) ──
    let detail = match state.selected_position.and_then(|i| rows.get(i)) {
        Some(entry) => {
            let alloc = match equity {
                Some(eq) if eq != 0.0 => format!("{:.1}%", 100.0 * entry.exposure_value() / eq),
                _ => "N/A".to_string(),
            };
            DetailView {
                symbol: if entry.symbol.is_empty() {
                    "No position selected".to_string()
                } else {
                    entry.symbol.clone()
                },
                side: entry.side.clone(),
                side_tone: if entry.side.is_empty() {
                    Tone::Muted
                } else {
                    Tone::Neutral
                },
                qty: format_scalar(entry.quantity),
                avg: entry
                    .avg_price
                    .map(format_scalar)
                    .unwrap_or_else(|| "N/A".to_string()),
                value: format_scalar(entry.exposure_value()),
                pnl: entry
                    .pnl
                    .map(format_money)
                    .unwrap_or_else(|| "N/A".to_string()),
                pnl_tone: tone_of(entry.pnl),
                alloc,
            }
        }
        None => DetailView {
            symbol: "No position selected".to_string(),
            side: String::new(),
            side_tone: Tone::Muted,
            qty: "N/A".to_string(),
            avg: "N/A".to_string(),
            value: "N/A".to_string(),
            pnl: "N/A".to_string(),
            pnl_tone: Tone::Muted,
            alloc: "N/A".to_string(),
        },
    };

    // ── orders / fills (usable tables; compact truthful empty states) ──
    let orders: Vec<OrderView> = snapshot
        .orders
        .iter()
        .map(|o| OrderView {
            id: o.order_id.clone(),
            time: o.time.clone(),
            symbol: o.symbol.clone(),
            side: o.side.clone(),
            qty: format_scalar(o.quantity),
            status: o.status.clone(),
        })
        .collect();
    let fills: Vec<FillView> = snapshot
        .fills
        .iter()
        .map(|f| FillView {
            time: f.time.clone(),
            symbol: f.symbol.clone(),
            side: f.side.clone(),
            qty: format_scalar(f.quantity),
            price: format_scalar(f.price),
        })
        .collect();
    let (active_empty_title, active_empty_detail, active_has_rows) = if state.orders_tab == 1 {
        if fills.is_empty() {
            (
                "NO FILLS".to_string(),
                "No execution history available.".to_string(),
                false,
            )
        } else {
            (String::new(), String::new(), true)
        }
    } else if !orders.is_empty() {
        (String::new(), String::new(), true)
    } else if configured {
        (
            "NO ORDERS".to_string(),
            "No orders are available for the selected period.".to_string(),
            false,
        )
    } else {
        (
            "ORDERS UNAVAILABLE".to_string(),
            "Account connection is required.".to_string(),
            false,
        )
    };

    // ── risk (never clipped; N/A where the backend is silent) ──
    let largest = entries.first().cloned();
    let (health_label, health_tone, health_note) = if !configured {
        (
            "NOT CONFIGURED".to_string(),
            Tone::Muted,
            "Connect an account to assess portfolio health.".to_string(),
        )
    } else if ["BLOCKED", "HALTED"]
        .iter()
        .any(|s| *s == snapshot.risk_status.as_str())
    {
        (
            "BLOCKED".to_string(),
            Tone::Negative,
            "Risk engine is blocking activity.".to_string(),
        )
    } else if let Some((_, pct)) = &largest {
        if *pct >= 50.0 {
            (
                "CONCENTRATED".to_string(),
                Tone::Warning,
                format!("Largest position is {pct:.1}% of equity (≥50% rule)."),
            )
        } else {
            (
                "HEALTHY".to_string(),
                Tone::Positive,
                "No blocking risk conditions detected.".to_string(),
            )
        }
    } else {
        (
            "HEALTHY".to_string(),
            Tone::Positive,
            "No blocking risk conditions detected.".to_string(),
        )
    };
    let mut risk_rows = Vec::new();
    let mut shown = 0usize;
    if rows.is_empty() {
        risk_rows.push(RiskView {
            label: "Exposure".to_string(),
            value: "N/A".to_string(),
        });
    } else {
        let total: f64 = rows.iter().map(|p| p.exposure_value()).sum();
        risk_rows.push(RiskView {
            label: "Exposure".to_string(),
            value: format_equity(total),
        });
        shown += 1;
    }
    match &largest {
        Some((symbol, pct)) => {
            risk_rows.push(RiskView {
                label: "Largest Position".to_string(),
                value: format!("{symbol}  {pct:.1}%"),
            });
            risk_rows.push(RiskView {
                label: "Concentration".to_string(),
                value: format!("{pct:.1}%"),
            });
            shown += 2;
        }
        None => {
            for label in ["Largest Position", "Concentration"] {
                risk_rows.push(RiskView {
                    label: label.to_string(),
                    value: "N/A".to_string(),
                });
            }
        }
    }
    for (label, raw) in [
        ("Drawdown", snapshot.risk_drawdown),
        ("Volatility", snapshot.risk_volatility),
    ] {
        match raw {
            Some(v) => {
                risk_rows.push(RiskView {
                    label: label.to_string(),
                    value: format!("{v:.2}%"),
                });
                shown += 1;
            }
            None => risk_rows.push(RiskView {
                label: label.to_string(),
                value: "N/A".to_string(),
            }),
        }
    }
    let risk_has_data = shown > 0;
    let risk_empty_detail = if !configured {
        "Connect an account to access portfolio health.".to_string()
    } else {
        "Risk metrics are not provided by the current backend.".to_string()
    };

    PortfolioView {
        account_title,
        account_tone,
        account_info,
        account_detail,
        kpis_primary,
        kpis_secondary,
        perf_has_data,
        perf_trades: trades
            .map(|t| t.to_string())
            .unwrap_or_else(|| "N/A".to_string()),
        perf_wins: opt_u64(pnl.wins),
        perf_losses: opt_u64(pnl.losses),
        perf_winrate: winrate,
        gates,
        positions_count,
        positions,
        positions_empty_title,
        positions_empty_detail,
        allocation,
        alloc_has_data,
        detail,
        orders_tab: state.orders_tab,
        orders,
        fills,
        active_empty_title,
        active_empty_detail,
        active_has_rows,
        health_label,
        health_tone,
        health_note,
        risk_rows,
        risk_has_data,
        risk_empty_detail,
    }
}

// ── representative snapshots (demo/verify only — backend-shaped facts) ──────

/// Honest disconnected snapshot: the shape the bridge feeds before any
/// account is configured. No numbers invented — every value is absent.
pub fn demo_unconfigured_snapshot() -> PortfolioSnapshot {
    PortfolioSnapshot {
        broker: BrokerFacts {
            name: "NOT CONFIGURED".to_string(),
            ..BrokerFacts::default()
        },
        ..PortfolioSnapshot::default()
    }
}

/// Representative FUNDED snapshot mirroring the retained Qt workspace test
/// state (paper account, one TEST position, one order + fill). Used for
/// render-matrix verification — never shipped as product data.
pub fn demo_configured_snapshot() -> PortfolioSnapshot {
    PortfolioSnapshot {
        broker: BrokerFacts {
            name: "paper".to_string(),
            environment: "paper".to_string(),
            connected: Some(true),
            status: "CONNECTED".to_string(),
        },
        funds: Some(FundsFacts {
            equity: Some(100_000.0),
            available: Some(80_000.0),
            used: Some(20_000.0),
        }),
        position: Some(PositionFacts {
            symbol: "TEST".to_string(),
            side: "LONG".to_string(),
            quantity: 10.0,
            avg_price: Some(100.0),
            current_price: Some(110.0),
            invested: Some(1_100.0),
            value: Some(1_100.0),
            exposure: Some(1_100.0),
            pnl: Some(100.0),
        }),
        orders: vec![OrderFacts {
            order_id: "c1".to_string(),
            symbol: "TEST".to_string(),
            side: "BUY".to_string(),
            quantity: 10.0,
            status: "FILLED".to_string(),
            ..OrderFacts::default()
        }],
        fills: vec![FillFacts {
            time: "t".to_string(),
            symbol: "TEST".to_string(),
            quantity: 10.0,
            price: 100.0,
            ..FillFacts::default()
        }],
        pnl: PnlFacts {
            total: Some(100.0),
            unrealized: Some(100.0),
            realized: Some(0.0),
            wins: Some(1),
            losses: Some(0),
            ..PnlFacts::default()
        },
        risk_status: "READY".to_string(),
        recon_status: "CLEAN".to_string(),
        lifecycle: "RUNNING".to_string(),
        mode: "PAPER".to_string(),
        ..PortfolioSnapshot::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn configured_state() -> PortfolioState {
        PortfolioState {
            snapshot: demo_configured_snapshot(),
            ..PortfolioState::default()
        }
    }

    fn unconfigured_state() -> PortfolioState {
        PortfolioState {
            snapshot: demo_unconfigured_snapshot(),
            ..PortfolioState::default()
        }
    }

    #[test]
    fn unconfigured_state_is_truthful_never_zero_filled() {
        let view = project(&unconfigured_state());
        assert_eq!(view.account_title, "ACCOUNT NOT CONFIGURED");
        assert_eq!(view.account_tone, Tone::Warning);
        // All eight KPIs honestly unavailable — never squeezed numbers.
        assert_eq!(view.kpis_primary.len(), 4);
        assert_eq!(view.kpis_secondary.len(), 4);
        assert!(view
            .kpis_primary
            .iter()
            .chain(view.kpis_secondary.iter())
            .all(|k| k.value == "Unavailable" && k.tone == Tone::Muted));
        // Compact empty states, not giant blank containers.
        assert!(!view.perf_has_data);
        assert_eq!(view.positions_empty_title, "ACCOUNT NOT CONFIGURED");
        assert!(view.positions.is_empty());
        assert!(!view.alloc_has_data);
        assert!(view.allocation.is_empty());
        assert_eq!(view.active_empty_title, "ORDERS UNAVAILABLE");
        assert!(!view.active_has_rows);
        assert_eq!(view.health_label, "NOT CONFIGURED");
        assert!(!view.risk_has_data);
        // Gates never claim readiness without a backend.
        assert!(view.gates.iter().all(|g| g.status == "NOT READY"));
    }

    #[test]
    fn configured_state_renders_hierarchy_in_order() {
        let view = project(&configured_state());
        assert_eq!(view.account_title, "ACCOUNT CONNECTED");
        assert!(view.kpis_primary[0].value.contains("100,000"));
        assert_eq!(view.kpis_primary[0].label, "TOTAL EQUITY");
        assert_eq!(view.kpis_primary[3].value, "+100.00");
        assert_eq!(view.kpis_primary[3].tone, Tone::Positive);
        // RETURN derives from backend facts only (100 / 100000).
        assert_eq!(view.kpis_secondary[3].value, "+0.10%");
        // Performance summary from real wins/losses.
        assert!(view.perf_has_data);
        assert_eq!(view.perf_trades, "1");
        assert_eq!(view.perf_wins, "1");
        assert_eq!(view.perf_winrate, "100.0%");
        // Positions stay readable with derived allocation.
        assert_eq!(view.positions.len(), 1);
        assert_eq!(view.positions[0].symbol, "TEST");
        assert_eq!(view.positions[0].alloc, "1.1%");
        assert_eq!(view.positions_count, "1 open position");
        // Allocation + risk carry the same basis.
        assert!(view.alloc_has_data);
        assert_eq!(view.allocation[0].symbol, "TEST");
        assert_eq!(view.allocation[0].pct_label, "1.1%");
        assert_eq!(view.health_label, "HEALTHY");
        assert!(view.risk_has_data);
        assert!(view.active_has_rows);
    }

    #[test]
    fn flat_book_is_an_explicit_empty_not_a_blank_table() {
        let mut state = configured_state();
        state.snapshot.position = None;
        state.snapshot.position_flat = true;
        state.snapshot.positions.clear();
        let view = project(&state);
        assert!(view.positions.is_empty());
        assert_eq!(view.positions_empty_title, "NO OPEN POSITIONS");
        assert!(!view.alloc_has_data);
    }

    #[test]
    fn missing_funds_is_unavailable_not_zero() {
        let mut state = configured_state();
        state.snapshot.funds = None;
        // Broker still reports connected, so the book stays configured —
        // but every money value degrades to N/A, never 0.
        let view = project(&state);
        assert_eq!(view.kpis_primary[0].value, "N/A");
        assert_eq!(view.positions[0].alloc, "N/A");
        assert!(!view.alloc_has_data);
    }

    #[test]
    fn allocation_sorts_descending_and_caps_at_eight() {
        let mut state = configured_state();
        state.snapshot.positions = (0..10)
            .map(|i| PositionFacts {
                symbol: format!("S{i:02}"),
                exposure: Some(1_000.0 * (i + 1) as f64),
                quantity: 1.0,
                ..PositionFacts::default()
            })
            .collect();
        state.snapshot.position = None;
        let view = project(&state);
        assert_eq!(view.allocation.len(), ALLOCATION_VIEW_CAP);
        assert_eq!(view.allocation[0].symbol, "S09");
        let first: f32 = view.allocation[0].pct;
        let last: f32 = view.allocation[7].pct;
        assert!(first > last);
    }

    #[test]
    fn health_concentration_rule_matches_legacy_threshold() {
        let mut state = configured_state();
        state.snapshot.position = Some(PositionFacts {
            symbol: "BIG".to_string(),
            exposure: Some(60_000.0),
            quantity: 1.0,
            ..PositionFacts::default()
        });
        let view = project(&state);
        assert_eq!(view.health_label, "CONCENTRATED");
        assert_eq!(view.health_tone, Tone::Warning);
    }

    #[test]
    fn blocked_risk_never_renders_healthy() {
        let mut state = configured_state();
        state.snapshot.risk_status = "BLOCKED".to_string();
        let view = project(&state);
        assert_eq!(view.health_label, "BLOCKED");
        assert_eq!(view.health_tone, Tone::Negative);
        assert_eq!(view.gates[0].status, "BLOCKED");
    }

    #[test]
    fn negative_pnl_carries_negative_tone_everywhere() {
        let mut state = configured_state();
        state.snapshot.pnl.total = Some(-250.0);
        state.snapshot.position = Some(PositionFacts {
            symbol: "TEST".to_string(),
            pnl: Some(-250.0),
            quantity: 10.0,
            exposure: Some(1_100.0),
            ..PositionFacts::default()
        });
        let view = project(&state);
        assert_eq!(view.kpis_primary[3].tone, Tone::Negative);
        assert_eq!(view.positions[0].pnl_tone, Tone::Negative);
        assert_eq!(view.kpis_secondary[3].tone, Tone::Negative);
    }

    #[test]
    fn orders_tab_drives_active_empty_state() {
        let state = configured_state();
        assert_eq!(project(&state).orders_tab, 0);
        assert!(project(&state).active_has_rows);
        let mut fills_tab = state.clone();
        fills_tab.set_orders_tab(1);
        let view = project(&fills_tab);
        assert_eq!(view.orders_tab, 1);
        assert!(view.active_has_rows);
        assert_eq!(view.fills.len(), 1);
        // Invalid tab indices are no-ops, never a broken layout.
        fills_tab.set_orders_tab(99);
        assert_eq!(fills_tab.orders_tab, 1);
    }

    #[test]
    fn position_selection_drives_detail_without_navigation() {
        let mut state = configured_state();
        assert_eq!(project(&state).detail.symbol, "No position selected");
        assert!(state.select_position(0));
        let view = project(&state);
        assert_eq!(view.detail.symbol, "TEST");
        assert_eq!(view.detail.qty, "10.00");
        assert!(!state.select_position(99));
    }

    /// First-principles layout contract on the Slint source itself: no fixed
    /// page geometry (only token heights, fills, hairlines and the 2px tab
    /// accent), exactly one page-level ScrollView (content scrolls instead of
    /// squeezing into overlap), token-only color (no hex literals — the
    /// palette owns every value), and responsive folding derived from the
    /// design-system fold width (never a screenshot constant).
    #[test]
    fn slint_layout_contract_first_principles() {
        let source = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/ui/portfolio.slint"));
        assert!(
            !source.contains('#'),
            "UI code must not hardcode colors — VayrenPalette owns every value"
        );
        assert_eq!(
            source.matches("ScrollView {").count(),
            1,
            "exactly one page-level scroll container — children never squeeze each other"
        );
        for line in source.lines() {
            let stripped = line.trim_start();
            if let Some(value) = stripped
                .strip_prefix("height:")
                .or_else(|| stripped.strip_prefix("width:"))
            {
                let allowed = ["VayrenDesign", "100%", "parent.", "1px", "2px"];
                assert!(
                    allowed.iter().any(|token| value.contains(token)),
                    "fixed geometry is forbidden (got `{line}`) — use natural sizing, \
                     fills, minimums, or design tokens"
                );
            }
        }
        assert!(
            source.contains("VayrenDesign.kpi-fold-width"),
            "responsive folding must derive from the design-system fold width"
        );
    }

    #[test]
    fn formatting_matches_legacy_conventions() {
        assert_eq!(format_equity(100_000.0), "100,000");
        assert_eq!(format_equity(1_100.0), "1,100.00");
        assert_eq!(format_money(100.0), "+100.00");
        assert_eq!(format_money(-5.5), "-5.50");
        assert_eq!(format_pct(0.1), "+0.10%");
        assert_eq!(format_scalar(10.0), "10.00");
    }

    /// Bridge contract: the exact JSON the Python exporter emits for a funded
    /// paper book must parse into the configured snapshot (pinned field by
    /// field — this is the cross-language handshake, not a demo).
    #[test]
    fn from_json_parses_funded_bridge_snapshot() {
        let text = r#"{
            "broker": {"name": "paper", "environment": "paper",
                       "connected": true, "status": "CONNECTED"},
            "funds": {"equity": 100000.0, "available": 80000.0, "used": 20000.0},
            "position": {"symbol": "TEST", "side": "LONG", "quantity": 10,
                         "avg_price": 100.0, "current_price": 110.0,
                         "exposure": 1100.0, "unrealized": 100.0},
            "positions": [],
            "orders": [{"order_id": "c1", "time": "", "symbol": "TEST",
                        "side": "BUY", "quantity": 10, "status": "FILLED"}],
            "fills": [{"time": "t", "symbol": "TEST", "side": "",
                       "quantity": 10, "price": 100.0}],
            "pnl": {"total": 100.0, "unrealized": 100.0, "realized": 0.0,
                    "today": null, "wins": 1, "losses": 0},
            "risk": {"status": "READY"},
            "reconciliation": {"status": "CLEAN"},
            "kill": {"halted": false},
            "lifecycle": "RUNNING",
            "mode": "PAPER",
            "risk_metrics": null
        }"#;
        let value: serde_json::Value = serde_json::from_str(text).unwrap();
        let snapshot = PortfolioSnapshot::from_json(&value);
        assert_eq!(snapshot.broker.name, "paper");
        assert_eq!(snapshot.broker.connected, Some(true));
        assert_eq!(snapshot.funds.as_ref().unwrap().equity, Some(100_000.0));
        assert!(!snapshot.position_flat);
        assert_eq!(snapshot.positions.len(), 0);
        assert_eq!(snapshot.orders.len(), 1);
        assert_eq!(snapshot.orders[0].quantity, 10.0);
        assert_eq!(snapshot.pnl.wins, Some(1));
        assert_eq!(snapshot.pnl.today, None);
        assert_eq!(snapshot.risk_status, "READY");
        assert_eq!(snapshot.recon_status, "CLEAN");
        assert!(!snapshot.kill_halted);
        assert_eq!(snapshot.risk_drawdown, None);
        // Must project exactly like the hand-built configured snapshot.
        let funded = PortfolioState {
            snapshot,
            ..PortfolioState::default()
        };
        let view = project(&funded);
        assert_eq!(view.account_title, "ACCOUNT CONNECTED");
        assert_eq!(view.positions.len(), 1);
        assert_eq!(view.positions[0].symbol, "TEST");
        assert_eq!(view.health_label, "HEALTHY");
    }

    /// Bridge contract: absent/mistyped fields degrade to honest absence —
    /// never invented, never zero-filled, never a crash.
    #[test]
    fn from_json_degrades_honestly() {
        let value: serde_json::Value = serde_json::from_str(
            r#"{"broker": {"name": "NOT CONFIGURED"},
                "funds": {}, "position": {"flat": true},
                "pnl": {"total": "oops", "wins": -3},
                "risk": "broken", "orders": [41], "fills": null,
                "risk_metrics": {"drawdown": "n/a"}}"#,
        )
        .unwrap();
        let snapshot = PortfolioSnapshot::from_json(&value);
        // Empty funds mapping counts as absent (legacy `_is_configured` parity).
        assert_eq!(snapshot.funds, None);
        assert!(snapshot.position_flat);
        assert_eq!(snapshot.pnl.total, None);
        assert_eq!(snapshot.pnl.wins, None);
        assert_eq!(snapshot.risk_status, "N/A");
        assert_eq!(snapshot.orders.len(), 0);
        assert_eq!(snapshot.fills.len(), 0);
        assert_eq!(snapshot.risk_drawdown, None);
        assert!(!is_configured(&snapshot));
        // Non-object root degrades to the honest empty snapshot (status
        // blocks read "N/A", mirroring the legacy `.get("status", _NA)`).
        let empty = PortfolioSnapshot::from_json(&serde_json::Value::Null);
        assert_eq!(empty.funds, None);
        assert_eq!(empty.positions.len(), 0);
        assert_eq!(empty.risk_status, "N/A");
        assert_eq!(empty.recon_status, "N/A");
        assert!(!is_configured(&empty));
    }
}
