import { useState, useMemo, useRef, useEffect } from "react";

type MarketTopBarProps = {
  /** All timeframes detected from the backend (e.g. from SymbolRepository). */
  timeframes: string[];
  /** Currently active timeframe (e.g. "15m"). */
  activeTimeframe: string;
  /** Called when a timeframe is selected. Must update the chart via existing backend. */
  onTimeframeSelect: (timeframe: string) => void;
  /** Called when INDICATORS is clicked (opens the single Indicators panel). */
  onIndicatorsClick?: () => void;
  /** Called when a strategy inside INDICATORS → STRATEGIES is selected. */
  onStrategySelect?: (name: string) => void;
  /** Strategy names from the existing storage (D:\VAYREN_STRATEGIES). Injected by app. */
  strategynames?: string[];
};

/**
 * VAYREN Market Top Bar — TypeScript/React canonical.
 *
 * Single horizontal line, TradingView-inspired, minimal premium:
 * 15m 30m 45m 1h 2h 4h ▾ INDICATORS
 *
 * - Visible: 15m,30m,45m,1h,2h,4h (if present in `timeframes` prop)
 * - Overflow: 1D,1W (and any other) inside ▾ dropdown
 * - No vertical separators, no "|" dividers, no second row, no wrapping, no overlap
 * - Flexbox, responsive, no horizontal scrollbar
 * - Active timeframe clearly highlighted via existing VAYREN palette (teal)
 *
 * Communicates with existing Python backend (market data, chart engine) via
 * the `onTimeframeSelect` callback — no backend changes.
 */
const VISIBLE_ORDER = ["5m", "15m", "30m", "45m", "1h", "2h", "4h"] as const;

export function MarketTopBar({
  timeframes,
  activeTimeframe,
  onTimeframeSelect,
  onIndicatorsClick,
}: MarketTopBarProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const { visible, overflow } = useMemo(() => {
    const available = new Set(timeframes);
    const vis = VISIBLE_ORDER.filter((tf) => available.has(tf));
    const over = timeframes.filter((tf) => !vis.includes(tf as typeof VISIBLE_ORDER[number]));
    return { visible: vis, overflow: over };
  }, [timeframes]);

  const isOverflowActive = overflow.includes(activeTimeframe);

  // Close dropdown on outside click / Esc
  useEffect(() => {
    if (!dropdownOpen) return;
    const onDown = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDropdownOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [dropdownOpen]);

  return (
    <div className="market-topbar" role="toolbar" aria-label="Market timeframe and indicators">
      <div className="market-topbar__left">
        {visible.map((tf) => (
          <button
            key={tf}
            className={`market-topbar__tf ${activeTimeframe === tf ? "is-active" : ""}`}
            aria-pressed={activeTimeframe === tf}
            onClick={() => onTimeframeSelect(tf)}
          >
            {tf}
          </button>
        ))}

        {/* Small dropdown arrow for overflow timeframes (1D, 1W, ...) */}
        <div className="market-topbar__dropdown" ref={dropdownRef}>
          <button
            className={`market-topbar__tf market-topbar__tf--dropdown ${isOverflowActive ? "is-active" : ""}`}
            aria-haspopup="menu"
            aria-expanded={dropdownOpen}
            aria-label="More timeframes"
            onClick={() => setDropdownOpen((v) => !v)}
          >
            ▼
          </button>
          {dropdownOpen && overflow.length > 0 && (
            <div className="market-topbar__menu" role="menu">
              {overflow.map((tf) => (
                <button
                  key={tf}
                  role="menuitem"
                  className={`market-topbar__menu-item ${activeTimeframe === tf ? "is-active" : ""}`}
                  onClick={() => {
                    onTimeframeSelect(tf);
                    setDropdownOpen(false);
                  }}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}
          {dropdownOpen && overflow.length === 0 && (
            <div className="market-topbar__menu market-topbar__menu--empty" role="menu">
              <span className="market-topbar__menu-empty">No more timeframes</span>
            </div>
          )}
        </div>
      </div>

      <div className="market-topbar__right">
        <button
          className="market-topbar__indicators"
          onClick={() => onIndicatorsClick?.()}
          aria-haspopup="dialog"
        >
          INDICATORS
        </button>
      </div>
    </div>
  );
}
