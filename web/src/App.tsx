import { useState } from "react";
import { MarketTopBar } from "./components/MarketTopBar";
import "./styles/MarketTopBar.css";

/**
 * Minimal app shell for gradual migration.
 * The MarketTopBar is the first TypeScript/React section rebuilt from the
 * existing Python PySide6 UI.
 */
export default function App() {
  const [timeframe, setTimeframe] = useState("15m");
  // In the real app, timeframes come from the backend: SymbolRepository.detect_timeframes(symbol)
  // For the migrated top bar we keep the existing set and let the backend filter.
  const [availableTimeframes] = useState<string[]>(["15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"]);

  return (
    <div className="vayren-app">
      <MarketTopBar
        timeframes={availableTimeframes}
        activeTimeframe={timeframe}
        onTimeframeSelect={setTimeframe}
      />
    </div>
  );
}
