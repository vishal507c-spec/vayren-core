//! Backtest engine — ExecutionSimulator, PositionManager, TradeJournal.
//!
//! Hot loop for 100K+ bars/sec: Fill → Position → Trade → Metrics.
//! Python port: 06_backtest/backtest/engine/*.py

/// Trade side.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Long,
    Short,
}

impl Side {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_uppercase().as_str() {
            "LONG" => Some(Self::Long),
            "SHORT" => Some(Self::Short),
            _ => None,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Long => "LONG",
            Self::Short => "SHORT",
        }
    }

    /// C ABI code (`vy_bt_*`).
    pub fn code(&self) -> i32 {
        match self {
            Self::Long => 0,
            Self::Short => 1,
        }
    }

    pub fn from_code(code: i32) -> Option<Self> {
        match code {
            0 => Some(Self::Long),
            1 => Some(Self::Short),
            _ => None,
        }
    }
}

/// Exit reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitReason {
    Signal,
    StopLoss,
    TakeProfit,
    End,
}

impl ExitReason {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Signal => "SIGNAL",
            Self::StopLoss => "SL",
            Self::TakeProfit => "TP",
            Self::End => "END",
        }
    }

    /// C ABI code (`vy_bt_*`); 0 is reserved for "no exit".
    pub fn code(&self) -> i32 {
        match self {
            Self::Signal => 1,
            Self::StopLoss => 2,
            Self::TakeProfit => 3,
            Self::End => 4,
        }
    }

    pub fn from_code(code: i32) -> Option<Self> {
        match code {
            1 => Some(Self::Signal),
            2 => Some(Self::StopLoss),
            3 => Some(Self::TakeProfit),
            4 => Some(Self::End),
            _ => None,
        }
    }
}

/// One execution: direction, price, quantity, fee.
#[derive(Debug, Clone, PartialEq)]
pub struct Fill {
    pub side: Side,
    pub fill_price: f64,
    pub quantity: f64,
    pub commission: f64,
    pub sl_price: Option<f64>,
    pub tp_price: Option<f64>,
}

/// Deterministic filler: signal → fill at bar's close ± slippage.
#[derive(Debug, Clone)]
pub struct ExecutionSimulator {
    slippage_pct: f64,
    commission_pct: f64,
}

impl ExecutionSimulator {
    pub fn new(slippage_pct: f64, commission_pct: f64) -> Self {
        Self {
            slippage_pct: slippage_pct.max(0.0),
            commission_pct: commission_pct.max(0.0),
        }
    }

    /// Return a Fill for bar_close, or None when not affordable.
    pub fn fill(&self, signal_side: Side, bar_close: f64, available_equity: f64) -> Option<Fill> {
        if available_equity <= 0.0 || bar_close <= 0.0 {
            return None;
        }
        let slip = bar_close * (self.slippage_pct / 100.0);
        let fill_price = match signal_side {
            Side::Long => bar_close + slip,
            Side::Short => bar_close - slip,
        };
        if fill_price <= 0.0 {
            return None;
        }
        let quantity = available_equity / fill_price;
        if quantity <= 0.0 {
            return None;
        }
        let commission = fill_price * quantity * (self.commission_pct / 100.0);
        Some(Fill {
            side: signal_side,
            fill_price,
            quantity,
            commission,
            sl_price: None,
            tp_price: None,
        })
    }
}

impl Default for ExecutionSimulator {
    fn default() -> Self {
        Self::new(0.02, 0.03)
    }
}

/// Packed fill result for the Python boundary (`vy_bt_fill`).
///
/// Layout: three `f64` then two `i32` — 32 bytes, no padding holes.
#[repr(C)]
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct FillOut {
    pub fill_price: f64,
    pub quantity: f64,
    pub commission: f64,
    /// Direction the kernel priced (1 LONG, 2 SHORT).
    pub side: i32,
    pub _pad: i32,
}

impl FillOut {
    pub const SIDE_LONG: i32 = 1;
    pub const SIDE_SHORT: i32 = 2;

    fn from_fill(side: Side, fill: &Fill) -> Self {
        Self {
            fill_price: fill.fill_price,
            quantity: fill.quantity,
            commission: fill.commission,
            side: match side {
                Side::Long => Self::SIDE_LONG,
                Side::Short => Self::SIDE_SHORT,
            },
            _pad: 0,
        }
    }
}

/// Direction rule of the entry filler: the leg is long only when the signal
/// says exactly `LONG`; every other label is priced as a short. Kept here so
/// the boundary receives a verdict, not a string to interpret.
pub fn side_of_signal(side: &str) -> Side {
    if side == "LONG" {
        Side::Long
    } else {
        Side::Short
    }
}

/// Price one entry from raw percentages (clamped like `ExecutionSimulator::new`).
pub fn fill_entry(
    side: &str,
    bar_close: f64,
    available_equity: f64,
    slippage_pct: f64,
    commission_pct: f64,
) -> Option<FillOut> {
    let direction = side_of_signal(side);
    let fill = ExecutionSimulator::new(slippage_pct, commission_pct).fill(
        direction,
        bar_close,
        available_equity,
    )?;
    Some(FillOut::from_fill(direction, &fill))
}

/// One closed position: entry → exit.
#[derive(Debug, Clone, PartialEq)]
pub struct TradeRecord {
    pub symbol: String,
    pub side: Side,
    pub entry_index: usize,
    pub exit_index: usize,
    pub entry_time: String,
    pub exit_time: String,
    pub entry_price: f64,
    pub exit_price: f64,
    pub quantity: f64,
    pub pnl: f64,
    pub pnl_pct: f64,
    pub commission: f64,
    pub bars_held: usize,
    pub exit_reason: ExitReason,
    pub r_multiple: Option<f64>,
}

impl TradeRecord {
    pub fn winning(&self) -> bool {
        self.pnl > 0.0
    }
}

/// Slippage the bar-exit rule fills a signal exit at, in percent. Historical
/// constant of `PositionManager.try_close`; the runner's own config-driven
/// exits pass their percentage explicitly.
pub const SIGNAL_EXIT_SLIPPAGE_PCT: f64 = 0.02;

/// The one exit-slippage formula: the bar close moved *against* the position.
/// Signal exits and end-of-run closes both price off this.
pub fn slipped_exit_price(side: Side, bar_close: f64, slippage_pct: f64) -> f64 {
    let slip = bar_close * (slippage_pct / 100.0);
    match side {
        Side::Long => bar_close - slip,
        Side::Short => bar_close + slip,
    }
}

/// Does a buy signal close the open leg? Only the opposite of what is held.
pub fn closes_on_signal(side: Side, is_buy: bool) -> bool {
    matches!(side, Side::Long) != is_buy
}

/// The exit a bar triggers for the open leg: `(price, reason)`, or `None` when
/// the position stays open.
///
/// Mirrors the rule table in `PositionManager.try_close`
/// (`06_backtest/backtest/engine/positions.py`): a signal exit fills at the
/// close with [`SIGNAL_EXIT_SLIPPAGE_PCT`] against the position; otherwise the
/// stop-loss is checked before the take-profit, on the bar extreme that
/// favours each leg.
pub fn exit_for_bar(
    side: Side,
    sl_price: Option<f64>,
    tp_price: Option<f64>,
    bar_high: f64,
    bar_low: f64,
    bar_close: f64,
    exit_signal: bool,
) -> Option<(f64, ExitReason)> {
    if exit_signal {
        return Some((
            slipped_exit_price(side, bar_close, SIGNAL_EXIT_SLIPPAGE_PCT),
            ExitReason::Signal,
        ));
    }
    match side {
        Side::Long => {
            if let Some(sl) = sl_price {
                if bar_low <= sl {
                    return Some((sl, ExitReason::StopLoss));
                }
            }
            if let Some(tp) = tp_price {
                if bar_high >= tp {
                    return Some((tp, ExitReason::TakeProfit));
                }
            }
        }
        Side::Short => {
            if let Some(sl) = sl_price {
                if bar_high >= sl {
                    return Some((sl, ExitReason::StopLoss));
                }
            }
            if let Some(tp) = tp_price {
                if bar_low <= tp {
                    return Some((tp, ExitReason::TakeProfit));
                }
            }
        }
    }
    None
}

/// Money maths for one closed trade.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TradeEconomics {
    pub commission: f64,
    pub gross: f64,
    pub pnl: f64,
    pub pnl_pct: f64,
    pub risk: Option<f64>,
    pub r_multiple: Option<f64>,
}

/// Fees, P&L and R multiple of a close (mirrors `_close_position`).
///
/// `risk` follows the Python truthiness test on `sl_price`: an absent *or*
/// zero-priced stop leaves both `risk` and `r_multiple` undefined.
pub fn close_economics(
    side: Side,
    entry_price: f64,
    quantity: f64,
    commission_entry: f64,
    exit_price: f64,
    commission_pct: f64,
    sl_price: Option<f64>,
) -> TradeEconomics {
    let commission_exit = exit_price * quantity * (commission_pct / 100.0);
    let commission = commission_entry + commission_exit;
    let gross = match side {
        Side::Long => (exit_price - entry_price) * quantity,
        Side::Short => (entry_price - exit_price) * quantity,
    };
    let pnl = gross - commission;
    let entry_cost = entry_price * quantity;
    let pnl_pct = if entry_cost != 0.0 {
        (pnl / entry_cost) * 100.0
    } else {
        0.0
    };
    let risk = sl_price
        .filter(|sl| *sl != 0.0)
        .map(|sl| (entry_price - sl).abs() * quantity);
    let r_multiple = risk.and_then(|r| if r != 0.0 { Some(pnl / r) } else { None });
    TradeEconomics {
        commission,
        gross,
        pnl,
        pnl_pct,
        risk,
        r_multiple,
    }
}

/// Packed close result for the Python boundary (`vy_bt_*`).
///
/// Layout: five `f64` then two `i32` — 48 bytes, no padding holes.
#[repr(C)]
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct TradeOut {
    pub exit_price: f64,
    pub commission: f64,
    pub pnl: f64,
    pub pnl_pct: f64,
    /// Only meaningful while [`TradeOut::FLAG_R_MULTIPLE_NONE`] is clear.
    pub r_multiple: f64,
    /// Exit reason code (1 SIGNAL, 2 SL, 3 TP, 4 END).
    pub reason: i32,
    pub flags: i32,
}

impl TradeOut {
    /// Python renders an absent R multiple as `None`; the flag carries that.
    pub const FLAG_R_MULTIPLE_NONE: i32 = 1;

    pub fn from_economics(exit_price: f64, reason: ExitReason, econ: &TradeEconomics) -> Self {
        Self {
            exit_price,
            commission: econ.commission,
            pnl: econ.pnl,
            pnl_pct: econ.pnl_pct,
            r_multiple: econ.r_multiple.unwrap_or(f64::NAN),
            reason: reason.code(),
            flags: if econ.r_multiple.is_none() {
                Self::FLAG_R_MULTIPLE_NONE
            } else {
                0
            },
        }
    }
}

/// Internal open position.
#[derive(Debug, Clone)]
struct OpenPosition {
    symbol: String,
    side: Side,
    entry_index: usize,
    entry_time: String,
    entry_price: f64,
    quantity: f64,
    commission_entry: f64,
    sl_price: Option<f64>,
    tp_price: Option<f64>,
}

/// Read-only view of the open leg (mirrors what the runner exposes to the
/// strategy as `StrategyState.open(side, entry_price, entry_index)`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OpenPositionView {
    pub side: Side,
    pub entry_price: f64,
    pub entry_index: usize,
}

/// One position at a time (long or short).
#[derive(Debug, Clone)]
pub struct PositionManager {
    symbol: String,
    open: Option<OpenPosition>,
}

impl PositionManager {
    pub fn new(symbol: impl Into<String>) -> Self {
        Self {
            symbol: symbol.into(),
            open: None,
        }
    }

    pub fn flat(&self) -> bool {
        self.open.is_none()
    }

    /// The open leg, if any (the runner reads this to build strategy state
    /// and to detect opposite-signal exits).
    pub fn open_position(&self) -> Option<OpenPositionView> {
        self.open.as_ref().map(|op| OpenPositionView {
            side: op.side,
            entry_price: op.entry_price,
            entry_index: op.entry_index,
        })
    }

    pub fn open_long(
        &mut self,
        symbol: impl Into<String>,
        entry_index: usize,
        entry_time: impl Into<String>,
        fill_price: f64,
        quantity: f64,
        commission: f64,
        sl_price: Option<f64>,
        tp_price: Option<f64>,
    ) -> bool {
        if self.open.is_some() {
            return false;
        }
        self.symbol = symbol.into();
        self.open = Some(OpenPosition {
            symbol: self.symbol.clone(),
            side: Side::Long,
            entry_index,
            entry_time: entry_time.into(),
            entry_price: fill_price,
            quantity,
            commission_entry: commission,
            sl_price,
            tp_price,
        });
        true
    }

    pub fn open_short(
        &mut self,
        symbol: impl Into<String>,
        entry_index: usize,
        entry_time: impl Into<String>,
        fill_price: f64,
        quantity: f64,
        commission: f64,
        sl_price: Option<f64>,
        tp_price: Option<f64>,
    ) -> bool {
        if self.open.is_some() {
            return false;
        }
        self.symbol = symbol.into();
        self.open = Some(OpenPosition {
            symbol: self.symbol.clone(),
            side: Side::Short,
            entry_index,
            entry_time: entry_time.into(),
            entry_price: fill_price,
            quantity,
            commission_entry: commission,
            sl_price,
            tp_price,
        });
        true
    }

    /// Try to close on SL/TP/signal.
    pub fn try_close(
        &mut self,
        exit_index: usize,
        exit_time: impl Into<String>,
        bar_high: f64,
        bar_low: f64,
        bar_close: f64,
        commission_pct: f64,
        exit_signal: bool,
    ) -> Option<TradeRecord> {
        let entry = self.open.as_ref()?;
        let (exit_price, exit_reason) = exit_for_bar(
            entry.side,
            entry.sl_price,
            entry.tp_price,
            bar_high,
            bar_low,
            bar_close,
            exit_signal,
        )?;
        Some(self.close_position(
            exit_index,
            exit_time,
            exit_price,
            commission_pct,
            exit_reason,
        ))
    }

    pub fn close_signal(
        &mut self,
        exit_index: usize,
        exit_time: impl Into<String>,
        exit_price: f64,
        commission_pct: f64,
    ) -> Option<TradeRecord> {
        if self.open.is_none() {
            return None;
        }
        Some(self.close_position(
            exit_index,
            exit_time,
            exit_price,
            commission_pct,
            ExitReason::Signal,
        ))
    }

    pub fn close_end(
        &mut self,
        exit_index: usize,
        exit_time: impl Into<String>,
        exit_price: f64,
        commission_pct: f64,
    ) -> Option<TradeRecord> {
        if self.open.is_none() {
            return None;
        }
        Some(self.close_position(
            exit_index,
            exit_time,
            exit_price,
            commission_pct,
            ExitReason::End,
        ))
    }

    fn close_position(
        &mut self,
        exit_index: usize,
        exit_time: impl Into<String>,
        exit_price: f64,
        commission_pct: f64,
        exit_reason: ExitReason,
    ) -> TradeRecord {
        let entry = self
            .open
            .take()
            .expect("close_position called with no open position");
        let economics = close_economics(
            entry.side,
            entry.entry_price,
            entry.quantity,
            entry.commission_entry,
            exit_price,
            commission_pct,
            entry.sl_price,
        );
        TradeRecord {
            symbol: entry.symbol,
            side: entry.side,
            entry_index: entry.entry_index,
            exit_index,
            entry_time: entry.entry_time,
            exit_time: exit_time.into(),
            entry_price: entry.entry_price,
            exit_price,
            quantity: entry.quantity,
            pnl: economics.pnl,
            pnl_pct: economics.pnl_pct,
            commission: economics.commission,
            bars_held: exit_index - entry.entry_index,
            exit_reason,
            r_multiple: economics.r_multiple,
        }
    }
}

/// Collects TradeRecord objects in close order.
#[derive(Debug, Clone, Default)]
pub struct TradeJournal {
    trades: Vec<TradeRecord>,
}

impl TradeJournal {
    pub fn new() -> Self {
        Self { trades: Vec::new() }
    }

    pub fn record(&mut self, trade: TradeRecord) {
        self.trades.push(trade);
    }

    pub fn trades(&self) -> &[TradeRecord] {
        &self.trades
    }

    pub fn clear(&mut self) {
        self.trades.clear();
    }

    pub fn len(&self) -> usize {
        self.trades.len()
    }

    pub fn into_trades(self) -> Vec<TradeRecord> {
        self.trades
    }

    pub fn is_empty(&self) -> bool {
        self.trades.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn execution_simulator_fill() {
        let sim = ExecutionSimulator::new(0.1, 0.05);
        let fill = sim.fill(Side::Long, 100.0, 10000.0).unwrap();
        assert_eq!(fill.side, Side::Long);
        assert!((fill.fill_price - 100.1).abs() < 0.01); // 100 + 0.1% slip
        assert!((fill.quantity - 99.9).abs() < 0.1); // 10000 / 100.1
        assert!(fill.commission > 0.0);
    }

    #[test]
    fn execution_simulator_no_fill_when_broke() {
        let sim = ExecutionSimulator::default();
        assert!(sim.fill(Side::Long, 100.0, 0.0).is_none());
        assert!(sim.fill(Side::Long, 0.0, 10000.0).is_none());
    }

    #[test]
    fn position_manager_open_long() {
        let mut pm = PositionManager::new("AAPL");
        assert!(pm.flat());
        assert!(pm.open_long("AAPL", 0, "2024-01-01", 150.0, 100.0, 5.0, None, None));
        assert!(!pm.flat());
        // Cannot open second position
        assert!(!pm.open_long("AAPL", 1, "2024-01-02", 151.0, 100.0, 5.0, None, None));
    }

    #[test]
    fn position_manager_close_signal() {
        let mut pm = PositionManager::new("AAPL");
        pm.open_long("AAPL", 0, "2024-01-01", 150.0, 100.0, 5.0, None, None);
        let trade = pm
            .try_close(10, "2024-01-11", 160.0, 150.0, 155.0, 0.05, true)
            .unwrap();
        assert_eq!(trade.symbol, "AAPL");
        assert_eq!(trade.side, Side::Long);
        assert_eq!(trade.entry_index, 0);
        assert_eq!(trade.exit_index, 10);
        assert_eq!(trade.bars_held, 10);
        assert_eq!(trade.exit_reason, ExitReason::Signal);
        assert!(trade.pnl > 0.0); // Profitable
        assert!(pm.flat());
    }

    #[test]
    fn position_manager_stop_loss() {
        let mut pm = PositionManager::new("TEST");
        pm.open_long("TEST", 0, "2024-01-01", 100.0, 100.0, 1.0, Some(95.0), None);
        let trade = pm
            .try_close(5, "2024-01-06", 105.0, 94.0, 98.0, 0.05, false)
            .unwrap();
        assert_eq!(trade.exit_reason, ExitReason::StopLoss);
        assert_eq!(trade.exit_price, 95.0);
        assert!(trade.pnl < 0.0); // Loss
    }

    #[test]
    fn position_manager_take_profit() {
        let mut pm = PositionManager::new("TEST");
        pm.open_long(
            "TEST",
            0,
            "2024-01-01",
            100.0,
            100.0,
            1.0,
            None,
            Some(110.0),
        );
        let trade = pm
            .try_close(5, "2024-01-06", 111.0, 95.0, 105.0, 0.05, false)
            .unwrap();
        assert_eq!(trade.exit_reason, ExitReason::TakeProfit);
        assert_eq!(trade.exit_price, 110.0);
        assert!(trade.pnl > 0.0);
    }

    #[test]
    fn position_manager_short() {
        let mut pm = PositionManager::new("TEST");
        pm.open_short(
            "TEST",
            0,
            "2024-01-01",
            100.0,
            100.0,
            1.0,
            Some(105.0),
            None,
        );
        let trade = pm
            .try_close(5, "2024-01-06", 106.0, 90.0, 95.0, 0.05, false)
            .unwrap();
        assert_eq!(trade.exit_reason, ExitReason::StopLoss);
        assert_eq!(trade.side, Side::Short);
    }

    #[test]
    fn trade_journal() {
        let mut journal = TradeJournal::new();
        assert!(journal.is_empty());
        let trade = TradeRecord {
            symbol: "TEST".into(),
            side: Side::Long,
            entry_index: 0,
            exit_index: 10,
            entry_time: "2024-01-01".into(),
            exit_time: "2024-01-11".into(),
            entry_price: 100.0,
            exit_price: 110.0,
            quantity: 100.0,
            pnl: 900.0,
            pnl_pct: 9.0,
            commission: 100.0,
            bars_held: 10,
            exit_reason: ExitReason::Signal,
            r_multiple: None,
        };
        journal.record(trade.clone());
        assert_eq!(journal.len(), 1);
        assert!(journal.trades()[0].winning());
        journal.clear();
        assert!(journal.is_empty());
    }

    #[test]
    fn r_multiple_calculation() {
        let mut pm = PositionManager::new("TEST");
        pm.open_long("TEST", 0, "2024-01-01", 100.0, 100.0, 1.0, Some(95.0), None);
        let trade = pm.close_end(10, "2024-01-11", 110.0, 0.05).unwrap();
        assert!(trade.r_multiple.is_some());
        let r = trade.r_multiple.unwrap();
        // Risk = (100 - 95) * 100 = 500
        // PnL ≈ (110 - 100) * 100 - commissions
        // R ≈ PnL / 500
        assert!(r > 1.0); // Profitable trade with SL
    }
}

#[cfg(test)]
mod position_kernel_tests {
    use super::*;

    fn econ(side: Side, entry: f64, qty: f64, exit: f64, sl: Option<f64>) -> TradeEconomics {
        close_economics(side, entry, qty, 0.0, exit, 0.0, sl)
    }

    #[test]
    fn signal_exit_pays_slippage_against_the_leg() {
        assert_eq!(
            exit_for_bar(Side::Long, None, None, 110.0, 100.0, 105.0, true),
            Some((105.0 - 105.0 * 0.0002, ExitReason::Signal))
        );
        assert_eq!(
            exit_for_bar(Side::Short, None, None, 110.0, 100.0, 105.0, true),
            Some((105.0 + 105.0 * 0.0002, ExitReason::Signal))
        );
    }

    #[test]
    fn stop_loss_wins_over_take_profit() {
        assert_eq!(
            exit_for_bar(
                Side::Long,
                Some(100.0),
                Some(120.0),
                130.0,
                99.0,
                125.0,
                false
            ),
            Some((100.0, ExitReason::StopLoss))
        );
        assert_eq!(
            exit_for_bar(
                Side::Short,
                Some(120.0),
                Some(100.0),
                121.0,
                99.0,
                105.0,
                false
            ),
            Some((120.0, ExitReason::StopLoss))
        );
    }

    #[test]
    fn each_leg_uses_the_bar_extreme_that_hits_it() {
        // Long stops on the low and targets on the high; short mirrors that.
        assert_eq!(
            exit_for_bar(
                Side::Long,
                Some(100.0),
                Some(120.0),
                121.0,
                101.0,
                110.0,
                false
            ),
            Some((120.0, ExitReason::TakeProfit))
        );
        assert_eq!(
            exit_for_bar(
                Side::Short,
                Some(120.0),
                Some(100.0),
                119.0,
                99.0,
                110.0,
                false
            ),
            Some((100.0, ExitReason::TakeProfit))
        );
        assert_eq!(
            exit_for_bar(
                Side::Short,
                Some(120.0),
                Some(100.0),
                121.0,
                101.0,
                110.0,
                false
            ),
            Some((120.0, ExitReason::StopLoss))
        );
        assert_eq!(
            exit_for_bar(
                Side::Long,
                Some(100.0),
                Some(120.0),
                119.0,
                101.0,
                110.0,
                false
            ),
            None
        );
    }

    #[test]
    fn touching_level_is_enough() {
        assert_eq!(
            exit_for_bar(Side::Long, Some(100.0), None, 100.0, 100.0, 100.0, false),
            Some((100.0, ExitReason::StopLoss))
        );
    }

    #[test]
    fn absent_levels_never_trigger() {
        assert_eq!(
            exit_for_bar(Side::Long, None, None, 1_000.0, 0.0, 500.0, false),
            None
        );
    }

    #[test]
    fn trade_maths_follow_the_entry_side() {
        let long = econ(Side::Long, 100.0, 10.0, 110.0, None);
        assert_eq!(long.gross, 100.0);
        assert_eq!(long.pnl, 100.0);
        assert_eq!(long.pnl_pct, 10.0);
        let short = econ(Side::Short, 100.0, 10.0, 90.0, None);
        assert_eq!(short.gross, 100.0);
    }

    #[test]
    fn fees_and_zero_cost_entry_are_pinned() {
        let fees = close_economics(Side::Long, 100.0, 10.0, 2.0, 110.0, 0.03, None);
        assert_eq!(fees.commission, 2.0 + 110.0 * 10.0 * 0.0003);
        assert_eq!(fees.pnl, 100.0 - fees.commission);
        assert_eq!(econ(Side::Long, 0.0, 0.0, 10.0, None).pnl_pct, 0.0);
    }

    #[test]
    fn risk_follows_python_truthiness_of_the_stop() {
        // Python: `if entry.sl_price` — an absent OR zero stop leaves risk None.
        assert_eq!(econ(Side::Long, 100.0, 10.0, 110.0, None).risk, None);
        assert_eq!(econ(Side::Long, 100.0, 10.0, 110.0, Some(0.0)).risk, None);
        assert_eq!(
            econ(Side::Long, 100.0, 10.0, 110.0, Some(90.0)).risk,
            Some(100.0)
        );
    }

    #[test]
    fn r_multiple_needs_a_non_zero_risk() {
        assert_eq!(
            econ(Side::Long, 100.0, 10.0, 110.0, Some(90.0)).r_multiple,
            Some(1.0)
        );
        assert_eq!(
            econ(Side::Long, 100.0, 10.0, 110.0, Some(100.0)).r_multiple,
            None
        );
    }

    #[test]
    fn manager_and_kernel_agree_on_every_path() {
        let mut direct = PositionManager::new("X");
        direct
            .open_long("X", 0, "t0", 100.0, 10.0, 2.0, Some(90.0), Some(120.0))
            .then_some(())
            .expect("open_long always succeeds when flat");
        let trade = direct
            .try_close(3, "t3", 121.0, 95.0, 110.0, 0.03, false)
            .expect("take-profit bar must close");
        let economics = close_economics(Side::Long, 100.0, 10.0, 2.0, 120.0, 0.03, Some(90.0));
        assert_eq!(trade.exit_price, 120.0);
        assert_eq!(trade.pnl, economics.pnl);
        assert_eq!(trade.pnl_pct, economics.pnl_pct);
        assert_eq!(trade.commission, economics.commission);
        assert_eq!(trade.r_multiple, economics.r_multiple);
        assert_eq!(trade.bars_held, 3);
        assert_eq!(trade.exit_reason, ExitReason::TakeProfit);
    }
}
