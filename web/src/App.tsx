import { useState, useEffect } from "react";
import { MarketTopBar } from "./components/MarketTopBar";
import "./styles/MarketTopBar.css";

/**
 * Minimal app shell for gradual migration.
 * The MarketTopBar is the first TypeScript/React section rebuilt from the
 * existing Python PySide6 UI. It communicates with the existing VAYREN backend
 * (market data, chart engine, strategy VM) via the same contracts — no Python
 * backend changes.
 */
export default function App() {
  const [symbol] = useState("360ONE");
  const [timeframe, setTimeframe] = useState("15m");
  // In the real app, timeframes come from the backend: SymbolRepository.detect_timeframes(symbol)
  // For the migrated top bar we keep the existing set and let the backend filter.
  const [availableTimeframes] = useState<string[]>(["15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"]);

  // Example: connect to existing backend (placeholder fetch)
  useEffect(() => {
    // TODO: wire to existing Python backend via EventBus or HTTP
    // e.g. fetch(`/api/chart?symbol=${symbol}&timeframe=${timeframe}`)
  }, [symbol, timeframe]);

  return (
    <div className="vayren-app">
      <MarketTopBar
        timeframes={availableTimeframes}
        activeTimeframe={timeframe}
        onTimeframeSelect={setTimeframe}
        onIndicatorsClick={() => {
          // Indicators panel is owned by MarketTopBar; this is for future chart integration
        }}
        onStrategySelect={(name) => {
          // Existing strategy system: strategy/language/storage → compiler → IR → VM
          // This will publish RunBacktest via the existing backend
          console.log("Strategy selected via Indicators:", name);
        }}
      />
      <div className="vayren-chart-placeholder">
        Chart canvas (existing Python CandleChartWidget / future React canvas) for {symbol} {timeframe}
      </div>
    </div>
  );
}
