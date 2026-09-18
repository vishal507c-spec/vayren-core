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
        let (exit_price, exit_reason) = if exit_signal {
            let slip = bar_close * 0.0002;
            let price = match entry.side {
                Side::Long => bar_close - slip,
                Side::Short => bar_close + slip,
            };
            (price, ExitReason::Signal)
        } else {
            match entry.side {
                Side::Long => {
                    if let Some(sl) = entry.sl_price {
                        if bar_low <= sl {
                            return Some(self.close_position(
                                exit_index,
                                exit_time,
                                sl,
                                commission_pct,
                                ExitReason::StopLoss,
                            ));
                        }
                    }
                    if let Some(tp) = entry.tp_price {
                        if bar_high >= tp {
                            return Some(self.close_position(
                                exit_index,
                                exit_time,
                                tp,
                                commission_pct,
                                ExitReason::TakeProfit,
                            ));
                        }
                    }
                    return None;
                }
                Side::Short => {
                    if let Some(sl) = entry.sl_price {
                        if bar_high >= sl {
                            return Some(self.close_position(
                                exit_index,
                                exit_time,
                                sl,
                                commission_pct,
                                ExitReason::StopLoss,
                            ));
                        }
                    }
                    if let Some(tp) = entry.tp_price {
                        if bar_low <= tp {
                            return Some(self.close_position(
                                exit_index,
                                exit_time,
                                tp,
                                commission_pct,
                                ExitReason::TakeProfit,
                            ));
                        }
                    }
                    return None;
                }
            }
        };
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
        let commission_exit = exit_price * entry.quantity * (commission_pct / 100.0);
        let commission = entry.commission_entry + commission_exit;
        let gross = match entry.side {
            Side::Long => (exit_price - entry.entry_price) * entry.quantity,
            Side::Short => (entry.entry_price - exit_price) * entry.quantity,
        };
        let pnl = gross - commission;
        let entry_cost = entry.entry_price * entry.quantity;
        let pnl_pct = if entry_cost != 0.0 {
            (pnl / entry_cost) * 100.0
        } else {
            0.0
        };
        let risk = entry
            .sl_price
            .map(|sl| (entry.entry_price - sl).abs() * entry.quantity);
        let r_multiple = risk.and_then(|r| if r != 0.0 { Some(pnl / r) } else { None });
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
            pnl,
            pnl_pct,
            commission,
            bars_held: exit_index - entry.entry_index,
            exit_reason,
            r_multiple,
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
