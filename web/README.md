# VAYREN Web — TypeScript/React Canonical UI

> **Permanent rule:** All new VAYREN UI development uses **TypeScript / React**. Python PySide6 UI remains functional and is gradually migrated section-by-section.

## Stack
- TypeScript 5.5 + React 18
- Vite 5 + @vitejs/plugin-react
- No new Python UI — new UI is React

## Gradual Migration
```
Existing Python UI (PySide6)
  ↓ understand
Rebuild section in React
  ↓ connect to existing backend (market data, chart engine, strategy VM, backtest)
Next section
```
Backend (VM, compiler, IR, strategy storage, research, data) stays independent and unchanged.

## Current Migrated Section — Market Top Bar
`src/components/MarketTopBar.tsx` is the first React section:

**Layout (single horizontal line, TradingView-inspired, minimal premium):**
```
5m 15m 30m 45m 1h 2h 4h [DOWN-ARROW] INDICATORS
```
- Visible: `5m,15m,30m,45m,1h,2h,4h` (if present in `timeframes` from backend)
- Overflow: `1D,1W` (and any others) inside small `▾` dropdown — no large dropdown
- No `|` separators, no dividers, no second row, no wrapping, no overlap
- Flexbox (`display: flex; justify-content: space-between`), `white-space: nowrap`, `overflow: hidden`
- Active timeframe highlighted with VAYREN teal `#26a69a` (existing palette)
- `INDICATORS` single control after timeframes, after dropdown

**Props:**
```tsx
<MarketTopBar
  timeframes={["5m","15m","30m","45m","1h","2h","4h","1D","1W"]}
  activeTimeframe="15m"
  onTimeframeSelect={(tf) => /* → existing backend: TimeframeChanged / chart engine */}
  onIndicatorsClick={() => {}}
  onStrategySelect={(name) => {}}
/>
```

**Styling:** `src/styles/MarketTopBar.css` + `global.css` — VAYREN institutional palette (`#101418`, `#26a69a`), compact buttons `4px 8px`, `11px/500`, `4px radius`, subtle hover `midlight`, no shadows/cards/decorations.

**Responsive:** `flex: 1 1 auto` left, `flex: 0 0 auto` right, `gap: 4px`, media query at 640px, no horizontal scrollbar.

## Connect to Backend
The React top bar calls `onTimeframeSelect` which should publish the existing `TimeframeChanged` (or call the existing Python `ChartWindow._on_timeframe_selected` via a bridge). No backend logic is duplicated.

```ts
// Example bridge in the host (Python QWebChannel or HTTP):
onTimeframeSelect={(tf) => fetch(`/api/timeframe?tf=${tf}`)}
```

## Dev
```bash
cd web
npm install
npm run dev      # http://localhost:5173
npm run build
npm run typecheck
```

## Files
- `src/components/MarketTopBar.tsx` — canonical top bar (this task)
- `src/App.tsx` — minimal shell demonstrating the bar
- `vite.config.ts` — proxy `/api` to Python backend
- `tsconfig.json` — strict
